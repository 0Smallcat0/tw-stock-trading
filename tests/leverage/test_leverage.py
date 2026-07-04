"""Leverage-route module tests: synthetic 2x math, splice, rebalance, costs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.leverage import (
    LeveragePoint,
    RebalanceParameters,
    build_leveraged_series,
    calibrate_daily_drag_bps,
    simulate_rebalanced_hold,
    splice_leverage_series,
)
from src.leverage.rebalance import RebalanceError
from src.leverage.synthetic import annual_drag_from_daily_bps


def _series(returns: list[str], start: date = date(2020, 1, 2)) -> list[tuple[date, Decimal]]:
    """Underlying total-return series from daily returns (first day = 100)."""
    out = [(start, Decimal("100"))]
    d, v = start, Decimal("100")
    for r in returns:
        d = d + timedelta(days=1)
        v = v * (Decimal("1") + Decimal(r))
        out.append((d, v))
    return out


def test_daily_2x_doubles_daily_returns_without_drag() -> None:
    # Underlying +10% then -10%; 2x should be +20% then -20%, compounding.
    underlying = _series(["0.10", "-0.10"])
    lev = build_leveraged_series(
        underlying, factor=Decimal("2"), daily_drag_bps=Decimal("0"), start_value=Decimal("100")
    )
    assert lev[1].nav == Decimal("120")  # 100 * (1 + 2*0.10)
    assert lev[2].nav == Decimal("96")  # 120 * (1 - 2*0.10)


def test_volatility_decay_is_reproduced() -> None:
    # A flat round-trip (+10%,-9.09% back to par for the underlying) leaves 2x
    # BELOW par: that is the decay a daily-leveraged product suffers.
    underlying = [
        (date(2020, 1, 2), Decimal("100")),
        (date(2020, 1, 3), Decimal("110")),
        (date(2020, 1, 6), Decimal("100")),
    ]
    lev = build_leveraged_series(
        underlying, factor=Decimal("2"), daily_drag_bps=Decimal("0"), start_value=Decimal("100")
    )
    # up 20% to 120, then down 2*(10/110)=18.18% -> 120*(1-0.1818)=98.18 < 100
    assert lev[-1].nav < Decimal("100")


def test_daily_drag_reduces_nav_monotonically() -> None:
    underlying = _series(["0.01"] * 10)
    no_drag = build_leveraged_series(underlying, daily_drag_bps=Decimal("0"))
    with_drag = build_leveraged_series(underlying, daily_drag_bps=Decimal("5"))
    assert with_drag[-1].nav < no_drag[-1].nav


def test_calibration_recovers_a_known_drag() -> None:
    underlying = _series(["0.01", "-0.005", "0.02", "-0.01", "0.015"] * 20)
    real = build_leveraged_series(underlying, daily_drag_bps=Decimal("3"), is_synthetic=False)
    recovered = calibrate_daily_drag_bps(underlying, real)
    assert abs(recovered - Decimal("3")) < Decimal("0.05")


def test_splice_is_continuous_at_the_boundary() -> None:
    underlying = _series(["0.01"] * 30)
    synthetic = build_leveraged_series(underlying, daily_drag_bps=Decimal("1.5"))
    boundary = synthetic[15].trading_date
    real = [
        LeveragePoint(
            trading_date=p.trading_date, nav=Decimal("500") + Decimal(i), is_synthetic=False
        )
        for i, p in enumerate(synthetic[15:])
    ]
    spliced = splice_leverage_series(synthetic, real)
    # The last synthetic point before the boundary must equal the first real NAV.
    pre = [p for p in spliced if p.trading_date < boundary]
    assert pre[-1].nav == pytest.approx(Decimal("500"), abs=Decimal("0.01"))
    assert spliced[-1].nav == real[-1].nav


def test_annual_drag_reporting() -> None:
    assert annual_drag_from_daily_bps(Decimal("1.53")) == Decimal("1.53") / Decimal(
        "10000"
    ) * Decimal("246")


def test_rebalance_holds_fraction_and_charges_sell_tax() -> None:
    # Leverage doubles over the window; a 50% holder must trim it back and pay
    # the ETF sell tax on the trimmed notional.
    points = [
        LeveragePoint(trading_date=date(2020, 1, 2), nav=Decimal("100"), is_synthetic=False),
        LeveragePoint(trading_date=date(2020, 4, 1), nav=Decimal("200"), is_synthetic=False),
    ]
    params = RebalanceParameters(fraction=Decimal("0.5"), initial_capital=Decimal("100000"))
    result = simulate_rebalanced_hold(points, params)
    assert result.rebalance_count == 1
    assert result.total_costs > Decimal("0")
    # 50k in leverage doubled to 100k; total 150k; target 75k -> sold 25k, taxed.
    assert result.final_equity < Decimal("150000")  # costs bit into it
    assert result.final_equity > Decimal("149000")


def test_full_fraction_never_rebalances() -> None:
    points = [
        LeveragePoint(trading_date=date(2020, 1, 2), nav=Decimal("100"), is_synthetic=False),
        LeveragePoint(trading_date=date(2020, 4, 1), nav=Decimal("150"), is_synthetic=False),
    ]
    params = RebalanceParameters(fraction=Decimal("1.0"), initial_capital=Decimal("100000"))
    result = simulate_rebalanced_hold(points, params)
    assert result.rebalance_count == 0
    assert result.total_costs == Decimal("0")
    assert result.final_equity == Decimal("150000")  # pure 1.5x, no costs


def test_cash_buffer_cuts_drawdown_versus_full() -> None:
    # A crash sequence: leverage halves then recovers. The 50%+cash holder
    # must show a shallower drawdown than the 100% holder.
    navs = [Decimal("100"), Decimal("70"), Decimal("50"), Decimal("60"), Decimal("90")]
    points = [
        LeveragePoint(trading_date=date(2020, 1, 2) + timedelta(days=i), nav=n, is_synthetic=False)
        for i, n in enumerate(navs)
    ]
    full = simulate_rebalanced_hold(points, RebalanceParameters(fraction=Decimal("1.0")))
    half = simulate_rebalanced_hold(points, RebalanceParameters(fraction=Decimal("0.5")))
    assert half.max_drawdown_fraction < full.max_drawdown_fraction


def test_invalid_fraction_rejected() -> None:
    with pytest.raises(RebalanceError):
        RebalanceParameters(fraction=Decimal("0"))
    with pytest.raises(RebalanceError):
        RebalanceParameters(fraction=Decimal("1.5"))
