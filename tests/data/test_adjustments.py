from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.data import (
    CorporateActionEvent,
    CorporateActionKind,
    MarketDataValidationError,
    TradingCalendar,
    adjusted_price,
    adjustment_factors_file_name,
    apply_adjustments,
    cumulative_factor,
    read_adjustment_factors,
    session_close_utc,
    session_open_utc,
    suspension_dates,
    write_adjustment_factors,
)
from src.domain import Candle, Symbol, Timeframe

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def _candle(trading_date: date, close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol=_SYMBOL,
        timeframe=Timeframe("1d"),
        open_time=session_open_utc(trading_date),
        close_time=session_close_utc(trading_date),
        open_price=price,
        high_price=price + Decimal("1"),
        low_price=price - Decimal("1"),
        close_price=price,
        volume=Decimal("1000"),
        is_closed=True,
        trading_date=trading_date,
    )


def _dividend(effective: date) -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol_value="0050",
        effective_date=effective,
        kind=CorporateActionKind.DIVIDEND,
        prior_close=Decimal("100"),
        reference_price=Decimal("99"),
        source="twse_twt49u",
        description="息",
    )


def _split(effective: date) -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol_value="0050",
        effective_date=effective,
        kind=CorporateActionKind.SPLIT,
        prior_close=Decimal("188.65"),
        reference_price=Decimal("47.16"),
        source="twse_twtcau",
    )


def test_cumulative_factor_chains_only_later_events() -> None:
    events = (_dividend(date(2025, 1, 15)), _split(date(2025, 6, 18)))
    dividend_factor = Decimal("99") / Decimal("100")
    split_factor = Decimal("47.16") / Decimal("188.65")

    assert cumulative_factor(events, symbol_value="0050", on=date(2025, 1, 2)) == (
        dividend_factor * split_factor
    )
    assert cumulative_factor(events, symbol_value="0050", on=date(2025, 1, 20)) == split_factor
    assert cumulative_factor(events, symbol_value="0050", on=date(2025, 7, 1)) == Decimal("1")
    assert cumulative_factor(events, symbol_value="2330", on=date(2025, 1, 2)) == Decimal("1")


def test_apply_adjustments_scales_ohlc_and_keeps_volume_and_raw_series() -> None:
    candles = (
        _candle(date(2025, 1, 2), "100"),
        _candle(date(2025, 1, 20), "100"),
        _candle(date(2025, 7, 1), "25"),
    )
    events = (_dividend(date(2025, 1, 15)), _split(date(2025, 6, 18)))

    adjusted = apply_adjustments(candles, events)

    split_factor = Decimal("47.16") / Decimal("188.65")
    both_factor = (Decimal("99") / Decimal("100")) * split_factor
    assert adjusted[0].close_price == adjusted_price(Decimal("100"), both_factor)
    assert adjusted[0].open_price == adjusted[0].close_price
    assert adjusted[0].high_price == adjusted_price(Decimal("101"), both_factor)
    # Six-decimal precision: 100 * 0.99 * (47.16/188.65) ≈ 24.748688
    assert adjusted[0].close_price == Decimal("24.748688")
    assert adjusted[1].close_price == adjusted_price(Decimal("100"), split_factor)
    assert adjusted[2].close_price == Decimal("25")
    # volume stays raw; raw candles untouched
    assert all(candle.volume == Decimal("1000") for candle in adjusted)
    assert candles[0].close_price == Decimal("100")


def test_candles_without_trading_date_are_rejected() -> None:
    naked = Candle(
        symbol=_SYMBOL,
        timeframe=Timeframe("1d"),
        open_time=session_open_utc(date(2025, 1, 2)),
        close_time=session_close_utc(date(2025, 1, 2)),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100"),
        volume=Decimal("1"),
        is_closed=True,
    )
    with pytest.raises(MarketDataValidationError, match="trading_date"):
        apply_adjustments((naked,), (_dividend(date(2025, 1, 15)),))


def test_identical_duplicate_events_deduplicate_silently() -> None:
    # Market-wide sweeps contain real same-day repeats (2408 2014-09-09).
    candles = (_candle(date(2025, 1, 2), "100"), _candle(date(2025, 7, 1), "25"))
    once = apply_adjustments(candles, (_split(date(2025, 6, 18)),))
    twice = apply_adjustments(candles, (_split(date(2025, 6, 18)), _split(date(2025, 6, 18))))

    assert once == twice


def test_conflicting_same_key_events_are_rejected() -> None:
    conflicting = CorporateActionEvent(
        symbol_value="0050",
        effective_date=date(2025, 6, 18),
        kind=CorporateActionKind.SPLIT,
        prior_close=Decimal("188.65"),
        reference_price=Decimal("94.33"),  # different ratio, same key
        source="twse_twtcau",
    )
    with pytest.raises(MarketDataValidationError, match="conflicting"):
        apply_adjustments((), (_split(date(2025, 6, 18)), conflicting))


def test_suspension_dates_cover_the_halt_window() -> None:
    # 0050 split halt: last cum-trading day 2025-06-10, resumption 06-18.
    calendar = TradingCalendar(covered_years=frozenset({2025}), closures=frozenset())
    candles = (
        _candle(date(2025, 6, 9), "189"),
        _candle(date(2025, 6, 10), "188.65"),
        _candle(date(2025, 6, 18), "47.16"),
    )
    halt = suspension_dates(candles, (_split(date(2025, 6, 18)),), calendar)

    assert halt == frozenset(
        {
            date(2025, 6, 11),
            date(2025, 6, 12),
            date(2025, 6, 13),
            date(2025, 6, 16),
            date(2025, 6, 17),
        }
    )


def test_dividends_do_not_create_suspension_windows() -> None:
    calendar = TradingCalendar(covered_years=frozenset({2025}), closures=frozenset())
    candles = (_candle(date(2025, 1, 2), "100"), _candle(date(2025, 1, 20), "99"))

    assert suspension_dates(candles, (_dividend(date(2025, 1, 15)),), calendar) == frozenset()


def test_factor_table_round_trips(tmp_path: Path) -> None:
    events = (_dividend(date(2025, 1, 15)), _split(date(2025, 6, 18)))
    path = tmp_path / adjustment_factors_file_name("0050")

    write_adjustment_factors(events, path)
    restored = read_adjustment_factors(path)

    assert restored == events
    text = path.read_text(encoding="utf-8")
    assert '"factor"' in text and '"prior_close"' in text


def test_split_share_multiplier_snaps_to_simple_ratios() -> None:
    from src.data import split_share_multiplier

    # 0050 2025-06: 188.65 / 47.16 = 4.0002... -> 4.
    assert split_share_multiplier(_split(date(2025, 6, 18))) == Decimal("4")

    reverse = CorporateActionEvent(
        symbol_value="00632R",
        effective_date=date(2024, 1, 10),
        kind=CorporateActionKind.SPLIT,
        prior_close=Decimal("4.85"),
        reference_price=Decimal("24.30"),
        source="twse_twtcau",
    )
    assert split_share_multiplier(reverse) == Decimal("0.2")


def test_split_share_multiplier_refuses_ambiguous_ratios_and_dividends() -> None:
    from src.data import split_share_multiplier

    ambiguous = CorporateActionEvent(
        symbol_value="0050",
        effective_date=date(2025, 6, 18),
        kind=CorporateActionKind.SPLIT,
        prior_close=Decimal("100"),
        reference_price=Decimal("29"),  # 3.448... snaps to 3 but deviates >1%
        source="twse_twtcau",
    )
    with pytest.raises(MarketDataValidationError, match="refusing to guess"):
        split_share_multiplier(ambiguous)
    with pytest.raises(MarketDataValidationError, match="dividends"):
        split_share_multiplier(_dividend(date(2025, 1, 15)))
