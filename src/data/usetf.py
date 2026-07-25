"""Public daily OHLCV for one US-listed ETF, read from two independent sources.

Nothing in this module is Taiwan-specific. It lives in this repository
because the ported daily-bar backtest engine does, and the third portfolio
sleeve has to be priced by the same engine as the second one to make the
"same untuned rule" claim mean anything.

Two sources are fetched deliberately. The 2026-07-26 ingestion bug in the
0050 path was invisible precisely because one source was trusted alone; a
single provider that truncates, freezes, or silently back-adjusts a series
cannot be caught from inside itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from src.data.types import MarketDataValidationError
from src.domain import Candle, DomainValidationError, Symbol, Timeframe

YAHOO_SOURCE = "yahoo_chart_public"
FINMIND_US_SOURCE = "finmind_us_public"

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com"
FINMIND_API_BASE_URL = "https://api.finmindtrade.com"

# Yahoo serves the browser HTML shell to unknown agents; this is a plain
# desktop UA with the project name appended, not a disguise.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tw-stock-trading-research"

_NEW_YORK = ZoneInfo("America/New_York")
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)
_PRICE_PRECISION = Decimal("0.000001")
_OHLC_FIELDS_YAHOO = ("open", "high", "low", "close")
_OHLC_FIELDS_FINMIND = ("Open", "High", "Low", "Close")


@dataclass(frozen=True, slots=True)
class SourceSeries:
    """One provider's view of a daily series, plus what it could not supply."""

    source: str
    candles: tuple[Candle, ...]
    incomplete_dates: tuple[date, ...]
    distribution_dates: tuple[date, ...]

    @property
    def trading_dates(self) -> frozenset[date]:
        return frozenset(
            candle.trading_date for candle in self.candles if candle.trading_date is not None
        )

    def closes_by_date(self) -> dict[date, Decimal]:
        return {
            candle.trading_date: candle.close_price
            for candle in self.candles
            if candle.trading_date is not None
        }


@dataclass(frozen=True, slots=True)
class CrossSourceReport:
    """Where two providers disagree about the same instrument."""

    common_start: date
    common_end: date
    shared_days: int
    primary_only: tuple[date, ...]
    secondary_only: tuple[date, ...]
    worst_relative_gap: Decimal
    worst_gap_date: date | None
    mismatch_dates: tuple[date, ...]

    @property
    def primary_days_in_window(self) -> int:
        return self.shared_days + len(self.primary_only)

    @property
    def agreement_ratio(self) -> Decimal:
        total = self.primary_days_in_window
        if total == 0:
            return Decimal("0")
        return Decimal(self.shared_days) / Decimal(total)


def session_times(trading_day: date) -> tuple[datetime, datetime]:
    """UTC open/close for one regular US equity session."""

    open_local = datetime.combine(trading_day, _SESSION_OPEN, tzinfo=_NEW_YORK)
    close_local = datetime.combine(trading_day, _SESSION_CLOSE, tzinfo=_NEW_YORK)
    return open_local.astimezone(UTC), close_local.astimezone(UTC)


def parse_yahoo_chart_payload(payload: Any, *, ticker: str, observed_at: datetime) -> SourceSeries:
    """Candles from a v8 chart response, dropping rows the provider left holed."""

    result = _yahoo_result(payload)
    timestamps = _as_list(result.get("timestamp"), "chart.result[0].timestamp")
    quote_blocks = _as_list(
        _as_mapping(result.get("indicators"), "indicators").get("quote"), "indicators.quote"
    )
    if not quote_blocks:
        msg = "yahoo chart returned no quote block"
        raise MarketDataValidationError(msg)
    quote = _as_mapping(quote_blocks[0], "indicators.quote[0]")
    adjusted = _yahoo_adjusted_closes(result, len(timestamps))

    builder = _SeriesBuilder(ticker=ticker, observed_at=observed_at, source=YAHOO_SOURCE)
    for index, raw_timestamp in enumerate(timestamps):
        opened_at = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
        trading_day = opened_at.astimezone(_NEW_YORK).date()
        builder.add(
            trading_day=trading_day,
            ohlc=[_optional_decimal(quote.get(field), index) for field in _OHLC_FIELDS_YAHOO],
            volume=_optional_decimal(quote.get("volume"), index),
            adjusted_close=adjusted[index],
        )
    return builder.build()


def parse_finmind_us_payload(payload: Any, *, ticker: str, observed_at: datetime) -> SourceSeries:
    """Candles from a FinMind ``USStockPrice`` response."""

    body = _as_mapping(payload, "finmind payload")
    rows = _as_list(body.get("data"), "data")
    builder = _SeriesBuilder(ticker=ticker, observed_at=observed_at, source=FINMIND_US_SOURCE)
    for raw_row in rows:
        row = _as_mapping(raw_row, "data[]")
        builder.add(
            trading_day=date.fromisoformat(str(row["date"])),
            ohlc=[_optional_decimal(row.get(field), 0) for field in _OHLC_FIELDS_FINMIND],
            volume=_optional_decimal(row.get("Volume"), 0),
            adjusted_close=_optional_decimal(row.get("Adj_Close"), 0),
        )
    return builder.build()


def cross_check_sources(
    primary: SourceSeries, secondary: SourceSeries, *, tolerance: Decimal
) -> CrossSourceReport:
    """Compare two providers over the range both of them cover.

    Only the overlap is judged: a provider whose history starts later cannot
    be accused of missing days before it starts.
    """

    left = primary.closes_by_date()
    right = secondary.closes_by_date()
    if not left or not right:
        msg = "cross-source check needs candles from both providers"
        raise MarketDataValidationError(msg)

    start = max(min(left), min(right))
    end = min(max(left), max(right))
    left_days = {day for day in left if start <= day <= end}
    right_days = {day for day in right if start <= day <= end}
    shared = sorted(left_days & right_days)

    worst_gap = Decimal("0")
    worst_day: date | None = None
    mismatches: list[date] = []
    for day in shared:
        reference = left[day]
        if reference <= 0:
            continue
        gap = abs(right[day] - reference) / reference
        if gap > worst_gap:
            worst_gap, worst_day = gap, day
        if gap > tolerance:
            mismatches.append(day)

    return CrossSourceReport(
        common_start=start,
        common_end=end,
        shared_days=len(shared),
        primary_only=tuple(sorted(left_days - right_days)),
        secondary_only=tuple(sorted(right_days - left_days)),
        worst_relative_gap=worst_gap,
        worst_gap_date=worst_day,
        mismatch_dates=tuple(mismatches),
    )


def missing_sessions(series: SourceSeries, *, max_gap: timedelta) -> tuple[date, ...]:
    """Trading dates followed by a suspiciously long silence.

    A US gap longer than ``max_gap`` is either a holiday-stretched week or a
    hole in the feed; the ingestion reports them rather than deciding.
    """

    days = sorted(series.trading_dates)
    return tuple(
        days[index] for index in range(len(days) - 1) if days[index + 1] - days[index] > max_gap
    )


def fetch_yahoo_daily(
    ticker: str,
    *,
    observed_at: datetime,
    base_url: str = YAHOO_CHART_BASE_URL,
    timeout_seconds: float = 60.0,
) -> SourceSeries:
    """Full daily history from Yahoo's public chart endpoint.

    ``range=max`` silently downsamples 1d to monthly bars (measured: 261 rows
    instead of 5,453); an explicit epoch window is the only way to get every
    session.
    """

    params = {"interval": "1d", "period1": "0", "period2": str(int(observed_at.timestamp()))}
    with httpx.Client(timeout=timeout_seconds, headers={"User-Agent": _USER_AGENT}) as client:
        response = client.get(f"{base_url}/v8/finance/chart/{ticker}", params=params)
        response.raise_for_status()
        payload = response.json()
    return parse_yahoo_chart_payload(payload, ticker=ticker, observed_at=observed_at)


def fetch_finmind_us_daily(
    ticker: str,
    *,
    observed_at: datetime,
    start: date,
    base_url: str = FINMIND_API_BASE_URL,
    timeout_seconds: float = 90.0,
) -> SourceSeries:
    """Full daily history from FinMind's public ``USStockPrice`` dataset."""

    params = {
        "dataset": "USStockPrice",
        "data_id": ticker,
        "start_date": start.isoformat(),
        "end_date": observed_at.date().isoformat(),
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.get(f"{base_url}/api/v4/data", params=params)
        response.raise_for_status()
        payload = response.json()
    return parse_finmind_us_payload(payload, ticker=ticker, observed_at=observed_at)


class _SeriesBuilder:
    """Shared row-to-candle path so both providers reject rows identically."""

    def __init__(self, *, ticker: str, observed_at: datetime, source: str) -> None:
        self._ticker = ticker
        self._observed_at = observed_at
        self._source = source
        self._candles: list[Candle] = []
        self._incomplete: list[date] = []
        self._distributions: list[date] = []

    def add(
        self,
        *,
        trading_day: date,
        ohlc: list[Decimal | None],
        volume: Decimal | None,
        adjusted_close: Decimal | None,
    ) -> None:
        if volume is None or any(value is None or value <= 0 for value in ohlc):
            self._incomplete.append(trading_day)
            return
        open_price, high_price, low_price, close_price = (
            value for value in ohlc if value is not None
        )
        if adjusted_close is not None and adjusted_close != close_price:
            self._distributions.append(trading_day)
        candle = self._build(
            trading_day=trading_day,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
        )
        if candle is None:
            self._incomplete.append(trading_day)
            return
        self._candles.append(candle)

    def build(self) -> SourceSeries:
        return SourceSeries(
            source=self._source,
            candles=tuple(self._candles),
            incomplete_dates=tuple(self._incomplete),
            distribution_dates=tuple(self._distributions),
        )

    def _build(
        self,
        *,
        trading_day: date,
        open_price: Decimal,
        high_price: Decimal,
        low_price: Decimal,
        close_price: Decimal,
        volume: Decimal,
    ) -> Candle | None:
        open_time, close_time = session_times(trading_day)
        if close_time > self._observed_at:
            return None
        try:
            return Candle(
                symbol=Symbol(value=self._ticker, base_asset=self._ticker, quote_asset="USD"),
                timeframe=Timeframe("1d"),
                open_time=open_time,
                close_time=close_time,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=volume,
                is_closed=True,
                trading_date=trading_day,
            )
        except DomainValidationError:
            # A row violating high >= max(open, close) >= min(open, close) >= low
            # is a provider defect, not a tradable bar.
            return None


def _yahoo_result(payload: Any) -> dict[str, Any]:
    chart = _as_mapping(_as_mapping(payload, "payload").get("chart"), "chart")
    error = chart.get("error")
    if error:
        msg = f"yahoo chart error: {error}"
        raise MarketDataValidationError(msg)
    results = _as_list(chart.get("result"), "chart.result")
    if not results:
        msg = "yahoo chart returned no result block"
        raise MarketDataValidationError(msg)
    return _as_mapping(results[0], "chart.result[0]")


def _yahoo_adjusted_closes(result: dict[str, Any], expected: int) -> list[Decimal | None]:
    indicators = _as_mapping(result.get("indicators"), "indicators")
    blocks = indicators.get("adjclose")
    if not isinstance(blocks, list) or not blocks:
        return [None] * expected
    series = _as_mapping(blocks[0], "indicators.adjclose[0]").get("adjclose")
    if not isinstance(series, list):
        return [None] * expected
    return [_optional_decimal(series, index) for index in range(expected)]


def _optional_decimal(series: Any, index: int) -> Decimal | None:
    """Quantized value at ``index`` of a JSON array, or of a scalar field."""

    if isinstance(series, list):
        if index >= len(series):
            return None
        raw = series[index]
    else:
        raw = series
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        return Decimal(str(raw)).quantize(_PRICE_PRECISION)
    except ArithmeticError:
        return None


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"expected an object at {label}"
        raise MarketDataValidationError(msg)
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        msg = f"expected an array at {label}"
        raise MarketDataValidationError(msg)
    return value
