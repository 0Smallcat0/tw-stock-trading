from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.data import (
    CandleIssueCode,
    MarketDataValidationError,
    TradingCalendar,
    inspect_daily_candle_quality,
    session_close_utc,
    session_open_utc,
)
from src.domain import Candle, Symbol, Timeframe

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")

# 2026 Lunar-New-Year block: no trading 02-12 through 02-22, resume 02-23.
_LNY_CLOSURES = frozenset(
    {
        date(2026, 2, 12),
        date(2026, 2, 13),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
    }
)
_CALENDAR = TradingCalendar(covered_years=frozenset({2026}), closures=_LNY_CLOSURES)


def _candle(trading_date: date, *, is_closed: bool = True) -> Candle:
    return Candle(
        symbol=_SYMBOL,
        timeframe=Timeframe("1d"),
        open_time=session_open_utc(trading_date),
        close_time=session_close_utc(trading_date),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100"),
        volume=Decimal("1"),
        is_closed=is_closed,
        trading_date=trading_date,
    )


def _codes(candles: tuple[Candle, ...], *, observed_on: date, **kwargs: object) -> list[str]:
    report = inspect_daily_candle_quality(
        candles,
        calendar=_CALENDAR,
        observed_on=observed_on,
        stale_trading_days=1,
        **kwargs,  # type: ignore[arg-type]
    )
    return [issue.code.value for issue in report.issues]


def test_weekends_and_lny_are_not_gaps_or_staleness() -> None:
    candles = (_candle(date(2026, 2, 10)), _candle(date(2026, 2, 11)), _candle(date(2026, 2, 23)))

    assert _codes(candles, observed_on=date(2026, 2, 23)) == []


def test_missing_trading_day_is_a_gap() -> None:
    candles = (_candle(date(2026, 3, 2)), _candle(date(2026, 3, 4)))

    report = inspect_daily_candle_quality(
        candles, calendar=_CALENDAR, observed_on=date(2026, 3, 4), stale_trading_days=1
    )
    assert [issue.code for issue in report.issues] == [CandleIssueCode.GAP]
    assert "2026-03-03" in report.issues[0].detail


def test_expected_missing_dates_suppress_halt_gaps() -> None:
    candles = (_candle(date(2026, 3, 2)), _candle(date(2026, 3, 4)))

    assert (
        _codes(
            candles,
            observed_on=date(2026, 3, 4),
            expected_missing_dates=frozenset({date(2026, 3, 3)}),
        )
        == []
    )


def test_staleness_counts_trading_days_only() -> None:
    friday = _candle(date(2026, 3, 6))

    # Monday: one trading day old -> fine at stale_trading_days=1.
    assert _codes((friday,), observed_on=date(2026, 3, 9)) == []
    # Tuesday: two trading days old -> stale.
    assert _codes((friday,), observed_on=date(2026, 3, 10)) == [CandleIssueCode.STALE.value]


def test_duplicate_trading_dates_are_flagged() -> None:
    candles = (_candle(date(2026, 3, 2)), _candle(date(2026, 3, 2)))

    assert _codes(candles, observed_on=date(2026, 3, 2)) == [CandleIssueCode.DUPLICATE.value]


def test_open_candles_are_flagged() -> None:
    candles = (_candle(date(2026, 3, 2), is_closed=False),)

    assert CandleIssueCode.OPEN_CANDLE.value in _codes(candles, observed_on=date(2026, 3, 2))


def test_missing_trading_date_raises() -> None:
    naked = Candle(
        symbol=_SYMBOL,
        timeframe=Timeframe("1d"),
        open_time=session_open_utc(date(2026, 3, 2)),
        close_time=session_close_utc(date(2026, 3, 2)),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100"),
        volume=Decimal("1"),
        is_closed=True,
    )
    with pytest.raises(MarketDataValidationError, match="trading_date"):
        inspect_daily_candle_quality(
            (naked,), calendar=_CALENDAR, observed_on=date(2026, 3, 2), stale_trading_days=1
        )
