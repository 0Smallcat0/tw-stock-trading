"""TWSE payload parser tests using recorded-format fixtures.

Row shapes mirror the endpoint formats verified by fetch on 2026-07-03
(docs/contracts/DATA_ADAPTER_TWSE.md), including the 0050 2025-06 split
month and the comma/ROC/no-trade parsing traps.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from src.data import (
    CorporateActionKind,
    MarketDataValidationError,
    TwsePublicClient,
    parse_ex_right_payload,
    parse_reduction_payload,
    parse_split_payload,
    parse_stock_day_payload,
)
from src.domain import Symbol

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")

_STOCK_DAY_FIELDS = [
    "日期",
    "成交股數",
    "成交金額",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
    "漲跌價差",
    "成交筆數",
    "註記",
]


def _stock_day_payload() -> dict[str, object]:
    return {
        "stat": "OK",
        "date": "20250601",
        "title": "114年06月 0050 元大台灣50 各日成交資訊",
        "fields": _STOCK_DAY_FIELDS,
        "data": [
            [
                "114/06/10",
                "12,345,678",
                "2,329,000,000",
                "189.00",
                "189.30",
                "188.20",
                "188.65",
                "-0.35",
                "15,432",
                "",
            ],
            ["114/06/17", "0", "0", "--", "--", "--", "--", "X", "0", ""],
            [
                "114/06/18",
                "252,600,000",
                "11,900,000,000",
                "47.20",
                "47.44",
                "46.88",
                "47.16",
                "X",
                "156,361",
                "**",
            ],
        ],
        "notes": ["「**」符號說明：辦理分割、反分割……恢復買賣日。"],
    }


def test_stock_day_rows_parse_with_roc_dates_commas_and_sessions() -> None:
    candles = parse_stock_day_payload(_stock_day_payload(), symbol=_SYMBOL)

    assert len(candles) == 2  # the "--" no-trade row is skipped
    first, second = candles
    assert first.trading_date == date(2025, 6, 10)
    assert first.close_price == Decimal("188.65")
    assert first.volume == Decimal("12345678")
    # 09:00/13:30 Taipei == 01:00/05:30 UTC
    assert first.open_time == datetime(2025, 6, 10, 1, 0, tzinfo=UTC)
    assert first.close_time == datetime(2025, 6, 10, 5, 30, tzinfo=UTC)
    assert second.trading_date == date(2025, 6, 18)
    assert second.close_price == Decimal("47.16")
    assert second.is_closed is True


def test_stock_day_stat_error_raises_with_message() -> None:
    payload = {"stat": "查詢日期小於99年1月4日，請重新查詢!", "data": []}
    with pytest.raises(MarketDataValidationError, match="99年1月4日"):
        parse_stock_day_payload(payload, symbol=_SYMBOL)


def test_stock_day_missing_field_names_fail_loudly() -> None:
    payload = _stock_day_payload()
    payload["fields"] = ["日期", "收盤價"]
    with pytest.raises(MarketDataValidationError, match="missing"):
        parse_stock_day_payload(payload, symbol=_SYMBOL)


def _ex_right_payload_modern() -> dict[str, object]:
    return {
        "stat": "OK",
        "fields": [
            "資料日期",
            "股票代號",
            "股票名稱",
            "除權息前收盤價",
            "除權息參考價",
            "權值+息值",
            "權/息",
            "漲停價格",
            "跌停價格",
            "開盤競價基準",
            "減除股利參考價",
        ],
        "data": [
            [
                "115年01月22日",
                "0050",
                "元大台灣50",
                "104.00",
                "103.00",
                "1.00",
                "息",
                "113.30",
                "92.70",
                "103.00",
                "103.00",
            ]
        ],
    }


def test_ex_right_modern_schema_parses_to_dividend_event() -> None:
    events = parse_ex_right_payload(_ex_right_payload_modern())

    assert len(events) == 1
    event = events[0]
    assert event.symbol_value == "0050"
    assert event.effective_date == date(2026, 1, 22)
    assert event.kind is CorporateActionKind.DIVIDEND
    assert event.factor == Decimal("103.00") / Decimal("104.00")
    assert event.description == "息"


def test_ex_right_pre_2011_schema_with_split_value_columns_parses() -> None:
    payload = {
        "stat": "OK",
        "fields": [
            "資料日期",
            "股票代號",
            "股票名稱",
            "除權息前收盤價",
            "除權息參考價",
            "權值",
            "息值",
            "權值+息值",
            "權/息",
            "漲停價格",
            "跌停價格",
            "開盤競價基準",
            "減除股利參考價",
            "詳細資料",
            "最近一次申報資料 季別/日期",
            "最近一次申報每股 (單位)淨值",
            "最近一次申報每股 (單位)盈餘",
        ],
        "data": [
            [
                "99/07/26",
                "0050",
                "元大台灣50",
                "54.30",
                "52.10",
                "0.00",
                "2.20",
                "2.20",
                "息",
                "55.70",
                "48.50",
                "52.10",
                "52.10",
                "",
                "",
                "",
                "",
            ]
        ],
    }
    events = parse_ex_right_payload(payload)

    assert events[0].effective_date == date(2010, 7, 26)
    assert events[0].factor == Decimal("52.10") / Decimal("54.30")


def test_split_payload_parses_verified_0050_row() -> None:
    # Field names verified by live fetch 2026-07-03 (code column is ETF代號).
    payload = {
        "stat": "OK",
        "fields": [
            "恢復買賣日期",
            "ETF代號",
            "名稱",
            "分割(反分割)",
            "停止買賣前收盤價格",
            "恢復買賣參考價",
            "漲停價格",
            "跌停價格",
            "開盤競價基準",
        ],
        "data": [
            [
                "114/06/18",
                "0050",
                "元大台灣50",
                "分割",
                "188.65",
                "47.16",
                "51.85",
                "42.45",
                "47.16",
            ]
        ],
    }
    events = parse_split_payload(payload)

    event = events[0]
    assert event.kind is CorporateActionKind.SPLIT
    assert event.effective_date == date(2025, 6, 18)
    # The official reference ratio, NOT an assumed exact 1/4.
    assert event.factor == Decimal("47.16") / Decimal("188.65")


def test_reduction_payload_parses() -> None:
    payload = {
        "stat": "OK",
        "fields": [
            "恢復買賣日期",
            "股票代號",
            "名稱",
            "停止買賣前收盤價格",
            "恢復買賣參考價格",
            "漲停價格",
            "跌停價格",
            "開始交易基準價",
            "減資原因",
        ],
        "data": [
            ["113/09/12", "2002", "中鋼", "21.00", "23.50", "25.85", "21.15", "23.50", "彌補虧損"]
        ],
    }
    events = parse_reduction_payload(payload)

    assert events[0].kind is CorporateActionKind.CAPITAL_REDUCTION
    assert events[0].factor == Decimal("23.50") / Decimal("21.00")


def test_client_throttles_sequential_requests() -> None:
    sleeps: list[float] = []
    clock = iter([0.0, 0.0, 0.5, 3.5])

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_stock_day_payload())

    client = TwsePublicClient(
        http_client=httpx.Client(transport=httpx.MockTransport(transport_handler)),
        min_request_interval_seconds=3.0,
        sleep=sleeps.append,
        monotonic=lambda: next(clock),
    )
    client.fetch_month_daily_candles(symbol=_SYMBOL, month=date(2025, 6, 1))
    client.fetch_month_daily_candles(symbol=_SYMBOL, month=date(2025, 7, 1))

    assert sleeps == [pytest.approx(2.5)]


def test_no_data_windows_return_empty_for_corporate_action_reports() -> None:
    payload = {"stat": "查無資料！", "data": []}

    assert parse_split_payload(payload) == ()
    assert parse_reduction_payload(payload) == ()
    assert parse_ex_right_payload(payload) == ()
