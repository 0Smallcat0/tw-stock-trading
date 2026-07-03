from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.backtest import BacktestError, BacktestParameters, run_backtest
from src.domain import Candle, OrderSide, Symbol, Timeframe

_BASE_OPEN_TIME = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
_WARMUP = 200


def _symbol(value: str, base_asset: str) -> Symbol:
    return Symbol(value=value, base_asset=base_asset, quote_asset="USDT")


def _daily_candle(symbol: Symbol, index: int, close: Decimal) -> Candle:
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
        is_closed=True,
    )


def _trend_and_crash_series(scale: Decimal) -> tuple[Decimal, ...]:
    """200 flat days, 24 strictly rising trend days, one crash day, 4 flat days.

    The trend leg rises every day so the close stays strictly above every SMA
    (the contract counts equality as NOT above, which a flat plateau would hit).
    """

    prices = [Decimal("100") * scale] * _WARMUP
    prices.extend([(Decimal("200") + Decimal(index)) * scale for index in range(24)])
    prices.append(Decimal("100") * scale)
    prices.extend([Decimal("100") * scale] * 4)
    return tuple(prices)


def _candles_for(symbol: Symbol, prices: tuple[Decimal, ...]) -> tuple[Candle, ...]:
    return tuple(_daily_candle(symbol, index, price) for index, price in enumerate(prices))


def _parameters(cost_multiplier: str = "1") -> BacktestParameters:
    return BacktestParameters(
        risk_budgets={"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal("0.5")},
        initial_cash=Decimal("1000"),
        account_id="bt-test",
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        quantity_step=Decimal("0.000001"),
        price_tick=Decimal("0.01"),
        min_notional_usdt=Decimal("10"),
        max_drawdown_fraction=Decimal("0.20"),
        daily_loss_pause_fraction=Decimal("0.05"),
        disaster_single_day_drop_fraction=Decimal("0.20"),
        stale_data_max_age_seconds=129600,
        cost_multiplier=Decimal(cost_multiplier),
    )


def _trend_and_crash_universe() -> dict[str, tuple[Candle, ...]]:
    return {
        "BTCUSDT": _candles_for(_symbol("BTCUSDT", "BTC"), _trend_and_crash_series(Decimal("1"))),
        "ETHUSDT": _candles_for(_symbol("ETHUSDT", "ETH"), _trend_and_crash_series(Decimal("0.1"))),
    }


def test_replay_buys_on_trend_and_exits_to_cash_after_the_crash() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    buys = [fill for fill in report.fills if fill.side is OrderSide.BUY]
    sells = [fill for fill in report.fills if fill.side is OrderSide.SELL]
    assert len(buys) == 2
    assert len(sells) == 2

    # Final state: crash forced the ladder to zero, everything back in cash.
    final_point = report.equity_curve[-1]
    assert final_point.equity > Decimal("0")
    assert report.metrics.trade_count == 4
    assert report.metrics.total_fees > Decimal("0")
    assert report.metrics.max_drawdown_fraction >= Decimal("0.20")


def test_fills_only_happen_on_the_bar_after_the_decision() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    decision_times = {entry.as_of for entry in report.signals}
    for fill in report.fills:
        prior_decisions = [as_of for as_of in decision_times if as_of < fill.filled_at]
        assert prior_decisions, "fill must follow a strictly earlier decision close"
        # The fill lands exactly at the next bar's open (close + 1ms).
        latest_decision = max(prior_decisions)
        assert fill.filled_at == latest_decision + timedelta(milliseconds=1)


def test_buy_fill_price_includes_slippage_and_fee_assumptions() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    btc_buy = next(
        fill
        for fill in report.fills
        if fill.side is OrderSide.BUY and fill.symbol.value == "BTCUSDT"
    )
    # Open 201 with 5 bps slippage (201.1005), rounded up to the 0.01 tick.
    assert btc_buy.price == Decimal("201.11")
    expected_fee = btc_buy.quantity * btc_buy.price * Decimal("10") / Decimal("10000")
    assert btc_buy.fee == expected_fee


def test_crash_day_emits_disaster_events_for_both_symbols() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    disaster_symbols = {event.symbol.value for event in report.risk_events}
    assert disaster_symbols == {"BTCUSDT", "ETHUSDT"}
    for event in report.risk_events:
        # Crash from 223 to 100 is a ~55% single-day drop.
        assert event.observed_fraction > Decimal("0.5")


def test_cost_stress_multiplier_doubles_effective_costs() -> None:
    base = run_backtest(_trend_and_crash_universe(), parameters=_parameters())
    stressed = run_backtest(_trend_and_crash_universe(), parameters=_parameters("2"))

    assert stressed.cost_assumptions["fee_bps"] == "20"
    assert stressed.cost_assumptions["slippage_bps"] == "10"
    assert stressed.metrics.total_fees > base.metrics.total_fees
    assert stressed.metrics.final_equity < base.metrics.final_equity


def test_replay_is_deterministic() -> None:
    first = run_backtest(_trend_and_crash_universe(), parameters=_parameters())
    second = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    assert first.metrics == second.metrics
    assert first.fills == second.fills
    assert first.equity_curve == second.equity_curve


def test_equity_curve_and_signal_counts_match_decision_days() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    assert report.decision_days == 30
    assert len(report.signals) == 30 * 2
    # The final decision day has no next bar to execute or mark on.
    assert len(report.equity_curve) == 29
    assert report.metrics.benchmark_final_equity > Decimal("0")


def test_misaligned_decision_days_are_rejected() -> None:
    universe = _trend_and_crash_universe()
    eth = universe["ETHUSDT"]
    universe["ETHUSDT"] = eth[:-1]

    with pytest.raises(BacktestError, match="align"):
        run_backtest(universe, parameters=_parameters())


def test_candles_must_cover_exactly_the_budgeted_universe() -> None:
    universe = _trend_and_crash_universe()
    universe.pop("ETHUSDT")

    with pytest.raises(BacktestError, match="universe"):
        run_backtest(universe, parameters=_parameters())


def test_rejected_count_never_double_counts_and_turnover_is_reported() -> None:
    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    # Broker rejections and gate rejections are disjoint sets in the metric.
    assert report.metrics.rejected_count == len(report.rejected_orders) + len(
        report.risk_rejections
    )
    assert report.metrics.total_traded_notional > Decimal("0")
    assert report.metrics.annualized_turnover > Decimal("0")


def test_simultaneous_full_budget_buys_fit_costs_inside_the_ladder_claim() -> None:
    """Budgets summing to 1 must not starve whichever symbol trades last."""

    report = run_backtest(_trend_and_crash_universe(), parameters=_parameters())

    buys = [fill for fill in report.fills if fill.side is OrderSide.BUY]
    assert len(buys) == 2
    total_cost = sum((fill.quantity * fill.price + fill.fee for fill in buys), Decimal("0"))
    assert total_cost <= Decimal("1000")
    # Both symbols' buy costs match their equal budgets to within rounding
    # dust: the fee burden is shared by construction, not dumped on the
    # alphabetically-last symbol.
    costs = sorted(fill.quantity * fill.price + fill.fee for fill in buys)
    assert costs[1] - costs[0] < Decimal("1")
    # No hidden shortfall: nothing was rejected on the buy day.
    assert not report.risk_rejections
