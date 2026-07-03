"""Thin CLI for the daily signal runtime (Goal L replay smoke + Goal O live cycle)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from src.config import AppConfig, load_config
from src.data import (
    BinanceSpotPublicClient,
    MarketDataError,
    MarketDataValidationError,
    candle_file_name,
    first_passing_public_rest_base_url,
    read_candles_jsonl,
    run_public_rest_smoke,
    symbol_from_binance_native,
)
from src.domain import Candle, Timeframe
from src.notify import (
    CollectingNotificationChannel,
    NotificationChannel,
    NotificationValidationError,
    WebhookNotificationChannel,
)
from src.runtime import (
    JsonlEventStore,
    RuntimeEngineError,
    RuntimeParameters,
    RuntimeStoreError,
    SignalRuntime,
    run_replay,
)

# 200-close warmup plus margin; Binance caps one request at 1000 anyway.
_LIVE_FETCH_LIMIT = 210
# A lock older than this is treated as a crash leftover, not a live run.
_LOCK_STALE_SECONDS = 2 * 60 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/runtime/paper_runtime.yaml",
        help="Path to the Core MVP runtime config.",
    )
    parser.add_argument(
        "--candles-dir",
        default=None,
        help="Directory with <SYMBOL>_1d.jsonl candle files (default: storage config).",
    )
    parser.add_argument(
        "--store",
        default=None,
        help="Runtime event store path (default: storage config).",
    )
    parser.add_argument(
        "--replay-smoke",
        action="store_true",
        help="Replay the recorded candle files through the runtime and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one LIVE daily cycle: fetch the latest closed daily candles from "
            "Binance public REST, process them, persist notifications, and exit. "
            "Idempotent: re-running on the same day is a no-op."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.replay_smoke == args.once:
        print(
            "choose exactly one mode: --replay-smoke (recorded candles) "
            "or --once (live daily cycle)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    config = load_config(Path(args.config))
    store_path = Path(args.store or config.storage.runtime_events_path)
    live_store_path = Path(config.storage.runtime_events_path)
    if args.replay_smoke and store_path.resolve() == live_store_path.resolve():
        # A replay against the live store would poison the gate's paper-day
        # counter with historical cycles. Replays must name their own store.
        print(
            "refusing: --replay-smoke must not target the live runtime store "
            f"({live_store_path}); pass an explicit --store path",
            file=sys.stderr,
        )
        raise SystemExit(2)

    channel = _channel_from_config(config.notifications.channel, config.notifications.webhook_url)
    runtime = SignalRuntime(
        parameters=_runtime_parameters(config),
        store=JsonlEventStore(store_path, durable_fsync=args.once),
        channel=channel,
    )

    if args.once:
        with _single_instance_lock(store_path):
            _run_live_cycle(config, runtime, store_path)
        return

    candles_dir = Path(args.candles_dir or config.storage.candle_files_directory)
    candles_by_symbol = {}
    for symbol_value in sorted(config.portfolio.risk_budgets):
        file_path = candles_dir / candle_file_name(symbol_value, config.data_source.timeframe)
        candles_by_symbol[symbol_value] = read_candles_jsonl(file_path)
    summary = run_replay(candles_by_symbol, runtime)

    print(
        json.dumps(
            {
                "cycles_processed": summary.cycles_processed,
                "cycles_skipped": summary.cycles_skipped,
                "notifications": summary.notifications,
                "fills": summary.fills,
                "rejections": summary.rejections,
                "final_equity": str(summary.final_equity) if summary.final_equity else None,
                "store_path": str(store_path),
                "last_processed": (
                    runtime.last_processed.isoformat() if runtime.last_processed else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_live_cycle(config: AppConfig, runtime: SignalRuntime, store_path: Path) -> None:
    observed_at = datetime.now(UTC)
    candles_by_symbol = asyncio.run(_fetch_latest_candles(config, observed_at))
    result = runtime.process_closed_candles(candles_by_symbol, observed_at=observed_at)

    print(
        json.dumps(
            {
                "mode": "live_once",
                "observed_at": observed_at.isoformat(),
                "processed": result.processed,
                "reason": result.reason,
                "close_time": result.close_time.isoformat() if result.close_time else None,
                "equity": str(result.equity) if result.equity is not None else None,
                "health_codes": list(result.health_codes),
                "notifications": [event.to_json_dict() for event in result.notifications],
                "fills": [
                    {
                        "symbol": fill.symbol.value,
                        "side": fill.side.value,
                        "quantity": str(fill.quantity),
                        "price": str(fill.price),
                        "fee": str(fill.fee),
                    }
                    for fill in result.fills
                ],
                "rejections": [
                    {"symbol": symbol, "reason_codes": list(codes)}
                    for symbol, codes in result.rejection_reason_codes
                ],
                "store_path": str(store_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


async def _fetch_latest_candles(
    config: AppConfig, observed_at: datetime
) -> dict[str, tuple[Candle, ...]]:
    timeout_seconds = float(config.data_source.timeout_seconds)
    preflight_results = run_public_rest_smoke(
        config.data_source.rest_base_url_candidates,
        timeout_seconds=timeout_seconds,
    )
    rest_base_url = first_passing_public_rest_base_url(preflight_results)
    if rest_base_url is None:
        msg = "no public Binance REST base URL is reachable from this environment"
        raise MarketDataError(msg)

    timeframe = Timeframe(config.data_source.timeframe)
    candles_by_symbol: dict[str, tuple[Candle, ...]] = {}
    async with BinanceSpotPublicClient(
        rest_base_url=rest_base_url,
        timeout_seconds=timeout_seconds,
    ) as client:
        for symbol_value in sorted(config.portfolio.risk_budgets):
            candles_by_symbol[symbol_value] = await client.fetch_historical_candles(
                symbol=symbol_from_binance_native(symbol_value),
                timeframe=timeframe,
                limit=_LIVE_FETCH_LIMIT,
                received_at=observed_at,
                closed_only=True,
            )
    return candles_by_symbol


def _runtime_parameters(config: AppConfig) -> RuntimeParameters:
    return RuntimeParameters(
        risk_budgets=config.portfolio.risk_budgets,
        initial_cash=config.account.initial_cash,
        account_id=config.account.account_id,
        fee_bps=config.execution.fee_bps,
        slippage_bps=config.execution.slippage_bps,
        quantity_step=config.execution.quantity_step,
        price_tick=config.execution.price_tick,
        min_notional_usdt=config.risk.min_notional_usdt,
        max_drawdown_fraction=config.risk.max_drawdown_fraction,
        daily_loss_pause_fraction=config.risk.daily_loss_pause_fraction,
        disaster_single_day_drop_fraction=config.risk.disaster_single_day_drop_fraction,
        stale_data_max_age_seconds=config.risk.stale_data_max_age_seconds,
        idempotency_namespace=config.runtime.idempotency_key_namespace,
    )


def _channel_from_config(channel: str, webhook_url: str) -> NotificationChannel:
    if channel == "webhook":
        return WebhookNotificationChannel(webhook_url)
    return CollectingNotificationChannel()


@contextmanager
def _single_instance_lock(store_path: Path) -> Iterator[None]:
    """Prevent overlapping live cycles (scheduled task + manual run)."""

    lock_path = store_path.with_suffix(store_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age_seconds = time.time() - lock_path.stat().st_mtime
        if age_seconds < _LOCK_STALE_SECONDS:
            print(
                f"another live cycle appears to be running (lock {lock_path}, "
                f"age {int(age_seconds)}s); exiting without side effects",
                file=sys.stderr,
            )
            raise SystemExit(0) from None
        # A crash left a stale lock behind; replace it and continue.
        lock_path.unlink()
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeEngineError,
        RuntimeStoreError,
        MarketDataError,
        MarketDataValidationError,
        NotificationValidationError,
        httpx.HTTPError,
    ) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
