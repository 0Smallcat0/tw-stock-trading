"""Low-volatility factor engine tests: selection, costs, marking, delisting."""

from __future__ import annotations

from datetime import date

import pytest

from src.factor import (
    FactorParameters,
    RebalancePlan,
    month_end_dates,
    run_backtest,
    select_holdings,
    simulate,
)
from src.factor.lowvol import FactorError, RawSeries


def test_month_end_dates_picks_last_session_per_month() -> None:
    dates = [date(2020, 1, 30), date(2020, 1, 31), date(2020, 2, 27), date(2020, 2, 28)]
    assert month_end_dates(dates) == (date(2020, 1, 31), date(2020, 2, 28))


def test_select_holdings_ranks_lowest_vol_and_applies_floors() -> None:
    params = FactorParameters(n=2, min_price=5.0, liquidity_floor=1_000_000.0)
    candidates = {
        "A": (0.010, 10.0, 2_000_000.0),
        "B": (0.020, 10.0, 2_000_000.0),
        "C": (0.005, 10.0, 2_000_000.0),
        "D": (0.001, 3.0, 2_000_000.0),  # price below floor
        "E": (0.001, 10.0, 500_000.0),  # illiquid
    }
    weights = select_holdings(candidates, params)
    assert set(weights) == {"C", "A"}  # two lowest vol among the eligible
    assert weights["C"] == pytest.approx(0.5)
    assert weights["A"] == pytest.approx(0.5)


def test_select_holdings_inverse_vol_weights_favor_calmer_names() -> None:
    params = FactorParameters(n=2, weight="inverse_vol", liquidity_floor=0.0, min_price=0.0)
    candidates = {"C": (0.005, 10.0, 1.0), "A": (0.010, 10.0, 1.0)}
    weights = select_holdings(candidates, params)
    # 1/0.005 : 1/0.010 = 200 : 100 -> 2/3, 1/3
    assert weights["C"] == pytest.approx(2 / 3)
    assert weights["A"] == pytest.approx(1 / 3)


def test_select_holdings_empty_when_nothing_eligible() -> None:
    params = FactorParameters(n=5, liquidity_floor=1e12)
    assert select_holdings({"A": (0.01, 10.0, 1.0)}, params) == {}


def _zero_cost(**kw: object) -> FactorParameters:
    base = dict(
        n=1,
        commission_bps=0.0,
        min_fee=0.0,
        slippage_bps=0.0,
        sell_tax_bps=0.0,
        cash_yield_annual_bps=0.0,
        initial_capital=1_000_000.0,
    )
    base.update(kw)
    return FactorParameters(**base)  # type: ignore[arg-type]


def test_simulate_tracks_adjusted_total_return() -> None:
    params = _zero_cost()
    plans = [RebalancePlan(date(2020, 1, 1), date(2020, 1, 1), {"X": 1.0})]
    lookup = {"X": {date(2020, 1, 1): 100.0, date(2020, 1, 2): 110.0, date(2020, 1, 3): 121.0}}
    all_dates = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]
    result = simulate(plans, lookup, all_dates, params)
    assert result.equity_curve[0][1] == pytest.approx(1_000_000.0)
    assert result.equity_curve[-1][1] == pytest.approx(1_210_000.0)  # +10% then +10%


def test_simulate_charges_entry_costs() -> None:
    params = FactorParameters(
        n=1,
        commission_bps=8.55,
        min_fee=20.0,
        slippage_bps=15.0,
        sell_tax_bps=30.0,
        cash_yield_annual_bps=0.0,
        initial_capital=1_000_000.0,
    )
    plans = [RebalancePlan(date(2020, 1, 1), date(2020, 1, 1), {"X": 1.0})]
    lookup = {"X": {date(2020, 1, 1): 100.0, date(2020, 1, 2): 100.0}}
    result = simulate(plans, lookup, [date(2020, 1, 1), date(2020, 1, 2)], params)
    # buy 1e6: commission 855 + slippage 1500, no tax on buy -> 2355
    assert result.total_cost == pytest.approx(2355.0)
    assert result.equity_curve[0][1] == pytest.approx(997_645.0)


def test_delisting_freezes_value_without_rescue() -> None:
    params = _zero_cost()
    plans = [RebalancePlan(date(2020, 1, 1), date(2020, 1, 1), {"X": 1.0})]
    # X stops trading after Jan 2 (no Jan 3 price) -> value freezes, not NaN/rescued
    lookup = {"X": {date(2020, 1, 1): 100.0, date(2020, 1, 2): 80.0}}
    all_dates = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]
    result = simulate(plans, lookup, all_dates, params)
    assert result.equity_curve[-1][1] == pytest.approx(800_000.0)  # frozen at the -20% mark


def test_sell_tax_charged_only_on_the_sold_leg_at_rebalance() -> None:
    # Two rebalances: fully rotate X -> Y. Selling X pays tax; buying Y does not.
    params = FactorParameters(
        n=1,
        commission_bps=0.0,
        min_fee=0.0,
        slippage_bps=0.0,
        sell_tax_bps=30.0,
        cash_yield_annual_bps=0.0,
        initial_capital=1_000_000.0,
    )
    d = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4)]
    plans = [
        RebalancePlan(d[0], d[0], {"X": 1.0}),
        RebalancePlan(d[2], d[2], {"Y": 1.0}),
    ]
    lookup = {
        "X": {d[0]: 100.0, d[1]: 100.0, d[2]: 100.0, d[3]: 100.0},
        "Y": {d[0]: 50.0, d[1]: 50.0, d[2]: 50.0, d[3]: 50.0},
    }
    result = simulate(plans, lookup, d, params)
    # first entry: buy 1e6, no tax. second: sell ~1e6 X (tax 0.3% = 3000) + buy Y (no tax).
    assert result.total_cost == pytest.approx(3000.0, rel=1e-3)


def _business_days(year: int, month0: int, count: int) -> list[date]:
    out: list[date] = []
    day = date(year, month0, 1)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return out


def test_run_backtest_end_to_end_small_universe() -> None:
    dates = _business_days(2020, 1, 45)  # ~2 months of sessions -> a Feb month-end rebalance
    params = FactorParameters(
        n=2,
        lookback_days=5,
        liquidity_window=3,
        liquidity_floor=1.0,
        min_price=1.0,
        commission_bps=8.55,
        min_fee=20.0,
        slippage_bps=15.0,
        sell_tax_bps=30.0,
        initial_capital=1_000_000.0,
    )
    raw: dict[str, RawSeries] = {}
    lookup: dict[str, dict[date, float]] = {}
    # Three stocks with different vol: LOW steady, MID, HIGH choppy.
    profiles = {"LOW": 0.002, "MID": 0.01, "HIGH": 0.05}
    for stock_id, step in profiles.items():
        closes: list[float] = []
        price = 100.0
        for i in range(len(dates)):
            price *= 1.0 + (step if i % 2 == 0 else -step)
            closes.append(round(price, 4))
        raw[stock_id] = RawSeries(
            stock_id=stock_id,
            dates=tuple(dates),
            close=tuple(closes),
            turnover=tuple(100_000_000.0 for _ in dates),
        )
        lookup[stock_id] = dict(zip(dates, closes, strict=True))
    result = run_backtest(raw, lookup, dates, dates[0], dates[-1], params)
    assert result.rebalances >= 1
    assert 0 < result.avg_names <= params.n
    assert result.periods_per_year > 0.0
    assert len(result.equity_curve) > 10


def test_raw_series_length_mismatch_rejected() -> None:
    with pytest.raises(FactorError):
        RawSeries(stock_id="Z", dates=(date(2020, 1, 1),), close=(1.0, 2.0), turnover=(1.0,))
