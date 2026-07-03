"""Calendar-aware validation for TW daily candle series.

TW replaces the crypto edition's continuous-time rules:

- GAP means a missing TRADING day per the exchange calendar; weekends,
  holidays, and make-up-day closures are not gaps. Trading days inside a
  known corporate-action halt (``expected_missing_dates``) are exempt.
- STALE is measured in TRADING days between the latest candle and the
  observation date — an 11-calendar-day Lunar New Year adds zero.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from datetime import date

from src.data.calendar import TradingCalendar
from src.data.types import (
    CandleIssueCode,
    CandleQualityIssue,
    CandleQualityReport,
    MarketDataValidationError,
)
from src.domain import Candle


def require_closed_candles(candles: Iterable[Candle]) -> tuple[Candle, ...]:
    """Return candles only when every item is closed; otherwise raise a hard block."""

    candle_tuple = tuple(candles)
    open_candles = [candle for candle in candle_tuple if not candle.is_closed]
    if open_candles:
        first = open_candles[0]
        msg = (
            "OPEN_CANDLE: candle is not safe for strategy input "
            f"({first.symbol.value} {first.timeframe.value} {first.open_time.isoformat()})"
        )
        raise MarketDataValidationError(msg)
    return candle_tuple


def inspect_daily_candle_quality(
    candles: Iterable[Candle],
    *,
    calendar: TradingCalendar,
    observed_on: date,
    stale_trading_days: int,
    expected_missing_dates: Collection[date] = frozenset(),
) -> CandleQualityReport:
    """Detect open-candle, duplicate, gap, and staleness issues per symbol."""

    if stale_trading_days <= 0:
        msg = "stale_trading_days must be positive"
        raise MarketDataValidationError(msg)

    candle_tuple = tuple(candles)
    issues: list[CandleQualityIssue] = []

    for candle in candle_tuple:
        if not candle.is_closed:
            issues.append(
                CandleQualityIssue(
                    code=CandleIssueCode.OPEN_CANDLE,
                    symbol=candle.symbol.value,
                    timeframe=candle.timeframe.value,
                    open_time=candle.open_time,
                    detail="still-open candle must not enter strategy input",
                )
            )
        if candle.trading_date is None:
            msg = (
                "daily quality checks require trading_date on every candle "
                f"({candle.symbol.value} {candle.open_time.isoformat()})"
            )
            raise MarketDataValidationError(msg)

    grouped: dict[tuple[str, str], list[Candle]] = defaultdict(list)
    for candle in candle_tuple:
        grouped[(candle.symbol.value, candle.timeframe.value)].append(candle)

    for (symbol_value, timeframe_value), group in grouped.items():
        ordered = sorted(group, key=lambda item: item.open_time)
        seen_dates: set[date] = set()
        unique_closed: list[Candle] = []
        for candle in ordered:
            candle_date = candle.trading_date
            if candle_date is None:  # pragma: no cover - validated above
                continue
            if candle_date in seen_dates:
                issues.append(
                    CandleQualityIssue(
                        code=CandleIssueCode.DUPLICATE,
                        symbol=symbol_value,
                        timeframe=timeframe_value,
                        open_time=candle.open_time,
                        detail=f"duplicate trading date {candle_date.isoformat()}",
                    )
                )
                continue
            seen_dates.add(candle_date)
            if candle.is_closed:
                unique_closed.append(candle)

        for previous, current in zip(unique_closed, unique_closed[1:]):
            previous_date = previous.trading_date
            current_date = current.trading_date
            if previous_date is None or current_date is None:  # pragma: no cover
                continue
            expected = calendar.next_trading_day(previous_date)
            while expected < current_date:
                if expected not in expected_missing_dates:
                    issues.append(
                        CandleQualityIssue(
                            code=CandleIssueCode.GAP,
                            symbol=symbol_value,
                            timeframe=timeframe_value,
                            expected_open_time=None,
                            actual_open_time=current.open_time,
                            detail=f"missing trading day {expected.isoformat()}",
                        )
                    )
                expected = calendar.next_trading_day(expected)

        if unique_closed:
            latest = unique_closed[-1]
            latest_date = latest.trading_date
            if latest_date is not None and observed_on >= latest_date:
                age = calendar.trading_days_between(latest_date, observed_on)
                if age > stale_trading_days:
                    issues.append(
                        CandleQualityIssue(
                            code=CandleIssueCode.STALE,
                            symbol=symbol_value,
                            timeframe=timeframe_value,
                            open_time=latest.open_time,
                            detail=(
                                f"latest close {latest_date.isoformat()} is {age} "
                                f"trading days old (limit {stale_trading_days})"
                            ),
                        )
                    )

    return CandleQualityReport(issues=tuple(issues))
