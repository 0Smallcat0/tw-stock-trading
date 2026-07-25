"""Ingest one US-listed ETF's full daily history, gated by two providers.

Series of record is Yahoo's consolidated chart feed; FinMind's USStockPrice
dataset is the independent confirmer. Nothing is written unless every gate
below passes, because a silently-wrong price series produces a
confident-looking backtest and there is no way to tell from the result.

Gates (fixed here, quoted in SLEEVE3_GOLD_PREREGISTRATION.md, not tunable
per run):

* at least 1,000 closed daily bars (the six-gate data floor)
* at least 99% of the primary's sessions confirmed by the secondary inside
  the range both cover
* no confirmed close differing by more than 0.5%
* no session-to-session gap longer than 10 calendar days (the longest US
  equity closure since GLD listed was Hurricane Sandy's two sessions)
* no distribution: the raw close must equal the adjusted close on every bar,
  otherwise the raw series has stopped being the total-return series and the
  sleeve would silently understate its own return

Usage:
    python -m scripts.ingest_us_etf_ohlcv
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from src.config import load_config
from src.data import (
    MarketDataValidationError,
    candle_file_name,
    cross_check_sources,
    fetch_finmind_us_daily,
    fetch_yahoo_daily,
    missing_sessions,
    write_candles_jsonl,
)

DEFAULT_TICKER = "GLD"
_HISTORY_START = date(2004, 1, 1)
_MIN_CANDLES = 1000
_MIN_AGREEMENT_RATIO = Decimal("0.99")
_MAX_CLOSE_GAP = Decimal("0.005")
_MAX_SESSION_GAP = timedelta(days=10)
_REPORTABLE_SESSION_GAP = timedelta(days=5)
_MAX_STALENESS = timedelta(days=10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/runtime/paper_runtime.yaml")
    parser.add_argument("--ticker", default=DEFAULT_TICKER)
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    output_dir = Path(args.output_dir or config.storage.candle_files_directory)
    observed_at = datetime.now(UTC)

    primary = fetch_yahoo_daily(args.ticker, observed_at=observed_at)
    secondary = fetch_finmind_us_daily(args.ticker, observed_at=observed_at, start=_HISTORY_START)
    report = cross_check_sources(primary, secondary, tolerance=_MAX_CLOSE_GAP)
    long_gaps = missing_sessions(primary, max_gap=_REPORTABLE_SESSION_GAP)
    blocking_gaps = missing_sessions(primary, max_gap=_MAX_SESSION_GAP)
    last_session = max(primary.trading_dates)
    staleness = observed_at.date() - last_session
    distributions = primary.distribution_dates + secondary.distribution_dates

    failures = _gate_failures(
        primary_count=len(primary.candles),
        agreement_ratio=report.agreement_ratio,
        mismatches=len(report.mismatch_dates),
        blocking_gaps=blocking_gaps,
        distributions=distributions,
        staleness=staleness,
    )

    written: str | None = None
    if not failures:
        written = str(
            write_candles_jsonl(primary.candles, output_dir / candle_file_name(args.ticker, "1d"))
        )

    print(
        json.dumps(
            {
                "ticker": args.ticker,
                "primary_source": primary.source,
                "secondary_source": secondary.source,
                "primary_candles": len(primary.candles),
                "secondary_candles": len(secondary.candles),
                "primary_incomplete_rows": [d.isoformat() for d in primary.incomplete_dates],
                "secondary_incomplete_rows": [d.isoformat() for d in secondary.incomplete_dates],
                "cross_source": {
                    "common_start": report.common_start.isoformat(),
                    "common_end": report.common_end.isoformat(),
                    "shared_days": report.shared_days,
                    "agreement_ratio": f"{report.agreement_ratio:.6f}",
                    "primary_only": [d.isoformat() for d in report.primary_only],
                    "secondary_only": [d.isoformat() for d in report.secondary_only],
                    "worst_relative_close_gap": f"{report.worst_relative_gap:.8f}",
                    "worst_gap_date": (
                        report.worst_gap_date.isoformat() if report.worst_gap_date else None
                    ),
                    "closes_over_tolerance": [d.isoformat() for d in report.mismatch_dates],
                },
                "first_session": min(primary.trading_dates).isoformat(),
                "last_session": last_session.isoformat(),
                "staleness_days": staleness.days,
                "sessions_before_long_gap": [d.isoformat() for d in long_gaps],
                "distribution_dates": [d.isoformat() for d in distributions],
                "gate_failures": failures,
                "written_file": written,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if failures:
        raise SystemExit(1)


def _gate_failures(
    *,
    primary_count: int,
    agreement_ratio: Decimal,
    mismatches: int,
    blocking_gaps: tuple[date, ...],
    distributions: tuple[date, ...],
    staleness: timedelta,
) -> list[str]:
    failures: list[str] = []
    if primary_count < _MIN_CANDLES:
        failures.append(f"only {primary_count} candles, floor is {_MIN_CANDLES}")
    if agreement_ratio < _MIN_AGREEMENT_RATIO:
        failures.append(
            f"cross-source agreement {agreement_ratio:.4f} below {_MIN_AGREEMENT_RATIO}"
        )
    if mismatches:
        failures.append(f"{mismatches} sessions where the closes differ by over {_MAX_CLOSE_GAP}")
    if blocking_gaps:
        failures.append(f"{len(blocking_gaps)} gaps longer than {_MAX_SESSION_GAP.days} days")
    if distributions:
        failures.append(
            f"{len(distributions)} bars where adjusted close != raw close; "
            "the raw series is no longer the total-return series"
        )
    if staleness > _MAX_STALENESS:
        failures.append(
            f"last session is {staleness.days} days old, limit is {_MAX_STALENESS.days}"
        )
    return failures


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, MarketDataValidationError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from None
