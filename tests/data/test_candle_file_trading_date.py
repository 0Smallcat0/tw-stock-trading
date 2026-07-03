from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.data import read_candles_jsonl, write_candles_jsonl
from src.domain import Candle, Symbol, Timeframe


def _candle(day: int, *, with_trading_date: bool) -> Candle:
    open_time = datetime(2026, 7, day, 1, 0, tzinfo=UTC)
    return Candle(
        symbol=Symbol(value="0050", base_asset="0050", quote_asset="TWD"),
        timeframe=Timeframe("1d"),
        open_time=open_time,
        close_time=open_time + timedelta(hours=4, minutes=30),
        open_price=Decimal("108.00"),
        high_price=Decimal("108.60"),
        low_price=Decimal("107.60"),
        close_price=Decimal("108.35"),
        volume=Decimal("1000"),
        is_closed=True,
        trading_date=date(2026, 7, day) if with_trading_date else None,
    )


def test_trading_date_round_trips_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "0050_1d.jsonl"
    write_candles_jsonl((_candle(1, with_trading_date=True),), path)

    restored = read_candles_jsonl(path)

    assert restored[0].trading_date == date(2026, 7, 1)
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["trading_date"] == "2026-07-01"


def test_rows_without_trading_date_still_load(tmp_path: Path) -> None:
    path = tmp_path / "0050_1d.jsonl"
    write_candles_jsonl((_candle(2, with_trading_date=False),), path)

    restored = read_candles_jsonl(path)

    assert restored[0].trading_date is None
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "trading_date" not in row
