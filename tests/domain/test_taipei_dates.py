from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from src.domain import Candle, DomainValidationError, Symbol, Timeframe, taipei_date


def _candle(*, trading_date: date | None) -> Candle:
    # 0050 daily session 2026-07-02: 09:00-13:30 Taipei = 01:00-05:30 UTC.
    open_time = datetime(2026, 7, 2, 1, 0, tzinfo=UTC)
    return Candle(
        symbol=Symbol(value="0050", base_asset="0050", quote_asset="TWD"),
        timeframe=Timeframe("1d"),
        open_time=open_time,
        close_time=open_time + timedelta(hours=4, minutes=30),
        open_price=Decimal("108.00"),
        high_price=Decimal("108.60"),
        low_price=Decimal("107.60"),
        close_price=Decimal("108.35"),
        volume=Decimal("73000000"),
        is_closed=True,
        trading_date=trading_date,
    )


def test_taipei_date_converts_utc_timestamps() -> None:
    assert taipei_date(datetime(2026, 7, 2, 5, 30, tzinfo=UTC)) == date(2026, 7, 2)
    # 20:00 UTC is already the next Taipei calendar day (UTC+8).
    assert taipei_date(datetime(2026, 7, 2, 20, 0, tzinfo=UTC)) == date(2026, 7, 3)


def test_taipei_date_rejects_naive_datetimes() -> None:
    with pytest.raises(DomainValidationError):
        taipei_date(datetime(2026, 7, 2, 5, 30))  # noqa: DTZ001


def test_candle_accepts_matching_trading_date() -> None:
    candle = _candle(trading_date=date(2026, 7, 2))
    assert candle.trading_date == date(2026, 7, 2)


def test_candle_without_trading_date_stays_valid() -> None:
    assert _candle(trading_date=None).trading_date is None


def test_candle_rejects_mismatched_trading_date() -> None:
    with pytest.raises(DomainValidationError, match="trading_date"):
        _candle(trading_date=date(2026, 7, 3))
