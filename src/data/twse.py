"""TWSE public data client and parsers (keyless).

Endpoint behavior, formats, and traps are contract-documented in
`docs/contracts/DATA_ADAPTER_TWSE.md` and were verified by direct fetch on
2026-07-03. Highlights the code must honor:

- RWD ``STOCK_DAY`` returns one full calendar month per request; history
  floor 2010-01-04 (earlier queries return a ``stat`` error message).
- ROC dates appear in slash form (``115/06/01``, leading space for 2-digit
  years) in RWD rows and CJK form (``114年06月16日``) in TWT49U rows.
- Numbers arrive comma-grouped; the change column may be ``"X"``; a note
  column value ``**`` marks a split/par-change resumption day.
- Rate limits are undocumented but IP bans are real: requests are
  sequential and spaced by ``min_request_interval_seconds``.
- The OpenAPI holiday schedule covers the CURRENT year only.
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from src.data.adjustments import CorporateActionEvent, CorporateActionKind
from src.data.calendar import HolidayEntry, parse_holiday_schedule
from src.data.roc_dates import parse_roc_cjk, parse_roc_slash
from src.data.sessions import session_close_utc, session_open_utc
from src.data.types import MarketDataError, MarketDataValidationError
from src.domain import Candle, Symbol, Timeframe
from src.tw_public_hosts import TWSE_OPENAPI_BASE_URL, TWSE_RWD_BASE_URL

TWSE_SOURCE = "twse_rwd"
TWSE_STOCK_DAY_FLOOR = date(2010, 1, 4)

_DAILY_TIMEFRAME = Timeframe("1d")
_NO_TRADE_MARKERS = frozenset({"", "--", "0", "0.00", "X"})

_STOCK_DAY_DATE_FIELD = "日期"
_STOCK_DAY_REQUIRED_FIELDS = {
    "date": ("日期",),
    "volume": ("成交股數",),
    "open": ("開盤價",),
    "high": ("最高價",),
    "low": ("最低價",),
    "close": ("收盤價",),
}

_EX_RIGHT_REQUIRED_FIELDS = {
    "date": ("資料日期",),
    "code": ("股票代號", "證券代號"),
    "prior_close": ("除權息前收盤價",),
    "reference_price": ("除權息參考價",),
}
_EX_RIGHT_KIND_FIELDS = ("權/息", "權息")

_SPLIT_REQUIRED_FIELDS = {
    "date": ("恢復買賣日期",),
    # Verified live field name is ETF代號 (fetched 2026-07-03).
    "code": ("ETF代號", "證券代號", "股票代號"),
    "prior_close": ("停止買賣前收盤價", "停止買賣前收盤價格"),
    "reference_price": ("恢復買賣參考價", "恢復買賣參考價格"),
}

_REDUCTION_REQUIRED_FIELDS = {
    "date": ("恢復買賣日期",),
    "code": ("股票代號", "證券代號"),
    "prior_close": ("停止買賣前收盤價格", "停止買賣前收盤價"),
    "reference_price": ("恢復買賣參考價格", "恢復買賣參考價", "開始交易基準價"),
}


class TwsePublicClient:
    """Throttled, sequential client for TWSE RWD and OpenAPI endpoints."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        rwd_base_url: str = TWSE_RWD_BASE_URL,
        openapi_base_url: str = TWSE_OPENAPI_BASE_URL,
        timeout_seconds: float = 10.0,
        min_request_interval_seconds: float = 3.0,
        sleep: Callable[[float], None] = time_module.sleep,
        monotonic: Callable[[], float] = time_module.monotonic,
    ) -> None:
        self._owns_http_client = http_client is None
        # Keep-alive is deliberately DISABLED: TWSE's WAF flags long-lived
        # connections after ~75 sequential requests and then serves garbage
        # stat responses on them (verified live 2026-07-03: a failed sweep's
        # next request on a FRESH connection succeeds immediately). One
        # connection per request costs ~a TLS handshake at our 3s cadence.
        self._http_client = http_client or httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": "tw-stock-signal-mvp/0.1 (public daily data; keyless)"},
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=4),
        )
        self._rwd_base_url = rwd_base_url.rstrip("/")
        self._openapi_base_url = openapi_base_url.rstrip("/")
        self._min_interval = min_request_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def __enter__(self) -> TwsePublicClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def fetch_month_daily_candles(self, *, symbol: Symbol, month: date) -> tuple[Candle, ...]:
        """Fetch the full calendar month of daily candles containing ``month``.

        The endpoint returns the whole month containing the request date, so
        any in-month date works — callers near the 2010-01-04 history floor
        must pass a date at or after the floor, never the month's 1st.
        """

        payload = self._get_json(
            f"{self._rwd_base_url}/rwd/zh/afterTrading/STOCK_DAY",
            params={
                "date": month.strftime("%Y%m%d"),
                "stockNo": symbol.value,
                "response": "json",
            },
        )
        return parse_stock_day_payload(payload, symbol=symbol)

    def fetch_daily_candles_range(
        self, *, symbol: Symbol, start: date, end: date
    ) -> tuple[Candle, ...]:
        """Fetch daily candles for ``[start, end]`` via monthly requests."""

        if start > end:
            msg = "start must not be after end"
            raise MarketDataValidationError(msg)
        if start < TWSE_STOCK_DAY_FLOOR:
            msg = (
                f"TWSE STOCK_DAY history begins {TWSE_STOCK_DAY_FLOOR.isoformat()}; "
                "use the FinMind backfill for earlier dates"
            )
            raise MarketDataValidationError(msg)
        candles: list[Candle] = []
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            # Clamp the request date to the range start so the January-2010
            # month is requested via 2010-01-04, not the pre-floor 01-01.
            month_candles = self._fetch_month_with_retry(symbol=symbol, month=max(cursor, start))
            candles.extend(
                candle
                for candle in month_candles
                if candle.trading_date is not None and start <= candle.trading_date <= end
            )
            cursor = _next_month(cursor)
        return tuple(sorted(candles, key=lambda candle: candle.open_time))

    def _fetch_month_with_retry(self, *, symbol: Symbol, month: date) -> tuple[Candle, ...]:
        """Retry STOCK_DAY soft-bans with an escalating penalty-box backoff.

        The afterTrading/STOCK_DAY endpoint family has its OWN request-count
        limiter: ~12-14 sequential requests trip a multi-minute penalty
        during which VALID in-range dates get the pre-floor stat message
        (isolated live 2026-07-03/04 across eight backfill runs; corporate
        -action endpoints never trip it, fresh-session/cookie/connection
        changes do not evade it). Since ``month`` is at or after the history
        floor by construction, a stat error here is the penalty box — the
        final 300s wait is sized to outlast it.
        """

        last_error: MarketDataValidationError | None = None
        for backoff_seconds in (0.0, 12.0, 60.0, 300.0):
            if backoff_seconds:
                self._sleep(backoff_seconds)
            try:
                return self.fetch_month_daily_candles(symbol=symbol, month=month)
            except MarketDataValidationError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def fetch_current_year_holiday_schedule(self) -> tuple[HolidayEntry, ...]:
        """Fetch and classify the CURRENT-YEAR holiday schedule."""

        payload = self._get_json(
            f"{self._openapi_base_url}/v1/holidaySchedule/holidaySchedule", params=None
        )
        return parse_holiday_schedule(payload)

    def fetch_ex_right_results(self, *, start: date, end: date) -> tuple[CorporateActionEvent, ...]:
        """Fetch TWT49U ex-dividend/ex-rights RESULTS for ``[start, end]``."""

        payload = self._get_json(
            f"{self._rwd_base_url}/rwd/zh/exRight/TWT49U",
            params={
                "startDate": start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
                "response": "json",
            },
        )
        return parse_ex_right_payload(payload)

    def fetch_etf_splits(self, *, start: date, end: date) -> tuple[CorporateActionEvent, ...]:
        """Fetch TWTCAU ETF split/reverse-split reference prices."""

        payload = self._get_json(
            f"{self._rwd_base_url}/rwd/zh/split/TWTCAU",
            params={
                "startDate": start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
                "response": "json",
            },
        )
        return parse_split_payload(payload)

    def fetch_capital_reductions(
        self, *, start: date, end: date
    ) -> tuple[CorporateActionEvent, ...]:
        """Fetch TWTAUU capital-reduction reference prices."""

        payload = self._get_json(
            f"{self._rwd_base_url}/rwd/zh/reducation/TWTAUU",
            params={
                "startDate": start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
                "response": "json",
            },
        )
        return parse_reduction_payload(payload)

    def _get_json(self, url: str, *, params: Mapping[str, str] | None) -> object:
        """Throttled GET with bounded retries.

        TWSE is flaky under sustained sequential load (observed live: read
        timeouts and transient garbage stats mid-backfill). GETs on public
        report endpoints are idempotent, so three backoff attempts are safe.
        """

        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            if attempt:
                self._sleep(self._min_interval * 2 * attempt)
            self._throttle()
            # TWSE's WAF tags heavy sessions via cookies and then serves
            # garbage stats to the tagged session (verified live 2026-07-03:
            # long sweeps failed on fresh CONNECTIONS but a fresh cookie-less
            # client succeeded immediately). Stay cookie-less on purpose.
            self._http_client.cookies.clear()
            try:
                response = self._http_client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            return cast(object, response.json())
        msg = f"TWSE request failed after retries: {url} ({last_error})"
        raise MarketDataError(msg) from last_error

    def _throttle(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
        self._last_request_at = self._monotonic()


def parse_stock_day_payload(payload: object, *, symbol: Symbol) -> tuple[Candle, ...]:
    """Parse one RWD STOCK_DAY monthly payload into daily candles.

    Rows whose OHLC carries no-trade markers (``--``) are skipped: the
    security did not trade that day. The 註記 note column is ignored for
    candle values; split resumption facts come from TWTCAU.
    """

    mapping = _require_ok_payload(payload, "STOCK_DAY")
    fields = _string_list(mapping.get("fields"), "STOCK_DAY fields")
    indices = _resolve_field_indices(fields, _STOCK_DAY_REQUIRED_FIELDS, "STOCK_DAY")
    rows = _row_list(mapping.get("data"), "STOCK_DAY data")

    candles: list[Candle] = []
    for row in rows:
        values = _string_row(row)
        raw_prices = {key: values[indices[key]].strip() for key in ("open", "high", "low", "close")}
        if any(raw in _NO_TRADE_MARKERS for raw in raw_prices.values()):
            continue
        trading_date = parse_roc_slash(values[indices["date"]])
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=_DAILY_TIMEFRAME,
                open_time=session_open_utc(trading_date),
                close_time=session_close_utc(trading_date),
                open_price=_tw_decimal(raw_prices["open"], "open"),
                high_price=_tw_decimal(raw_prices["high"], "high"),
                low_price=_tw_decimal(raw_prices["low"], "low"),
                close_price=_tw_decimal(raw_prices["close"], "close"),
                volume=_tw_decimal(values[indices["volume"]], "volume"),
                is_closed=True,
                trading_date=trading_date,
            )
        )
    return tuple(sorted(candles, key=lambda candle: candle.open_time))


def parse_ex_right_payload(payload: object) -> tuple[CorporateActionEvent, ...]:
    """Parse TWT49U rows (handles both the modern and pre-2011 schemas).

    A no-data window (stat contains 查無) is a legitimate empty result for
    corporate-action reports; any other non-OK stat still raises.
    """

    mapping = _ok_payload_or_none(payload, "TWT49U")
    if mapping is None:
        return ()
    fields = _string_list(mapping.get("fields"), "TWT49U fields")
    indices = _resolve_field_indices(fields, _EX_RIGHT_REQUIRED_FIELDS, "TWT49U")
    kind_index = _optional_field_index(fields, _EX_RIGHT_KIND_FIELDS)
    rows = _row_list(mapping.get("data"), "TWT49U data")

    events: list[CorporateActionEvent] = []
    for row in rows:
        values = _string_row(row)
        description = values[kind_index].strip() if kind_index is not None else ""
        events.append(
            CorporateActionEvent(
                symbol_value=values[indices["code"]].strip(),
                effective_date=_flexible_roc_date(values[indices["date"]]),
                kind=CorporateActionKind.DIVIDEND,
                prior_close=_tw_decimal(values[indices["prior_close"]], "prior close"),
                reference_price=_tw_decimal(values[indices["reference_price"]], "reference price"),
                source="twse_twt49u",
                description=description,
            )
        )
    return tuple(events)


def parse_split_payload(payload: object) -> tuple[CorporateActionEvent, ...]:
    """Parse TWTCAU ETF split/reverse-split rows."""

    return _parse_resumption_payload(
        payload,
        report="TWTCAU",
        required=_SPLIT_REQUIRED_FIELDS,
        kind=CorporateActionKind.SPLIT,
        source="twse_twtcau",
    )


def parse_reduction_payload(payload: object) -> tuple[CorporateActionEvent, ...]:
    """Parse TWTAUU capital-reduction rows."""

    return _parse_resumption_payload(
        payload,
        report="TWTAUU",
        required=_REDUCTION_REQUIRED_FIELDS,
        kind=CorporateActionKind.CAPITAL_REDUCTION,
        source="twse_twtauu",
    )


def _parse_resumption_payload(
    payload: object,
    *,
    report: str,
    required: Mapping[str, tuple[str, ...]],
    kind: CorporateActionKind,
    source: str,
) -> tuple[CorporateActionEvent, ...]:
    mapping = _ok_payload_or_none(payload, report)
    if mapping is None:
        return ()
    fields = _string_list(mapping.get("fields"), f"{report} fields")
    indices = _resolve_field_indices(fields, required, report)
    rows = _row_list(mapping.get("data"), f"{report} data")

    events: list[CorporateActionEvent] = []
    for row in rows:
        values = _string_row(row)
        events.append(
            CorporateActionEvent(
                symbol_value=values[indices["code"]].strip(),
                effective_date=_flexible_roc_date(values[indices["date"]]),
                kind=kind,
                prior_close=_tw_decimal(values[indices["prior_close"]], "prior close"),
                reference_price=_tw_decimal(values[indices["reference_price"]], "reference price"),
                source=source,
            )
        )
    return tuple(events)


def _require_ok_payload(payload: object, report: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        msg = f"{report} payload must be a JSON object"
        raise MarketDataValidationError(msg)
    mapping = cast(Mapping[str, object], payload)
    stat = str(mapping.get("stat", "")).strip()
    if stat != "OK":
        msg = f"{report} returned stat={stat!r}"
        raise MarketDataValidationError(msg)
    return mapping


def _ok_payload_or_none(payload: object, report: str) -> Mapping[str, object] | None:
    """Like ``_require_ok_payload`` but treats 查無-style stats as no data."""

    if not isinstance(payload, Mapping):
        msg = f"{report} payload must be a JSON object"
        raise MarketDataValidationError(msg)
    mapping = cast(Mapping[str, object], payload)
    stat = str(mapping.get("stat", "")).strip()
    if stat == "OK":
        return mapping
    if "查無" in stat:
        return None
    msg = f"{report} returned stat={stat!r}"
    raise MarketDataValidationError(msg)


def _resolve_field_indices(
    fields: Sequence[str],
    required: Mapping[str, tuple[str, ...]],
    report: str,
) -> dict[str, int]:
    indices: dict[str, int] = {}
    for key, aliases in required.items():
        index = _optional_field_index(fields, aliases)
        if index is None:
            msg = f"{report} fields are missing {aliases}; got {list(fields)}"
            raise MarketDataValidationError(msg)
        indices[key] = index
    return indices


def _optional_field_index(fields: Sequence[str], aliases: tuple[str, ...]) -> int | None:
    for alias in aliases:
        for index, name in enumerate(fields):
            if alias in name:
                return index
    return None


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        msg = f"{label} must be a list"
        raise MarketDataValidationError(msg)
    return tuple(str(item) for item in cast(Sequence[object], value))


def _row_list(value: object, label: str) -> tuple[Sequence[object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        msg = f"{label} must be a list of rows"
        raise MarketDataValidationError(msg)
    rows: list[Sequence[object]] = []
    for item in cast(Sequence[object], value):
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
            msg = f"{label} rows must be lists"
            raise MarketDataValidationError(msg)
        rows.append(cast(Sequence[object], item))
    return tuple(rows)


def _string_row(row: Sequence[object]) -> tuple[str, ...]:
    return tuple(str(value) for value in row)


def _tw_decimal(raw: str, label: str) -> Decimal:
    cleaned = raw.replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        msg = f"{label} is not a TW decimal value: {raw!r}"
        raise MarketDataValidationError(msg) from exc


def _flexible_roc_date(raw: str) -> date:
    text = raw.strip()
    if "年" in text:
        return parse_roc_cjk(text)
    return parse_roc_slash(text)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
