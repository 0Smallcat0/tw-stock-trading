"""Thin CLI for public Binance Spot OHLCV smoke ingestion."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from src.config import load_config
from src.data import (
    BinanceSpotPublicClient,
    MarketDataError,
    PublicDataSmokeResult,
    PublicDataSmokeStatus,
    build_universe_eligibility_metrics,
    build_universe_snapshot,
    candle_file_name,
    first_passing_public_rest_base_url,
    inspect_candle_quality,
    run_public_rest_smoke,
    symbol_from_binance_native,
    write_candles_jsonl,
)
from src.domain import Timeframe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/runtime/paper_runtime.yaml",
        help="Path to the Core MVP runtime config.",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Binance-native symbol to fetch. Defaults to config data_source.symbols.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Number of klines per symbol.")
    parser.add_argument(
        "--start",
        default=None,
        help="ISO date (UTC) to fetch full history from, paginated (e.g. 2017-08-01).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Write fetched candles as <SYMBOL>_<TF>.jsonl files into this directory.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    symbols = tuple(args.symbols or config.data_source.symbols)
    timeframe = Timeframe(config.data_source.timeframe)
    observed_at = datetime.now(UTC)
    timeout_seconds = float(config.data_source.timeout_seconds)
    preflight_results = run_public_rest_smoke(
        config.data_source.rest_base_url_candidates,
        timeout_seconds=timeout_seconds,
    )
    rest_base_url = first_passing_public_rest_base_url(preflight_results)
    if rest_base_url is None:
        print(
            json.dumps(
                {
                    "status": _blocked_smoke_status(preflight_results),
                    "classification": "ENVIRONMENT_BLOCKER",
                    "baseline_impact": "none",
                    "release_certification_impact": "cannot_start",
                    "live_public_smoke": [result.as_dict() for result in preflight_results],
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(2)

    start_time: datetime | None = None
    if args.start:
        start_time = datetime.fromisoformat(args.start)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)

    written_files: dict[str, str] = {}
    async with BinanceSpotPublicClient(
        rest_base_url=rest_base_url,
        timeout_seconds=timeout_seconds,
    ) as client:
        symbol_filters = await client.fetch_symbol_filters(symbols)
        candle_list = []
        for symbol_value in symbols:
            symbol = symbol_from_binance_native(symbol_value)
            if start_time is not None:
                symbol_candles = await client.fetch_historical_candles_range(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=start_time,
                    received_at=observed_at,
                )
            else:
                symbol_candles = await client.fetch_historical_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=args.limit,
                    received_at=observed_at,
                )
            if args.output_dir and symbol_candles:
                output_path = Path(args.output_dir) / candle_file_name(
                    symbol_value, timeframe.value
                )
                write_candles_jsonl(symbol_candles, output_path)
                written_files[symbol_value] = str(output_path)
            candle_list.extend(symbol_candles)
        candles = tuple(candle_list)

    report = inspect_candle_quality(
        candles,
        timeframe=timeframe,
        observed_at=observed_at,
        stale_after=timedelta(seconds=config.risk.stale_data_max_age_seconds),
    )
    # Universe eligibility screening is a 15m-candle contract; daily ingestion
    # only reports per-symbol counts and quality issues.
    universe_symbols: list[str] = []
    if timeframe.value == "15m":
        universe_metrics = build_universe_eligibility_metrics(candles)
        universe = build_universe_snapshot(
            symbol_filters,
            metrics=universe_metrics,
            created_at=observed_at,
        )
        universe_symbols = [symbol.value for symbol in universe.symbols]

    print(
        json.dumps(
            {
                "rest_base_url": rest_base_url,
                "source": "binance_spot_public",
                "symbols_requested": symbols,
                "symbol_filters": len(symbol_filters),
                "universe_symbols": universe_symbols,
                "candles": len(candles),
                "written_files": written_files,
                "live_public_smoke": [result.as_dict() for result in preflight_results],
                "quality_issues": sorted({issue.code.value for issue in report.issues}),
            },
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except (httpx.HTTPError, MarketDataError) as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


def _blocked_smoke_status(preflight_results: tuple[PublicDataSmokeResult, ...]) -> str:
    if all(
        getattr(result, "status", None) is PublicDataSmokeStatus.DNS_BLOCKED
        for result in preflight_results
    ):
        return "DNS_BLOCKED_BY_ENVIRONMENT"
    return "BLOCKED_BY_ENVIRONMENT"


if __name__ == "__main__":
    main()
