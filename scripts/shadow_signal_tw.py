"""Forward-only shadow track for the 0050 sleeve of the cross-market combination.

Computes the mid-channel Donchian 10/20/55/110 signal — the same untuned
rule the crypto program runs — on the local adjusted 0050 series and
appends one row per newly closed session to an append-only JSONL.

Boundaries:
- Reads local candle files only. It never fetches, so a stale file is a
  refusal rather than a silently repeated signal: TWSE's WAF makes daily
  ingestion impractical, so the refresh cadence is weekly and the record
  says so.
- Places no orders, emits no instruction, and touches no live contract.
- Records only from its own first day forward, so every number it ever
  reports is out of sample.

Usage:
    python -m scripts.shadow_signal_tw            # append if data is fresh
    python -m scripts.shadow_signal_tw --summary  # print the track
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import load_config
from src.data import adjusted_candle_file_name, read_candles_jsonl
from src.domain import Candle
from src.strategies import evaluate_donchian_ensemble

SYMBOL = "0050"
WINDOWS = (10, 20, 55, 110)
EXIT_MODE = "mid_channel"
TRACK_PATH = Path("data/runtime/shadow_tw0050.jsonl")
MAX_STALENESS_DAYS = 10  # a weekly refresh plus a long holiday


def load_track() -> list[dict[str, Any]]:
    if not TRACK_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in TRACK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def decide(candles: tuple[Candle, ...]) -> tuple[Decimal, tuple[str, ...]]:
    """Replay the state machine to the latest close — a pure function of price."""

    closed = tuple(candle for candle in candles if candle.is_closed)
    states: tuple[bool, ...] | None = None
    fraction = Decimal("0")
    codes: tuple[str, ...] = ()
    for index in range(max(WINDOWS), len(closed)):
        fraction, codes, states = evaluate_donchian_ensemble(
            closed, index, windows=WINDOWS, exit_mode=EXIT_MODE, previous_states=states
        )
    return fraction, codes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/runtime/paper_runtime.yaml")
    parser.add_argument("--summary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_track()
    if args.summary:
        if not rows:
            print("tw shadow track is empty")
            return
        exposures = [float(row["exposure"]) for row in rows]
        print(f"sessions recorded: {len(rows)}")
        print(f"first / last     : {rows[0]['date']} / {rows[-1]['date']}")
        print(f"notional equity  : {rows[-1]['equity']} (start 1000)")
        print(
            f"mean exposure    : {sum(exposures) / len(exposures):.3f}, latest {exposures[-1]:.2f}"
        )
        return

    config = load_config(Path(args.config))
    candles = read_candles_jsonl(
        Path(config.storage.candle_files_directory)
        / adjusted_candle_file_name(SYMBOL, config.data_source.timeframe)
    )
    closed = tuple(candle for candle in candles if candle.is_closed)
    latest = closed[-1]
    age = datetime.now(UTC) - latest.close_time
    if age > timedelta(days=MAX_STALENESS_DAYS):
        raise SystemExit(
            f"0050 candles end {latest.close_time.date().isoformat()} "
            f"({age.days}d old, limit {MAX_STALENESS_DAYS}d); "
            "run scripts.ingest_public_ohlcv before recording a signal"
        )

    session = latest.open_time.date().isoformat()
    if rows and str(rows[-1]["date"]) >= session:
        print(f"already recorded through {rows[-1]['date']}")
        return

    fraction, codes = decide(candles)
    equity = Decimal(str(rows[-1]["equity"])) if rows else Decimal("1000")
    if rows:
        prior_close = Decimal(str(rows[-1]["close"]))
        held = Decimal(str(rows[-1]["exposure"]))
        if prior_close > 0:
            equity *= Decimal("1") + held * (latest.close_price / prior_close - Decimal("1"))

    row = {
        "date": session,
        "recorded_at": datetime.now(UTC).isoformat(),
        "symbol": SYMBOL,
        "strategy": "donchian_breakout_ensemble",
        "config": {"windows": list(WINDOWS), "exit": EXIT_MODE, "source_trial": 23},
        "exposure": str(fraction),
        "close": str(latest.close_price),
        "reason_codes": list(codes),
        "equity": str(equity),
    }
    TRACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"appended {session} exposure={fraction} close={latest.close_price}")


if __name__ == "__main__":
    main()
