"""Binance Spot public market data client and parsers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx
from websockets.asyncio.client import connect

from src.binance_public_hosts import (
    BINANCE_REST_BASE_URL,
    BINANCE_WS_BASE_URL,
)
from src.data.quality import timeframe_delta
from src.data.types import (
    BookTickerSnapshot,
    DepthLevel,
    DepthSnapshot,
    MarketDataError,
    MarketDataValidationError,
    SymbolFilters,
    UniverseEligibilityMetrics,
    UniverseSelectionRules,
    UniverseSnapshot,
)
from src.domain import Candle, Symbol, Timeframe

BINANCE_SOURCE = "binance_spot_public"

_COMMON_QUOTE_ASSETS = ("USDT", "FDUSD", "USDC", "TUSD", "BUSD", "BTC", "ETH", "BNB")
_EXCLUDED_STABLE_BASE_ASSETS = frozenset({"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP"})
_EXCLUDED_FIAT_PROXY_BASE_ASSETS = frozenset(
    {"AUD", "BRL", "EUR", "GBP", "NGN", "RUB", "TRY", "UAH", "ZAR"}
)
_LEVERAGED_TOKEN_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


class BinanceSpotPublicClient:
    """Minimal public-data client for Binance Spot REST and kline streams."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        rest_base_url: str = BINANCE_REST_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=rest_base_url,
            timeout=timeout_seconds,
        )

    async def __aenter__(self) -> BinanceSpotPublicClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned HTTP client."""

        if self._owns_http_client:
            await self._http_client.aclose()

    async def fetch_historical_candles(
        self,
        *,
        symbol: Symbol,
        timeframe: Timeframe,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 1000,
        received_at: datetime | None = None,
        closed_only: bool = True,
    ) -> tuple[Candle, ...]:
        """Fetch public Binance Spot klines and return parsed candle objects."""

        if limit <= 0 or limit > 1000:
            msg = "limit must be between 1 and 1000"
            raise MarketDataValidationError(msg)
        params: dict[str, str | int] = {
            "symbol": symbol.value,
            "interval": timeframe.value,
            "limit": limit,
        }
        if start_time is not None:
            _require_utc("start_time", start_time)
            params["startTime"] = _to_milliseconds(start_time)
        if end_time is not None:
            _require_utc("end_time", end_time)
            params["endTime"] = _to_milliseconds(end_time)

        response = await self._http_client.get("/api/v3/klines", params=params)
        response.raise_for_status()
        payload = cast(object, response.json())
        rows = _expect_sequence(payload, "klines response")
        parsed_received_at = received_at or datetime.now(UTC)
        candles = parse_rest_kline_rows(
            rows,
            symbol=symbol,
            timeframe=timeframe,
            received_at=parsed_received_at,
        )
        if closed_only:
            return tuple(candle for candle in candles if candle.is_closed)
        return candles

    async def fetch_historical_candles_range(
        self,
        *,
        symbol: Symbol,
        timeframe: Timeframe,
        start_time: datetime,
        received_at: datetime | None = None,
        max_requests: int = 20,
    ) -> tuple[Candle, ...]:
        """Fetch the full closed-candle history from start_time via pagination.

        Binance caps klines at 1000 per request; this walks forward request by
        request, deduplicates on open_time, and stops when the exchange has no
        newer closed candles.
        """

        _require_utc("start_time", start_time)
        if max_requests <= 0:
            msg = "max_requests must be positive"
            raise MarketDataValidationError(msg)

        collected: dict[datetime, Candle] = {}
        cursor = start_time
        for _ in range(max_requests):
            batch = await self.fetch_historical_candles(
                symbol=symbol,
                timeframe=timeframe,
                start_time=cursor,
                limit=1000,
                received_at=received_at,
                closed_only=True,
            )
            new_candles = [candle for candle in batch if candle.open_time not in collected]
            for candle in new_candles:
                collected[candle.open_time] = candle
            if not new_candles or len(batch) < 1000:
                break
            cursor = max(candle.open_time for candle in new_candles) + timeframe_delta(timeframe)
        return tuple(sorted(collected.values(), key=lambda candle: candle.open_time))

    async def fetch_symbol_filters(
        self,
        symbols: Iterable[str] | None = None,
    ) -> tuple[SymbolFilters, ...]:
        """Fetch public exchangeInfo and parse the symbol filter subset needed by the MVP."""

        params: dict[str, str] = {"showPermissionSets": "false"}
        symbol_tuple = tuple(symbols or ())
        if len(symbol_tuple) == 1:
            params["symbol"] = symbol_tuple[0]
        elif len(symbol_tuple) > 1:
            # Binance rejects the query when the JSON list contains spaces.
            params["symbols"] = json.dumps(symbol_tuple, separators=(",", ":"))

        response = await self._http_client.get("/api/v3/exchangeInfo", params=params)
        response.raise_for_status()
        payload = cast(object, response.json())
        return parse_exchange_info_symbol_filters(payload)

    async def fetch_book_tickers(
        self,
        symbols: Iterable[str] | None = None,
        *,
        captured_at: datetime | None = None,
    ) -> tuple[BookTickerSnapshot, ...]:
        """Fetch public best bid/ask snapshots from Binance Spot."""

        params = _symbols_params(symbols)
        response = await self._http_client.get("/api/v3/ticker/bookTicker", params=params)
        response.raise_for_status()
        payload = cast(object, response.json())
        return parse_book_ticker_payload(payload, captured_at=captured_at or datetime.now(UTC))

    async def fetch_depth_snapshot(
        self,
        *,
        symbol: Symbol,
        limit: int = 100,
        captured_at: datetime | None = None,
    ) -> DepthSnapshot:
        """Fetch a public order-book depth snapshot for one Binance Spot symbol."""

        if limit <= 0 or limit > 5000:
            msg = "limit must be between 1 and 5000"
            raise MarketDataValidationError(msg)
        response = await self._http_client.get(
            "/api/v3/depth",
            params={"symbol": symbol.value, "limit": limit},
        )
        response.raise_for_status()
        payload = cast(object, response.json())
        return parse_depth_snapshot_payload(
            payload,
            symbol=symbol,
            captured_at=captured_at or datetime.now(UTC),
        )

    async def stream_closed_candles(
        self,
        *,
        symbols: Sequence[str],
        timeframe: Timeframe,
    ) -> AsyncIterator[Candle]:
        """Yield only closed public kline events from Binance's WebSocket stream."""

        url = build_closed_kline_stream_url(symbols, timeframe)
        async with connect(url) as websocket:
            async for raw_message in websocket:
                message = (
                    raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
                )
                candle = parse_runtime_closed_kline_message(message)
                if candle is not None:
                    yield candle


def parse_rest_kline_rows(
    rows: Sequence[object],
    *,
    symbol: Symbol,
    timeframe: Timeframe,
    received_at: datetime,
) -> tuple[Candle, ...]:
    """Parse Binance REST kline rows into UTC-aware domain candles."""

    _require_utc("received_at", received_at)
    candles: list[Candle] = []
    for row in rows:
        values = _expect_sequence(row, "kline row")
        if len(values) < 7:
            msg = "kline row must contain at least 7 fields"
            raise MarketDataError(msg)
        open_time = _datetime_from_milliseconds(values[0], "kline open time")
        close_time = _datetime_from_milliseconds(values[6], "kline close time")
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open_price=_decimal_from(values[1], "open price"),
                high_price=_decimal_from(values[2], "high price"),
                low_price=_decimal_from(values[3], "low price"),
                close_price=_decimal_from(values[4], "close price"),
                volume=_decimal_from(values[5], "volume"),
                is_closed=close_time < received_at,
            )
        )
    return tuple(candles)


def parse_runtime_closed_kline_message(message: str) -> Candle | None:
    """Parse a Binance WebSocket kline message, returning None for still-open updates."""

    payload = cast(object, json.loads(message))
    root = _expect_mapping(payload, "websocket payload")
    data_payload = root.get("data", root)
    data = _expect_mapping(data_payload, "websocket data")
    if data.get("e") != "kline":
        msg = "websocket payload is not a kline event"
        raise MarketDataError(msg)
    kline = _expect_mapping(data.get("k"), "kline payload")
    if kline.get("x") is not True:
        return None

    native_symbol = _string_from(kline.get("s", data.get("s")), "symbol")
    return Candle(
        symbol=symbol_from_binance_native(native_symbol),
        timeframe=Timeframe(_string_from(kline.get("i"), "interval")),
        open_time=_datetime_from_milliseconds(kline.get("t"), "kline open time"),
        close_time=_datetime_from_milliseconds(kline.get("T"), "kline close time"),
        open_price=_decimal_from(kline.get("o"), "open price"),
        high_price=_decimal_from(kline.get("h"), "high price"),
        low_price=_decimal_from(kline.get("l"), "low price"),
        close_price=_decimal_from(kline.get("c"), "close price"),
        volume=_decimal_from(kline.get("v"), "volume"),
        is_closed=True,
    )


def parse_exchange_info_symbol_filters(payload: object) -> tuple[SymbolFilters, ...]:
    """Parse the public exchangeInfo symbol filter payload."""

    root = _expect_mapping(payload, "exchangeInfo payload")
    symbols_payload = _expect_sequence(root.get("symbols"), "exchangeInfo symbols")
    parsed: list[SymbolFilters] = []
    for symbol_payload in symbols_payload:
        symbol_mapping = _expect_mapping(symbol_payload, "exchangeInfo symbol")
        filters_payload = _expect_sequence(symbol_mapping.get("filters", ()), "symbol filters")
        filters_by_type = _filters_by_type(filters_payload)

        price_filter = filters_by_type.get("PRICE_FILTER", {})
        lot_size = filters_by_type.get("LOT_SIZE", {})
        min_notional_filter = filters_by_type.get("MIN_NOTIONAL", {})
        notional_filter = filters_by_type.get("NOTIONAL", {})
        min_notional_source = min_notional_filter or notional_filter

        parsed.append(
            SymbolFilters(
                symbol=Symbol(
                    value=_string_from(symbol_mapping.get("symbol"), "symbol"),
                    base_asset=_string_from(symbol_mapping.get("baseAsset"), "baseAsset"),
                    quote_asset=_string_from(symbol_mapping.get("quoteAsset"), "quoteAsset"),
                ),
                status=_string_from(symbol_mapping.get("status"), "status"),
                is_spot_trading_allowed=_bool_from(
                    symbol_mapping.get("isSpotTradingAllowed", True),
                    "isSpotTradingAllowed",
                ),
                price_tick_size=_optional_decimal_from(price_filter.get("tickSize"), "tickSize"),
                quantity_step_size=_optional_decimal_from(lot_size.get("stepSize"), "stepSize"),
                min_quantity=_optional_decimal_from(lot_size.get("minQty"), "minQty"),
                min_notional=_optional_decimal_from(
                    min_notional_source.get("minNotional"),
                    "minNotional",
                ),
                raw_filter_types=tuple(filters_by_type),
            )
        )
    return tuple(parsed)


def parse_book_ticker_payload(
    payload: object,
    *,
    captured_at: datetime,
) -> tuple[BookTickerSnapshot, ...]:
    """Parse Binance's public bookTicker response into bid/ask snapshots."""

    _require_utc("captured_at", captured_at)
    rows = (
        payload
        if isinstance(payload, Sequence) and not isinstance(payload, str | bytes)
        else [payload]
    )
    parsed: list[BookTickerSnapshot] = []
    for row in rows:
        mapping = _expect_mapping(row, "bookTicker row")
        parsed.append(
            BookTickerSnapshot(
                symbol=symbol_from_binance_native(_string_from(mapping.get("symbol"), "symbol")),
                bid_price=_decimal_from(mapping.get("bidPrice"), "bidPrice"),
                bid_quantity=_decimal_from(mapping.get("bidQty"), "bidQty"),
                ask_price=_decimal_from(mapping.get("askPrice"), "askPrice"),
                ask_quantity=_decimal_from(mapping.get("askQty"), "askQty"),
                captured_at=captured_at,
            )
        )
    return tuple(parsed)


def parse_depth_snapshot_payload(
    payload: object,
    *,
    symbol: Symbol,
    captured_at: datetime,
) -> DepthSnapshot:
    """Parse Binance's public depth response into a typed depth snapshot."""

    _require_utc("captured_at", captured_at)
    root = _expect_mapping(payload, "depth payload")
    bids = _parse_depth_levels(root.get("bids"), "bids")
    asks = _parse_depth_levels(root.get("asks"), "asks")
    return DepthSnapshot(
        symbol=symbol,
        last_update_id=_int_from(root.get("lastUpdateId"), "lastUpdateId"),
        bids=bids,
        asks=asks,
        captured_at=captured_at,
    )


def build_universe_snapshot(
    symbol_filters: Iterable[SymbolFilters],
    *,
    metrics: Iterable[UniverseEligibilityMetrics],
    created_at: datetime,
    rules: UniverseSelectionRules | None = None,
) -> UniverseSnapshot:
    """Build the MVP universe from public filters and closed-candle metrics."""

    selection_rules = rules or UniverseSelectionRules()
    metrics_by_symbol = _metrics_by_symbol(metrics)
    eligible_symbols_with_metrics = [
        (filters.symbol, symbol_metrics)
        for filters in symbol_filters
        if (symbol_metrics := metrics_by_symbol.get(filters.symbol.value)) is not None
        and _is_universe_eligible(filters, symbol_metrics, selection_rules)
    ]
    eligible_symbols = tuple(
        symbol
        for symbol, _symbol_metrics in sorted(
            eligible_symbols_with_metrics,
            key=lambda item: (-item[1].recent_quote_volume, item[0].value),
        )
    )
    return UniverseSnapshot(
        symbols=eligible_symbols,
        created_at=created_at,
        source=BINANCE_SOURCE,
    )


def build_universe_eligibility_metrics(
    candles: Iterable[Candle],
) -> tuple[UniverseEligibilityMetrics, ...]:
    """Summarize closed 15m public candles for universe eligibility checks."""

    metrics_by_symbol: dict[str, tuple[Symbol, int, Decimal]] = {}
    for candle in candles:
        if not candle.is_closed:
            msg = "universe metrics require closed candles"
            raise MarketDataValidationError(msg)
        if candle.timeframe.value != "15m":
            msg = "universe metrics require 15m candles"
            raise MarketDataValidationError(msg)
        previous = metrics_by_symbol.get(candle.symbol.value)
        symbol = candle.symbol
        count = 0
        quote_volume = Decimal("0")
        if previous is not None:
            symbol, count, quote_volume = previous
        metrics_by_symbol[candle.symbol.value] = (
            symbol,
            count + 1,
            quote_volume + (candle.close_price * candle.volume),
        )
    return tuple(
        UniverseEligibilityMetrics(
            symbol=symbol,
            closed_15m_candle_count=count,
            recent_quote_volume=quote_volume,
        )
        for _symbol_value, (symbol, count, quote_volume) in sorted(metrics_by_symbol.items())
    )


def build_closed_kline_stream_url(
    symbols: Sequence[str],
    timeframe: Timeframe,
    *,
    base_url: str = BINANCE_WS_BASE_URL,
) -> str:
    """Build Binance's public combined-stream URL for kline updates."""

    if not symbols:
        msg = "symbols must not be empty"
        raise MarketDataValidationError(msg)
    streams = "/".join(f"{symbol.lower()}@kline_{timeframe.value}" for symbol in symbols)
    return f"{base_url}?streams={streams}"


def _symbols_params(symbols: Iterable[str] | None) -> dict[str, str]:
    symbol_tuple = tuple(symbols or ())
    if len(symbol_tuple) == 1:
        return {"symbol": symbol_tuple[0]}
    if len(symbol_tuple) > 1:
        return {"symbols": json.dumps(symbol_tuple)}
    return {}


def _metrics_by_symbol(
    metrics: Iterable[UniverseEligibilityMetrics],
) -> dict[str, UniverseEligibilityMetrics]:
    metrics_by_symbol: dict[str, UniverseEligibilityMetrics] = {}
    for symbol_metrics in metrics:
        symbol_value = symbol_metrics.symbol.value
        if symbol_value in metrics_by_symbol:
            msg = f"duplicate universe metrics symbol: {symbol_value}"
            raise MarketDataValidationError(msg)
        metrics_by_symbol[symbol_value] = symbol_metrics
    return metrics_by_symbol


def _is_universe_eligible(
    filters: SymbolFilters,
    metrics: UniverseEligibilityMetrics,
    rules: UniverseSelectionRules,
) -> bool:
    base_asset = filters.symbol.base_asset
    return (
        filters.status == "TRADING"
        and filters.is_spot_trading_allowed
        and filters.symbol.quote_asset == rules.quote_asset
        and base_asset not in _EXCLUDED_STABLE_BASE_ASSETS
        and base_asset not in _EXCLUDED_FIAT_PROXY_BASE_ASSETS
        and not base_asset.endswith(_LEVERAGED_TOKEN_SUFFIXES)
        and metrics.closed_15m_candle_count >= rules.min_closed_15m_candles
        and metrics.recent_quote_volume >= rules.min_recent_quote_volume
    )


def symbol_from_binance_native(native_symbol: str) -> Symbol:
    """Create a Symbol from a Binance-native pair, using common quote suffixes."""

    native = _string_from(native_symbol, "symbol")
    for quote_asset in _COMMON_QUOTE_ASSETS:
        if native.endswith(quote_asset) and native != quote_asset:
            base_asset = native[: -len(quote_asset)]
            if base_asset:
                return Symbol(value=native, base_asset=base_asset, quote_asset=quote_asset)
    msg = f"cannot infer base/quote assets for symbol: {native}"
    raise MarketDataValidationError(msg)


def _parse_depth_levels(payload: object, name: str) -> tuple[DepthLevel, ...]:
    rows = _expect_sequence(payload, name)
    parsed: list[DepthLevel] = []
    for row in rows:
        values = _expect_sequence(row, f"{name} level")
        if len(values) < 2:
            msg = f"{name} level must include price and quantity"
            raise MarketDataError(msg)
        parsed.append(
            DepthLevel(
                price=_decimal_from(values[0], f"{name} price"),
                quantity=_decimal_from(values[1], f"{name} quantity"),
            )
        )
    return tuple(parsed)


def _filters_by_type(filters_payload: Sequence[object]) -> dict[str, Mapping[str, object]]:
    filters_by_type: dict[str, Mapping[str, object]] = {}
    for filter_payload in filters_payload:
        filter_mapping = _expect_mapping(filter_payload, "symbol filter")
        filter_type = _string_from(filter_mapping.get("filterType"), "filterType")
        filters_by_type[filter_type] = filter_mapping
    return filters_by_type


def _expect_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        msg = f"{name} must be an object"
        raise MarketDataError(msg)
    return cast(Mapping[str, object], value)


def _expect_sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        msg = f"{name} must be a sequence"
        raise MarketDataError(msg)
    return cast(Sequence[object], value)


def _string_from(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{name} must be a non-empty string"
        raise MarketDataError(msg)
    return value


def _bool_from(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{name} must be a boolean"
        raise MarketDataError(msg)
    return value


def _int_from(value: object, name: str) -> int:
    if not isinstance(value, int):
        msg = f"{name} must be an integer"
        raise MarketDataError(msg)
    return value


def _decimal_from(value: object, name: str) -> Decimal:
    if not isinstance(value, str | int):
        msg = f"{name} must be a decimal string"
        raise MarketDataError(msg)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        msg = f"{name} must be a valid decimal"
        raise MarketDataError(msg) from exc


def _optional_decimal_from(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal_from(value, name)


def _datetime_from_milliseconds(value: object, name: str) -> datetime:
    if not isinstance(value, int):
        msg = f"{name} must be milliseconds as an integer"
        raise MarketDataError(msg)
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _to_milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise MarketDataValidationError(msg)
