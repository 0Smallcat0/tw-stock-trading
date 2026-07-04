"""Synthetic daily-leveraged NAV series, calibrated to real 00631L.

A daily 2x product's NAV compounds ``factor * underlying_daily_return`` each
session. Reconstructing pre-2014 history (so 2008 is testable) requires a
synthetic path built from the underlying total-return series. Real 00631L
lags a frictionless 2x-TR by a measured ~3.77%/yr (expense ratio + tracking +
decay), so the synthetic carries a calibrated daily drag to match reality
rather than flatter it (docs/research/TW2_UNCONSTRAINED_RESEARCH.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.data import read_candles_jsonl
from src.data.types import MarketDataValidationError

# Calibrated on the 2014-10..2026-07 overlap: real 00631L CAGR 37.82% vs
# frictionless 2x(0050-TR) 41.59% -> the real product runs ~3.77%/yr behind a
# perfect 2x. Over ~246 trading days that is ~1.53 bps/session of drag on top
# of the volatility decay the daily compounding already reproduces.
LEVERAGE_DAILY_DRAG_BPS = Decimal("1.53")
_TRADING_DAYS_PER_YEAR = Decimal("246")


@dataclass(frozen=True, slots=True)
class LeveragePoint:
    """One NAV observation on a (real or synthetic) leveraged series."""

    trading_date: date
    nav: Decimal
    is_synthetic: bool


def load_underlying_total_return(path: str) -> list[tuple[date, Decimal]]:
    """Load a dividend/split-adjusted candle file as a (date, close) series."""

    candles = read_candles_jsonl(path)
    series = [
        (candle.trading_date, candle.close_price)
        for candle in candles
        if candle.trading_date is not None
    ]
    if len(series) != len(candles):
        msg = "underlying total-return series requires trading_date on every candle"
        raise MarketDataValidationError(msg)
    series.sort()
    return series


def build_leveraged_series(
    underlying_tr: Sequence[tuple[date, Decimal]],
    *,
    factor: Decimal = Decimal("2"),
    daily_drag_bps: Decimal = LEVERAGE_DAILY_DRAG_BPS,
    start_value: Decimal = Decimal("100"),
    start_on: date | None = None,
    is_synthetic: bool = True,
) -> list[LeveragePoint]:
    """Compound ``factor * daily_return`` minus a daily drag into a NAV path.

    ``start_on`` restricts the build to dates at or after a boundary (used to
    splice a synthetic pre-history before the real listing date).
    """

    if factor <= Decimal("0"):
        msg = "factor must be positive"
        raise MarketDataValidationError(msg)
    if daily_drag_bps < Decimal("0"):
        msg = "daily_drag_bps must not be negative"
        raise MarketDataValidationError(msg)
    ordered = sorted(underlying_tr)
    if start_on is not None:
        ordered = [(d, v) for d, v in ordered if d >= start_on]
    if len(ordered) < 2:
        msg = "leveraged series needs at least two underlying observations"
        raise MarketDataValidationError(msg)
    keep = Decimal("1") - daily_drag_bps / Decimal("10000")
    nav = start_value
    points = [LeveragePoint(trading_date=ordered[0][0], nav=nav, is_synthetic=is_synthetic)]
    for (_, prev_close), (cur_date, cur_close) in zip(ordered, ordered[1:]):
        if prev_close <= Decimal("0"):
            msg = "underlying close must be positive"
            raise MarketDataValidationError(msg)
        daily_return = cur_close / prev_close - Decimal("1")
        nav = nav * (Decimal("1") + factor * daily_return) * keep
        if nav <= Decimal("0"):
            # A daily-2x product is wiped out only by a single-session <= -50%
            # move; the ±10% TW limit makes that impossible, but guard anyway.
            nav = Decimal("0.0001")
        points.append(LeveragePoint(trading_date=cur_date, nav=nav, is_synthetic=is_synthetic))
    return points


def splice_leverage_series(
    synthetic: Sequence[LeveragePoint],
    real: Sequence[LeveragePoint],
) -> list[LeveragePoint]:
    """Join synthetic pre-history to the real series, scaled to be continuous.

    The synthetic segment is rescaled so its final NAV equals the real series'
    first NAV, giving one continuous total-return path with no seam jump.
    """

    if not synthetic or not real:
        msg = "both synthetic and real segments are required to splice"
        raise MarketDataValidationError(msg)
    real_sorted = sorted(real, key=lambda p: p.trading_date)
    syn_sorted = sorted(synthetic, key=lambda p: p.trading_date)
    boundary = real_sorted[0].trading_date
    pre = [p for p in syn_sorted if p.trading_date < boundary]
    if not pre:
        return list(real_sorted)
    scale = real_sorted[0].nav / pre[-1].nav
    rescaled = [
        LeveragePoint(trading_date=p.trading_date, nav=p.nav * scale, is_synthetic=True)
        for p in pre
    ]
    return rescaled + list(real_sorted)


def calibrate_daily_drag_bps(
    underlying_tr: Sequence[tuple[date, Decimal]],
    real: Sequence[LeveragePoint],
    *,
    factor: Decimal = Decimal("2"),
) -> Decimal:
    """Solve for the daily drag that matches a synthetic 2x to the real series.

    Used to (re)derive ``LEVERAGE_DAILY_DRAG_BPS`` from data; the caller can
    then pin it as a constant so backtests stay reproducible.
    """

    real_sorted = sorted(real, key=lambda p: p.trading_date)
    start, end = real_sorted[0].trading_date, real_sorted[-1].trading_date
    window = [(d, v) for d, v in sorted(underlying_tr) if start <= d <= end]
    if len(window) < 2:
        msg = "no overlap between underlying and real series"
        raise MarketDataValidationError(msg)
    target_growth = real_sorted[-1].nav / real_sorted[0].nav
    lo, hi = Decimal("0"), Decimal("50")
    for _ in range(60):
        mid = (lo + hi) / Decimal("2")
        built = build_leveraged_series(
            window, factor=factor, daily_drag_bps=mid, start_value=Decimal("100")
        )
        growth = built[-1].nav / built[0].nav
        if growth > target_growth:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / Decimal("2")


def annual_drag_from_daily_bps(daily_drag_bps: Decimal) -> Decimal:
    """Approximate annualized drag (for reporting) from the daily bps value."""

    return daily_drag_bps / Decimal("10000") * _TRADING_DAYS_PER_YEAR
