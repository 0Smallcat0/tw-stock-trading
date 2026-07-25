"""Guards for the two-provider US ETF ingestion path."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.data.types import MarketDataValidationError
from src.data.usetf import (
    cross_check_sources,
    missing_sessions,
    parse_finmind_us_payload,
    parse_yahoo_chart_payload,
    session_times,
)

_OBSERVED_AT = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
_DAYS = (date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22))


def _yahoo_payload(
    days: tuple[date, ...],
    *,
    closes: list[float | None],
    adjusted: list[float | None] | None = None,
) -> dict[str, Any]:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "timestamp": [int(session_times(day)[0].timestamp()) for day in days],
                    "indicators": {
                        "quote": [
                            {
                                "open": list(closes),
                                "high": [None if c is None else c + 1.0 for c in closes],
                                "low": [None if c is None else c - 1.0 for c in closes],
                                "close": list(closes),
                                "volume": [None if c is None else 1000 for c in closes],
                            }
                        ],
                        "adjclose": [{"adjclose": adjusted if adjusted is not None else closes}],
                    },
                }
            ],
        }
    }


def _finmind_payload(days: tuple[date, ...], *, closes: list[float]) -> dict[str, Any]:
    return {
        "msg": "success",
        "status": 200,
        "data": [
            {
                "date": day.isoformat(),
                "stock_id": "GLD",
                "Open": close,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Adj_Close": close,
                "Volume": 1000,
            }
            for day, close in zip(days, closes, strict=True)
        ],
    }


def test_a_holed_row_is_reported_not_silently_dropped() -> None:
    series = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, None, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )

    assert len(series.candles) == 2
    assert series.incomplete_dates == (date(2026, 7, 21),)


def test_an_adjusted_close_that_differs_flags_a_distribution() -> None:
    # GLD makes no distributions, so raw closes are the total-return series.
    # If that ever changes the sleeve would silently understate its return.
    series = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, 101.0, 102.0], adjusted=[99.5, 101.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )

    assert series.distribution_dates == (date(2026, 7, 20),)


def test_a_session_that_has_not_closed_yet_is_not_a_candle() -> None:
    # Observed during the 2026-07-22 session: that bar is still forming.
    mid_session = datetime(2026, 7, 22, 17, 0, tzinfo=UTC)

    series = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, 101.0, 102.0]),
        ticker="GLD",
        observed_at=mid_session,
    )

    assert sorted(series.trading_dates) == [date(2026, 7, 20), date(2026, 7, 21)]


def test_cross_check_judges_only_the_range_both_providers_cover() -> None:
    primary = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, 101.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )
    # Secondary history starts a day late; that is not a missing session.
    secondary = parse_finmind_us_payload(
        _finmind_payload(_DAYS[1:], closes=[101.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )

    report = cross_check_sources(primary, secondary, tolerance=Decimal("0.005"))

    assert report.common_start == date(2026, 7, 21)
    assert report.shared_days == 2
    assert report.primary_only == ()
    assert report.agreement_ratio == Decimal("1")


def test_cross_check_catches_a_close_the_second_provider_contradicts() -> None:
    primary = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, 101.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )
    secondary = parse_finmind_us_payload(
        _finmind_payload(_DAYS, closes=[100.0, 108.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )

    report = cross_check_sources(primary, secondary, tolerance=Decimal("0.005"))

    assert report.mismatch_dates == (date(2026, 7, 21),)
    assert report.worst_gap_date == date(2026, 7, 21)


def test_cross_check_refuses_an_empty_provider() -> None:
    primary = parse_yahoo_chart_payload(
        _yahoo_payload(_DAYS, closes=[100.0, 101.0, 102.0]),
        ticker="GLD",
        observed_at=_OBSERVED_AT,
    )
    empty = parse_finmind_us_payload({"data": []}, ticker="GLD", observed_at=_OBSERVED_AT)

    with pytest.raises(MarketDataValidationError):
        cross_check_sources(primary, empty, tolerance=Decimal("0.005"))


def test_long_silences_are_surfaced_by_date() -> None:
    days = (date(2026, 7, 20), date(2026, 7, 21), date(2026, 8, 10))
    series = parse_yahoo_chart_payload(
        _yahoo_payload(days, closes=[100.0, 101.0, 102.0]),
        ticker="GLD",
        observed_at=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
    )

    assert missing_sessions(series, max_gap=timedelta(days=5)) == (date(2026, 7, 21),)
