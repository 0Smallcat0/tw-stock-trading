"""Manual network smokes against the real public endpoints.

Run explicitly (never in CI):

    pytest -m network tests/data/test_public_data_smoke.py -q

These assert the live payload shapes still match the recorded fixtures the
unit tests pin — including that every REAL holiday-schedule row classifies
without error (the TW-B fixture promise).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from src.data import (
    FinMindPublicClient,
    TwsePublicClient,
)
from src.domain import Symbol, taipei_date

pytestmark = pytest.mark.network

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def test_holiday_schedule_rows_all_classify() -> None:
    with TwsePublicClient() as client:
        entries = client.fetch_current_year_holiday_schedule()
    assert entries, "holiday schedule came back empty"


def test_stock_day_split_month_matches_verified_facts() -> None:
    with TwsePublicClient() as client:
        candles = client.fetch_month_daily_candles(symbol=_SYMBOL, month=date(2025, 6, 1))

    by_date = {candle.trading_date: candle for candle in candles}
    # Resumption day traded ABOVE the 47.16 reference price: real close 47.57.
    assert by_date[date(2025, 6, 18)].close_price == Decimal("47.57")
    assert by_date[date(2025, 6, 18)].volume == Decimal("252639825")
    assert by_date[date(2025, 6, 10)].close_price == Decimal("188.65")
    halt = {date(2025, 6, day) for day in range(11, 18)}
    assert not (halt & set(by_date)), "halt sessions must have no candles"


def test_etf_split_event_matches_verified_row() -> None:
    with TwsePublicClient() as client:
        events = client.fetch_etf_splits(start=date(2025, 6, 1), end=date(2025, 6, 30))

    split = next(event for event in events if event.symbol_value == "0050")
    assert split.effective_date == date(2025, 6, 18)
    assert split.factor == Decimal("47.16") / Decimal("188.65")


def test_ex_right_results_include_0050_january_2026_dividend() -> None:
    with TwsePublicClient() as client:
        events = client.fetch_ex_right_results(start=date(2026, 1, 15), end=date(2026, 1, 31))

    dividend = next(event for event in events if event.symbol_value == "0050")
    assert dividend.effective_date == date(2026, 1, 22)


def test_finmind_and_twse_closes_reconcile_this_month() -> None:
    today = taipei_date(datetime.now(UTC))
    month_start = today.replace(day=1)
    with TwsePublicClient() as twse, FinMindPublicClient() as finmind:
        twse_candles = twse.fetch_month_daily_candles(symbol=_SYMBOL, month=month_start)
        finmind_candles = finmind.fetch_daily_candles(
            symbol=_SYMBOL, start=month_start - timedelta(days=5), end=today
        )

    twse_by_date = {candle.trading_date: candle.close_price for candle in twse_candles}
    finmind_by_date = {candle.trading_date: candle.close_price for candle in finmind_candles}
    overlap = set(twse_by_date) & set(finmind_by_date)
    assert overlap, "no overlapping dates fetched"
    for trading_date in overlap:
        assert twse_by_date[trading_date] == finmind_by_date[trading_date], trading_date
