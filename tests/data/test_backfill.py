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
    read_candles_jsonl,
    reconcile_and_splice,
    session_close_utc,
    session_open_utc,
    write_backfill_artifacts,
)
from src.domain import Candle, Symbol, Timeframe

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def _candle(trading_date: date, close: str, volume: str = "1000") -> Candle:
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
        volume=Decimal(volume),
        is_closed=True,
        trading_date=trading_date,
    )


def test_splice_prefers_twse_rows_and_keeps_finmind_tail() -> None:
    finmind = (
        _candle(date(2009, 12, 31), "50", volume="111"),
        _candle(date(2010, 1, 4), "51", volume="222"),
    )
    twse = (_candle(date(2010, 1, 4), "51", volume="999"),)

    spliced = reconcile_and_splice(finmind_candles=finmind, twse_candles=twse)

    assert spliced.overlap_days == 1
    assert spliced.finmind_only_days == 1
    assert [candle.trading_date for candle in spliced.candles] == [
        date(2009, 12, 31),
        date(2010, 1, 4),
    ]
    assert spliced.candles[1].volume == Decimal("999")  # official row wins


def test_close_mismatch_refuses_to_splice() -> None:
    finmind = (_candle(date(2010, 1, 4), "51.00"),)
    twse = (_candle(date(2010, 1, 4), "51.05"),)

    with pytest.raises(MarketDataValidationError, match="reconciliation failed"):
        reconcile_and_splice(finmind_candles=finmind, twse_candles=twse)


def test_backfill_artifacts_handle_split_halt_and_write_dual_series(tmp_path: Path) -> None:
    calendar = TradingCalendar(covered_years=frozenset({2025}), closures=frozenset())
    candles = (
        _candle(date(2025, 6, 9), "189"),
        _candle(date(2025, 6, 10), "188.65"),
        _candle(date(2025, 6, 18), "47.16"),
    )
    split = CorporateActionEvent(
        symbol_value="0050",
        effective_date=date(2025, 6, 18),
        kind=CorporateActionKind.SPLIT,
        prior_close=Decimal("188.65"),
        reference_price=Decimal("47.16"),
        source="twse_twtcau",
    )

    artifacts = write_backfill_artifacts(
        symbol=_SYMBOL,
        candles=candles,
        events=(split,),
        calendar=calendar,
        observed_on=date(2025, 6, 18),
        stale_trading_days=1,
        candles_directory=tmp_path,
        adjustments_directory=tmp_path,
        overlap_days=3,
    )

    assert artifacts.candle_count == 3
    raw = read_candles_jsonl(artifacts.raw_path)
    adjusted = read_candles_jsonl(artifacts.adjusted_path)
    assert raw[0].close_price == Decimal("189")
    split_factor = Decimal("47.16") / Decimal("188.65")
    assert adjusted[0].close_price == adjusted_price(Decimal("189"), split_factor)
    assert adjusted[-1].close_price == Decimal("47.16")
    assert artifacts.factors_path.exists()


def test_unexplained_gap_fails_backfill(tmp_path: Path) -> None:
    calendar = TradingCalendar(covered_years=frozenset({2025}), closures=frozenset())
    candles = (_candle(date(2025, 6, 9), "189"), _candle(date(2025, 6, 11), "188"))

    with pytest.raises(MarketDataValidationError, match="quality check failed"):
        write_backfill_artifacts(
            symbol=_SYMBOL,
            candles=candles,
            events=(),
            calendar=calendar,
            observed_on=date(2025, 6, 11),
            stale_trading_days=1,
            candles_directory=tmp_path,
            adjustments_directory=tmp_path,
            overlap_days=2,
        )
