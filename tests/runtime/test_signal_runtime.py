from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.domain import Candle, Symbol, Timeframe
from src.notify import CollectingNotificationChannel
from src.runtime import (
    STALE_DATA_HALT,
    WARMUP_INSUFFICIENT_HISTORY,
    JsonlEventStore,
    RuntimeEngineError,
    RuntimeParameters,
    SignalRuntime,
    run_replay,
)

_BASE_OPEN_TIME = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_WARMUP = 200


def _symbol(value: str, base_asset: str) -> Symbol:
    return Symbol(value=value, base_asset=base_asset, quote_asset="USDT")


def _daily_candle(symbol: Symbol, index: int, close: Decimal, *, is_closed: bool = True) -> Candle:
    open_time = _BASE_OPEN_TIME + timedelta(days=index)
    return Candle(
        symbol=symbol,
        timeframe=Timeframe("1d"),
        open_time=open_time,
        close_time=open_time + timedelta(days=1) - timedelta(milliseconds=1),
        open_price=close,
        high_price=close + Decimal("1"),
        low_price=max(close - Decimal("1"), Decimal("0.01")),
        close_price=close,
        volume=Decimal("1000"),
        is_closed=is_closed,
    )


def _series(scale: Decimal) -> tuple[Decimal, ...]:
    """200 flat days, 5 rising trend days, one crash day, 4 flat days."""

    prices = [Decimal("100") * scale] * _WARMUP
    prices.extend([(Decimal("200") + Decimal(index)) * scale for index in range(5)])
    prices.append(Decimal("100") * scale)
    prices.extend([Decimal("100") * scale] * 4)
    return tuple(prices)


def _universe(days: int | None = None) -> dict[str, tuple[Candle, ...]]:
    btc_prices = _series(Decimal("1"))
    eth_prices = _series(Decimal("0.1"))
    if days is not None:
        btc_prices = btc_prices[:days]
        eth_prices = eth_prices[:days]
    return {
        "BTCUSDT": tuple(
            _daily_candle(_symbol("BTCUSDT", "BTC"), index, price)
            for index, price in enumerate(btc_prices)
        ),
        "ETHUSDT": tuple(
            _daily_candle(_symbol("ETHUSDT", "ETH"), index, price)
            for index, price in enumerate(eth_prices)
        ),
    }


def _parameters() -> RuntimeParameters:
    return RuntimeParameters(
        risk_budgets={"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal("0.5")},
        initial_cash=Decimal("1000"),
        account_id="paper-main",
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        quantity_step=Decimal("0.000001"),
        price_tick=Decimal("0.01"),
        min_notional_usdt=Decimal("10"),
        max_drawdown_fraction=Decimal("0.20"),
        daily_loss_pause_fraction=Decimal("0.05"),
        disaster_single_day_drop_fraction=Decimal("0.20"),
        stale_data_max_age_seconds=129600,
        idempotency_namespace="paper-runtime",
    )


def _runtime(store_path: Path) -> tuple[SignalRuntime, CollectingNotificationChannel]:
    channel = CollectingNotificationChannel()
    runtime = SignalRuntime(
        parameters=_parameters(),
        store=JsonlEventStore(store_path),
        channel=channel,
    )
    return runtime, channel


def test_replay_notifies_ladder_changes_and_fills_the_scoreboard(tmp_path: Path) -> None:
    runtime, channel = _runtime(tmp_path / "events.jsonl")

    summary = run_replay(_universe(), runtime)

    # 0->1 on the first trend close and 1->0 after the crash, per symbol.
    assert summary.notifications == 4
    assert summary.fills == 4
    # 210 days of history yield decision days at indexes 199..209.
    assert summary.cycles_processed == 11
    assert summary.final_equity is not None
    assert len(channel.delivered) == 4
    actions = {(event.symbol_value, event.action) for event in channel.delivered}
    assert ("BTCUSDT", "INCREASE_EXPOSURE") in actions
    assert ("BTCUSDT", "DECREASE_EXPOSURE") in actions


def test_notifications_are_persisted_before_delivery(tmp_path: Path) -> None:
    store_path = tmp_path / "events.jsonl"
    runtime, channel = _runtime(store_path)

    run_replay(_universe(), runtime)

    store = JsonlEventStore(store_path)
    persisted_ids = {event.key for event in store.events_of_kind("notification")}
    delivered_ids = {event.notification_id for event in channel.delivered}
    assert delivered_ids == persisted_ids
    assert store.count_of_kind("signal") == 11 * 2
    assert store.count_of_kind("cycle") == 11


def test_restart_duplicates_no_orders_and_no_notifications(tmp_path: Path) -> None:
    # Reference: one uninterrupted run.
    reference_runtime, reference_channel = _runtime(tmp_path / "single.jsonl")
    run_replay(_universe(), reference_runtime)

    # Interrupted run: stop mid-history, then restart from the same store and
    # replay the FULL history again (everything already processed must dedup).
    split_store = tmp_path / "split.jsonl"
    first_runtime, first_channel = _runtime(split_store)
    run_replay(_universe(days=204), first_runtime)

    second_runtime, second_channel = _runtime(split_store)
    run_replay(_universe(), second_runtime)

    reference_store = JsonlEventStore(tmp_path / "single.jsonl")
    split_result_store = JsonlEventStore(split_store)
    for kind in ("order", "fill", "notification", "signal", "cycle"):
        assert split_result_store.count_of_kind(kind) == reference_store.count_of_kind(kind), kind

    total_delivered = len(first_channel.delivered) + len(second_channel.delivered)
    assert total_delivered == len(reference_channel.delivered)
    assert second_runtime.last_processed == reference_runtime.last_processed

    reference_cycle = reference_store.latest_of_kind("cycle")
    split_cycle = split_result_store.latest_of_kind("cycle")
    assert reference_cycle is not None
    assert split_cycle is not None
    assert reference_cycle.payload["account"] == split_cycle.payload["account"]


def test_warmup_history_emits_health_event_and_skips(tmp_path: Path) -> None:
    store_path = tmp_path / "events.jsonl"
    runtime, channel = _runtime(store_path)

    summary = run_replay(_universe(days=150), runtime)

    assert summary.cycles_processed == 0
    assert not channel.delivered
    store = JsonlEventStore(store_path)
    health = store.events_of_kind("health")
    assert health
    assert all(event.payload["code"] == WARMUP_INSUFFICIENT_HISTORY for event in health)


def test_stale_data_blocks_new_exposure_but_not_the_cycle(tmp_path: Path) -> None:
    runtime, _channel = _runtime(tmp_path / "events.jsonl")
    universe = _universe()

    # Process through the first trend close normally: decision goes to 1.
    first = runtime.process_closed_candles(
        {symbol: candles[: _WARMUP + 1] for symbol, candles in universe.items()}
    )
    assert first.processed

    # Next candle arrives, but we only observe it three days late: the pending
    # buy must be blocked while the cycle itself still runs.
    late_observation = universe["BTCUSDT"][_WARMUP + 1].close_time + timedelta(days=3)
    second = runtime.process_closed_candles(
        {symbol: candles[: _WARMUP + 2] for symbol, candles in universe.items()},
        observed_at=late_observation,
    )

    assert second.processed
    assert STALE_DATA_HALT in second.health_codes
    assert not second.fills
    assert second.rejection_reason_codes
    assert all(STALE_DATA_HALT in codes for _, codes in second.rejection_reason_codes)


def test_open_candles_are_rejected(tmp_path: Path) -> None:
    runtime, _channel = _runtime(tmp_path / "events.jsonl")
    universe = _universe(days=_WARMUP + 1)
    btc = universe["BTCUSDT"]
    universe["BTCUSDT"] = btc[:-1] + (
        _daily_candle(_symbol("BTCUSDT", "BTC"), _WARMUP, Decimal("200"), is_closed=False),
    )

    with pytest.raises(RuntimeEngineError, match="still-open"):
        runtime.process_closed_candles(universe)


def test_event_store_deduplicates_by_key(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path / "events.jsonl")
    recorded_at = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)

    assert store.append(kind="order", key="order:x", recorded_at=recorded_at, payload={"a": 1})
    assert not store.append(kind="order", key="order:x", recorded_at=recorded_at, payload={"a": 2})

    reloaded = JsonlEventStore(tmp_path / "events.jsonl")
    assert reloaded.has("order:x")
    assert reloaded.count_of_kind("order") == 1
    latest = reloaded.latest_of_kind("order")
    assert latest is not None
    assert latest.payload == {"a": 1}


class _FlakyChannel:
    """Raises on the first deliveries, then succeeds — a webhook outage."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.delivered: list[object] = []

    def deliver(self, event: object) -> None:
        if self.failures > 0:
            self.failures -= 1
            msg = "simulated webhook outage"
            raise ConnectionError(msg)
        self.delivered.append(event)


def test_mid_cycle_crash_after_fill_loses_nothing(tmp_path: Path) -> None:
    """Crash between a fill append and the cycle snapshot must not eat the fill."""

    reference_runtime, _ = _runtime(tmp_path / "reference.jsonl")
    run_replay(_universe(), reference_runtime)
    reference_store = JsonlEventStore(tmp_path / "reference.jsonl")

    # Run up to and including the first fill day, then simulate a crash by
    # truncating the store right after the LAST fill event of that day.
    crash_store_path = tmp_path / "crash.jsonl"
    first_runtime, _ = _runtime(crash_store_path)
    run_replay(_universe(days=_WARMUP + 2), first_runtime)
    lines = crash_store_path.read_text(encoding="utf-8").splitlines()
    last_fill_index = max(index for index, line in enumerate(lines) if '"kind": "fill"' in line)
    assert last_fill_index < len(lines) - 1, "test setup: events must follow the fill"
    crash_store_path.write_text("\n".join(lines[: last_fill_index + 1]) + "\n", encoding="utf-8")

    # Restart from the truncated store and replay the full history.
    second_runtime, _ = _runtime(crash_store_path)
    run_replay(_universe(), second_runtime)
    crashed_store = JsonlEventStore(crash_store_path)

    for kind in ("order", "fill", "notification", "cycle"):
        assert crashed_store.count_of_kind(kind) == reference_store.count_of_kind(kind), kind
    reference_cycle = reference_store.latest_of_kind("cycle")
    crashed_cycle = crashed_store.latest_of_kind("cycle")
    assert reference_cycle is not None
    assert crashed_cycle is not None
    assert reference_cycle.payload["account"] == crashed_cycle.payload["account"]


def test_delivery_outage_never_corrupts_the_cycle_and_retries(tmp_path: Path) -> None:
    store_path = tmp_path / "events.jsonl"
    channel = _FlakyChannel(failures=2)
    runtime = SignalRuntime(
        parameters=_parameters(),
        store=JsonlEventStore(store_path),
        channel=channel,
    )

    summary = run_replay(_universe(), runtime)

    # Every cycle processed despite the outage; the two failed deliveries were
    # retried on later cycles, so everything persisted was delivered.
    assert summary.cycles_processed == 11
    store = JsonlEventStore(store_path)
    assert store.count_of_kind("notification") == 4
    assert store.count_of_kind("notification_delivered") == 4
    assert len(channel.delivered) == 4
    failures = [
        event
        for event in store.events_of_kind("health")
        if event.payload.get("code") == "NOTIFICATION_DELIVERY_FAILED"
    ]
    assert failures


def test_torn_final_line_is_quarantined_not_fatal(tmp_path: Path) -> None:
    store_path = tmp_path / "events.jsonl"
    store = JsonlEventStore(store_path)
    recorded_at = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    store.append(kind="health", key="health:x", recorded_at=recorded_at, payload={"a": 1})
    with store_path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "cycle", "key": "cycle:tor')  # power-loss artifact

    reloaded = JsonlEventStore(store_path)

    assert reloaded.count_of_kind("health") == 1
    assert not reloaded.has("cycle:tor")
    assert store_path.with_suffix(".jsonl.torn").exists()
    # The intact prefix is preserved and the store accepts appends again.
    assert reloaded.append(kind="cycle", key="cycle:new", recorded_at=recorded_at, payload={})
