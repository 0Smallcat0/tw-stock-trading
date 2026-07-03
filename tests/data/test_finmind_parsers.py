from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.data import (
    MarketDataValidationError,
    parse_taiwan_stock_price_payload,
    parse_trading_date_payload,
)
from src.domain import Symbol

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def _price_payload() -> dict[str, object]:
    return {
        "msg": "success",
        "status": 200,
        "data": [
            {
                "date": "2003-06-30",
                "stock_id": "0050",
                "Trading_Volume": 16487000,
                "Trading_money": 611408380,
                "open": 37.08,
                "max": 37.31,
                "min": 36.7,
                "close": 37.08,
                "spread": 0.0,
                "Trading_turnover": 4741,
            },
            {
                "date": "2003-07-01",
                "stock_id": "0050",
                "Trading_Volume": 0,
                "Trading_money": 0,
                "open": 0,
                "max": 0,
                "min": 0,
                "close": 0,
                "spread": 0.0,
                "Trading_turnover": 0,
            },
        ],
    }


def test_taiwan_stock_price_rows_parse_and_zero_rows_are_skipped() -> None:
    candles = parse_taiwan_stock_price_payload(_price_payload(), symbol=_SYMBOL)

    assert len(candles) == 1
    candle = candles[0]
    assert candle.trading_date == date(2003, 6, 30)
    assert candle.close_price == Decimal("37.08")
    assert candle.volume == Decimal("16487000")


def test_wrong_stock_id_is_rejected() -> None:
    payload = _price_payload()
    other = Symbol(value="2330", base_asset="2330", quote_asset="TWD")
    with pytest.raises(MarketDataValidationError, match="stock_id"):
        parse_taiwan_stock_price_payload(payload, symbol=other)


def test_non_200_status_is_rejected() -> None:
    payload = {"msg": "Your level is free", "status": 400, "data": []}
    with pytest.raises(MarketDataValidationError, match="status"):
        parse_taiwan_stock_price_payload(payload, symbol=_SYMBOL)


def test_trading_dates_parse_sorted() -> None:
    payload = {
        "msg": "success",
        "status": 200,
        "data": [{"date": "2026-07-03"}, {"date": "2026-07-01"}, {"date": "2026-07-02"}],
    }
    assert parse_trading_date_payload(payload) == (
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
    )
