"""TWSE daily session time helpers.

The regular session runs 09:00-13:30 Taipei. Daily candles carry these
session boundaries as UTC timestamps plus the Taipei ``trading_date``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from src.domain import TAIPEI_TZ

_SESSION_OPEN = time(9, 0)
_SESSION_CLOSE = time(13, 30)


def session_open_utc(trading_date: date) -> datetime:
    """Return the 09:00 Taipei session open as a UTC timestamp."""

    return datetime.combine(trading_date, _SESSION_OPEN, tzinfo=TAIPEI_TZ).astimezone(UTC)


def session_close_utc(trading_date: date) -> datetime:
    """Return the 13:30 Taipei session close as a UTC timestamp."""

    return datetime.combine(trading_date, _SESSION_CLOSE, tzinfo=TAIPEI_TZ).astimezone(UTC)
