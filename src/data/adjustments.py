"""Corporate-action events and the dividend/split adjustment pipeline.

Hard rule (docs/contracts/DATA_ADAPTER_TWSE.md): the ADJUSTED series feeds
signals and return math; the RAW series feeds accounting and display. Every
adjustment factor is derived from an official corporate-action row
(factor = reference price ÷ prior close) and persisted with its inputs so
the chain is auditable and recomputable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path
from typing import cast

from src.data.calendar import TradingCalendar
from src.data.types import MarketDataValidationError
from src.domain import Candle


class CorporateActionKind(Enum):
    """Official corporate-action categories that adjust the price series."""

    DIVIDEND = "DIVIDEND"
    SPLIT = "SPLIT"
    CAPITAL_REDUCTION = "CAPITAL_REDUCTION"


@dataclass(frozen=True, slots=True)
class CorporateActionEvent:
    """One official corporate-action row with its adjustment inputs.

    ``effective_date`` is the ex-date (dividends) or the resumption date
    (splits, capital reductions): the first session whose prices are on the
    new basis. Prices BEFORE the effective date are multiplied by
    ``factor`` when building the adjusted series.
    """

    symbol_value: str
    effective_date: date
    kind: CorporateActionKind
    prior_close: Decimal
    reference_price: Decimal
    source: str
    description: str = ""

    def __post_init__(self) -> None:
        if not self.symbol_value.strip():
            msg = "symbol_value must not be empty"
            raise MarketDataValidationError(msg)
        for name, value in (
            ("prior_close", self.prior_close),
            ("reference_price", self.reference_price),
        ):
            if not isinstance(value, Decimal) or value <= Decimal("0"):
                msg = f"{name} must be a positive Decimal"
                raise MarketDataValidationError(msg)
        if not self.source.strip():
            msg = "source must not be empty"
            raise MarketDataValidationError(msg)

    @property
    def factor(self) -> Decimal:
        """Backward adjustment factor: official reference ÷ prior close."""

        return self.reference_price / self.prior_close


def cumulative_factor(
    events: Iterable[CorporateActionEvent],
    *,
    symbol_value: str,
    on: date,
) -> Decimal:
    """Product of factors of this symbol's events strictly after ``on``."""

    factor = Decimal("1")
    for event in events:
        if event.symbol_value == symbol_value and event.effective_date > on:
            factor *= event.factor
    return factor


ADJUSTED_PRICE_PRECISION = Decimal("0.000001")


def adjusted_price(raw_price: Decimal, factor: Decimal) -> Decimal:
    """Scale one raw price by a cumulative factor at fixed 1e-6 precision.

    Quantizing makes the result independent of multiplication order (chained
    Decimal divisions are otherwise rounded at 28 significant digits) and
    1e-6 on NT$10-3,000 prices is far below one basis point of error.
    """

    return (raw_price * factor).quantize(ADJUSTED_PRICE_PRECISION, rounding=ROUND_HALF_UP)


def apply_adjustments(
    candles: Sequence[Candle],
    events: Iterable[CorporateActionEvent],
) -> tuple[Candle, ...]:
    """Build the adjusted series: scale OHLC of candles before each event.

    Volume is intentionally left raw: adjusted volume is not needed by the
    SMA stack and unit-mixing volume invites silent errors.
    """

    event_tuple = _validated_events(events)
    adjusted: list[Candle] = []
    for candle in candles:
        if candle.trading_date is None:
            msg = "adjusted series requires candles with trading_date set"
            raise MarketDataValidationError(msg)
        factor = cumulative_factor(
            event_tuple, symbol_value=candle.symbol.value, on=candle.trading_date
        )
        if factor == Decimal("1"):
            adjusted.append(candle)
            continue
        adjusted.append(
            Candle(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open_price=adjusted_price(candle.open_price, factor),
                high_price=adjusted_price(candle.high_price, factor),
                low_price=adjusted_price(candle.low_price, factor),
                close_price=adjusted_price(candle.close_price, factor),
                volume=candle.volume,
                is_closed=candle.is_closed,
                trading_date=candle.trading_date,
            )
        )
    return tuple(adjusted)


def split_share_multiplier(event: CorporateActionEvent) -> Decimal:
    """Whole-share multiplier for a split/reverse-split position adjustment.

    TWTCAU publishes only prices; the share ratio is derived from
    prior ÷ reference and snapped to the nearest simple ratio (integer for
    splits, 1/integer for reverse splits). The 0050 case: 188.65 / 47.16 =
    4.0002... → 4. A derived ratio more than 1% off its snapped value is an
    error, never a guess.
    """

    if event.kind is CorporateActionKind.DIVIDEND:
        msg = "dividends do not adjust share counts"
        raise MarketDataValidationError(msg)
    derived = event.prior_close / event.reference_price
    if derived >= Decimal("1"):
        snapped = Decimal(int(derived.to_integral_value(rounding=ROUND_HALF_UP)))
    else:
        inverse = (Decimal("1") / derived).to_integral_value(rounding=ROUND_HALF_UP)
        snapped = Decimal("1") / Decimal(int(inverse))
    if snapped <= Decimal("0"):
        msg = f"split multiplier must be positive (derived {derived})"
        raise MarketDataValidationError(msg)
    deviation = abs(derived / snapped - Decimal("1"))
    if deviation > Decimal("0.01"):
        msg = (
            f"derived share multiplier {derived} deviates {deviation:.4f} from "
            f"snapped {snapped}; refusing to guess"
        )
        raise MarketDataValidationError(msg)
    return snapped


def suspension_dates(
    candles: Sequence[Candle],
    events: Iterable[CorporateActionEvent],
    calendar: TradingCalendar,
) -> frozenset[date]:
    """Trading days inside split/reduction trading halts (expected-missing).

    A split or capital reduction halts trading for several sessions before
    the resumption date (the 0050 2025-06 split halted five sessions).
    Those trading days legitimately have no candle forever; quality checks
    must not flag them as gaps.
    """

    dates_with_candles = {
        candle.trading_date for candle in candles if candle.trading_date is not None
    }
    expected_missing: set[date] = set()
    for event in _validated_events(events):
        if event.kind is CorporateActionKind.DIVIDEND:
            continue
        prior_dates = [
            candle_date
            for candle_date in dates_with_candles
            if candle_date is not None and candle_date < event.effective_date
        ]
        if not prior_dates:
            continue
        cursor = calendar.next_trading_day(max(prior_dates))
        while cursor < event.effective_date:
            expected_missing.add(cursor)
            cursor = calendar.next_trading_day(cursor)
    return frozenset(expected_missing)


def _validated_events(
    events: Iterable[CorporateActionEvent],
) -> tuple[CorporateActionEvent, ...]:
    """Deduplicate identical rows; raise only on CONFLICTING same-key rows.

    Market-wide report sweeps contain real same-day repeats (observed live:
    2408 had two capital-reduction rows for 2014-09-09). Identical repeats
    are harmless and dropped; same-key rows with different prices would make
    the factor chain ambiguous and must fail loudly.
    """

    deduplicated: list[CorporateActionEvent] = []
    seen: dict[tuple[str, date, CorporateActionKind], CorporateActionEvent] = {}
    for event in events:
        key = (event.symbol_value, event.effective_date, event.kind)
        existing = seen.get(key)
        if existing is None:
            seen[key] = event
            deduplicated.append(event)
            continue
        if (
            existing.prior_close != event.prior_close
            or existing.reference_price != event.reference_price
        ):
            msg = (
                "conflicting corporate-action rows: "
                f"{event.symbol_value} {event.effective_date.isoformat()} {event.kind.value} "
                f"({existing.prior_close}->{existing.reference_price} vs "
                f"{event.prior_close}->{event.reference_price})"
            )
            raise MarketDataValidationError(msg)
    return tuple(deduplicated)


def adjustment_factors_file_name(symbol_value: str) -> str:
    """Deterministic file name for one symbol's factor table."""

    return f"{symbol_value}_adjustments.json"


def write_adjustment_factors(
    events: Iterable[CorporateActionEvent],
    path: str | Path,
) -> Path:
    """Persist the auditable factor table (inputs + derived factor)."""

    event_tuple = _validated_events(events)
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "symbol": event.symbol_value,
            "effective_date": event.effective_date.isoformat(),
            "kind": event.kind.value,
            "prior_close": str(event.prior_close),
            "reference_price": str(event.reference_price),
            "factor": str(event.factor),
            "source": event.source,
            "description": event.description,
        }
        for event in sorted(event_tuple, key=lambda item: (item.effective_date, item.kind.value))
    ]
    file_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return file_path


def read_adjustment_factors(path: str | Path) -> tuple[CorporateActionEvent, ...]:
    """Load a factor table written by ``write_adjustment_factors``."""

    file_path = Path(path)
    payload = cast(object, json.loads(file_path.read_text(encoding="utf-8")))
    if not isinstance(payload, list):
        msg = f"{file_path} must contain a JSON array"
        raise MarketDataValidationError(msg)
    events: list[CorporateActionEvent] = []
    for raw_row in cast(list[object], payload):
        if not isinstance(raw_row, Mapping):
            msg = f"{file_path} rows must be objects"
            raise MarketDataValidationError(msg)
        row = cast(Mapping[str, object], raw_row)
        events.append(
            CorporateActionEvent(
                symbol_value=str(row["symbol"]),
                effective_date=date.fromisoformat(str(row["effective_date"])),
                kind=CorporateActionKind(str(row["kind"])),
                prior_close=Decimal(str(row["prior_close"])),
                reference_price=Decimal(str(row["reference_price"])),
                source=str(row["source"]),
                description=str(row.get("description", "")),
            )
        )
    return tuple(events)
