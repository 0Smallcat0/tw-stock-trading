"""FinMind public API client (anonymous tier) for backfill and reconciliation.

Roles per `docs/contracts/DATA_ADAPTER_TWSE.md`:

- `TaiwanStockPrice` supplies pre-2010 history (TWSE RWD's floor) and the
  daily cross-check series; one call can return a symbol's entire life.
- `TaiwanStockTradingDate` is the realized trading-day list used to build
  historical calendars and to adjudicate unscheduled (typhoon) closures.

Anonymous quota is 300 requests/hour — this system uses a handful per day.
A token is deliberately NOT supported here: the product must stay keyless.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from src.data.sessions import session_close_utc, session_open_utc
from src.data.types import MarketDataError, MarketDataValidationError
from src.domain import Candle, Symbol, Timeframe
from src.tw_public_hosts import FINMIND_API_BASE_URL

FINMIND_SOURCE = "finmind_public"

_DAILY_TIMEFRAME = Timeframe("1d")


class FinMindPublicClient:
    """Minimal anonymous FinMind v4 data client."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        api_base_url: str = FINMIND_API_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(timeout=timeout_seconds)
        self._api_base_url = api_base_url.rstrip("/")

    def __enter__(self) -> FinMindPublicClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def fetch_daily_candles(self, *, symbol: Symbol, start: date, end: date) -> tuple[Candle, ...]:
        """Fetch raw daily OHLCV (volume in shares) for ``[start, end]``."""

        payload = self._get_json(
            dataset="TaiwanStockPrice",
            data_id=symbol.value,
            start=start,
            end=end,
        )
        return parse_taiwan_stock_price_payload(payload, symbol=symbol)

    def fetch_trading_dates(self, *, start: date, end: date) -> tuple[date, ...]:
        """Fetch the realized TWSE trading-day list for ``[start, end]``."""

        payload = self._get_json(
            dataset="TaiwanStockTradingDate",
            data_id=None,
            start=start,
            end=end,
        )
        return parse_trading_date_payload(payload)

    def _get_json(self, *, dataset: str, data_id: str | None, start: date, end: date) -> object:
        params: dict[str, str] = {
            "dataset": dataset,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        if data_id is not None:
            params["data_id"] = data_id
        url = f"{self._api_base_url}/api/v4/data"
        try:
            response = self._http_client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"FinMind request failed: {dataset} ({exc})"
            raise MarketDataError(msg) from exc
        return cast(object, response.json())


def parse_taiwan_stock_price_payload(payload: object, *, symbol: Symbol) -> tuple[Candle, ...]:
    """Parse a TaiwanStockPrice payload into daily candles."""

    rows = _require_data_rows(payload, "TaiwanStockPrice")
    candles: list[Candle] = []
    for row in rows:
        stock_id = str(row.get("stock_id", "")).strip()
        if stock_id != symbol.value:
            msg = f"TaiwanStockPrice row stock_id {stock_id!r} != requested {symbol.value!r}"
            raise MarketDataValidationError(msg)
        trading_date = date.fromisoformat(str(row.get("date")))
        open_price = _finmind_decimal(row.get("open"), "open")
        high_price = _finmind_decimal(row.get("max"), "max")
        low_price = _finmind_decimal(row.get("min"), "min")
        close_price = _finmind_decimal(row.get("close"), "close")
        if min(open_price, high_price, low_price, close_price) <= Decimal("0"):
            # Zero-price rows are no-trade artifacts; skip like TWSE "--".
            continue
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=_DAILY_TIMEFRAME,
                open_time=session_open_utc(trading_date),
                close_time=session_close_utc(trading_date),
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=_finmind_decimal(row.get("Trading_Volume"), "Trading_Volume"),
                is_closed=True,
                trading_date=trading_date,
            )
        )
    return tuple(sorted(candles, key=lambda candle: candle.open_time))


def parse_trading_date_payload(payload: object) -> tuple[date, ...]:
    """Parse a TaiwanStockTradingDate payload into an ascending date tuple."""

    rows = _require_data_rows(payload, "TaiwanStockTradingDate")
    dates = sorted(date.fromisoformat(str(row.get("date"))) for row in rows)
    return tuple(dates)


def _require_data_rows(payload: object, dataset: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(payload, Mapping):
        msg = f"{dataset} payload must be a JSON object"
        raise MarketDataValidationError(msg)
    mapping = cast(Mapping[str, object], payload)
    status = mapping.get("status")
    if status != 200:
        msg = f"{dataset} returned status={status!r} msg={mapping.get('msg')!r}"
        raise MarketDataValidationError(msg)
    raw_data = mapping.get("data")
    if not isinstance(raw_data, Sequence) or isinstance(raw_data, (str, bytes)):
        msg = f"{dataset} data must be a list"
        raise MarketDataValidationError(msg)
    rows: list[Mapping[str, object]] = []
    for item in cast(Sequence[object], raw_data):
        if not isinstance(item, Mapping):
            msg = f"{dataset} rows must be objects"
            raise MarketDataValidationError(msg)
        rows.append(cast(Mapping[str, object], item))
    return tuple(rows)


def _finmind_decimal(value: object, label: str) -> Decimal:
    if value is None:
        msg = f"{label} is missing"
        raise MarketDataValidationError(msg)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        msg = f"{label} is not numeric: {value!r}"
        raise MarketDataValidationError(msg) from exc
