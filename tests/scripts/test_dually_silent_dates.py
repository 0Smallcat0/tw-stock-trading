"""The verified-suspension guard: two silent sources vs one failed fetch."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from scripts.ingest_public_ohlcv import _dually_silent_dates
from src.domain import Candle, Symbol, Timeframe

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def _candle(day: date) -> Candle:
    open_time = datetime(day.year, day.month, day.day, 1, 0, tzinfo=UTC)
    return Candle(
        symbol=_SYMBOL,
        timeframe=Timeframe("1d"),
        open_time=open_time,
        close_time=open_time + timedelta(hours=4, minutes=30),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100"),
        volume=Decimal("1000"),
        is_closed=True,
        trading_date=day,
    )


_WEEK = (
    date(2026, 7, 6),
    date(2026, 7, 7),
    date(2026, 7, 8),
    date(2026, 7, 9),
    date(2026, 7, 10),
    date(2026, 7, 13),
    date(2026, 7, 14),
)


def test_both_sources_silent_on_an_open_day_is_a_verified_suspension() -> None:
    traded = tuple(day for day in _WEEK if day != date(2026, 7, 10))
    candles = tuple(_candle(day) for day in traded)

    assert _dually_silent_dates(
        trading_dates=_WEEK, finmind_candles=candles, twse_candles=candles
    ) == (date(2026, 7, 10),)


def test_one_source_silent_is_not_a_suspension() -> None:
    full = tuple(_candle(day) for day in _WEEK)
    partial = tuple(_candle(day) for day in _WEEK if day != date(2026, 7, 10))

    # Only FinMind is missing the day: that is a fetch problem, not a halt.
    assert (
        _dually_silent_dates(trading_dates=_WEEK, finmind_candles=partial, twse_candles=full) == ()
    )


def test_a_truncated_fetch_cannot_masquerade_as_suspensions() -> None:
    # Both sources stop early — the classic wholesale failure. Without the
    # inside-the-covered-range guard every later day would be "exempt".
    truncated = tuple(_candle(day) for day in _WEEK[:3])

    assert (
        _dually_silent_dates(trading_dates=_WEEK, finmind_candles=truncated, twse_candles=truncated)
        == ()
    )
