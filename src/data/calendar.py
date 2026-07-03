"""TWSE trading calendar: which days trade, and trading-day arithmetic.

Design rules (docs/contracts/DATA_ADAPTER_TWSE.md):

- Weekends are implicitly closed; the exchange holiday schedule supplies the
  weekday closures (holidays, Lunar-New-Year settlement-only days, make-up
  workdays on which the market is CLOSED).
- The TWSE OpenAPI holiday schedule covers ONE year per fetch and mixes real
  closures with informational rows (e.g. "first trading day of the year");
  parsing classifies every row by keyword and FAILS LOUDLY on unknown
  wording rather than guessing.
- Unscheduled closures (typhoon days) are runtime events, not schedule
  entries; they are recorded as extra closures when adjudicated
  (`UNSCHEDULED_CLOSURE`) per the data adapter contract.
- Any query outside the calendar's covered years raises
  ``CalendarCoverageError``: the system never guesses whether an uncovered
  date trades.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import cast

from src.data.roc_dates import parse_roc_compact
from src.data.types import MarketDataError, MarketDataValidationError

_SATURDAY = 5

_CLOSURE_KEYWORDS = ("放假", "休市", "無交易", "補假", "停止交易", "休息")
_TRADING_INFO_KEYWORDS = ("開始交易", "最後交易", "開始買賣", "恢復交易")


class CalendarCoverageError(MarketDataError):
    """Raised when a date outside the calendar's covered years is queried."""


class HolidayEntryKind(Enum):
    """Classification of one holiday-schedule row."""

    CLOSURE = "CLOSURE"
    TRADING_INFO = "TRADING_INFO"


@dataclass(frozen=True, slots=True)
class HolidayEntry:
    """One parsed row of the TWSE holiday schedule."""

    entry_date: date
    name: str
    description: str
    kind: HolidayEntryKind


def parse_holiday_schedule(payload: object) -> tuple[HolidayEntry, ...]:
    """Parse the TWSE OpenAPI holiday-schedule JSON array.

    Rows whose description matches no known keyword raise: new exchange
    wording must be classified by a human, never silently guessed.
    """

    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        msg = "holiday schedule payload must be a JSON array"
        raise MarketDataValidationError(msg)
    entries: list[HolidayEntry] = []
    for index, raw_row in enumerate(payload):
        if not isinstance(raw_row, Mapping):
            msg = f"holiday schedule row {index} must be an object"
            raise MarketDataValidationError(msg)
        row = cast(Mapping[str, object], raw_row)
        raw_date = row.get("Date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            msg = f"holiday schedule row {index} is missing Date"
            raise MarketDataValidationError(msg)
        entry_date = parse_roc_compact(raw_date)
        name = str(row.get("Name", "")).strip()
        description = str(row.get("Description", "")).strip()
        entries.append(
            HolidayEntry(
                entry_date=entry_date,
                name=name,
                description=description,
                kind=_classify(name, description, index),
            )
        )
    return tuple(entries)


def _classify(name: str, description: str, index: int) -> HolidayEntryKind:
    text = f"{name} {description}"
    is_closure = any(keyword in text for keyword in _CLOSURE_KEYWORDS)
    is_trading_info = any(keyword in text for keyword in _TRADING_INFO_KEYWORDS)
    if is_closure:
        # Closure keywords win: "最後交易日...後續放假" style rows are closures
        # only when they carry closure wording for THIS date; plain
        # informational rows carry none.
        if is_trading_info and not any(keyword in description for keyword in _CLOSURE_KEYWORDS):
            return HolidayEntryKind.TRADING_INFO
        return HolidayEntryKind.CLOSURE
    if is_trading_info:
        return HolidayEntryKind.TRADING_INFO
    msg = (
        f"holiday schedule row {index} has unrecognized wording "
        f"(name={name!r}, description={description!r}); classify it before use"
    )
    raise MarketDataValidationError(msg)


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """Trading-day oracle over explicitly covered years."""

    covered_years: frozenset[int]
    closures: frozenset[date]

    def __post_init__(self) -> None:
        if not self.covered_years:
            msg = "covered_years must not be empty"
            raise MarketDataValidationError(msg)
        outside = {closure for closure in self.closures if closure.year not in self.covered_years}
        if outside:
            msg = f"closures outside covered years: {sorted(outside)}"
            raise MarketDataValidationError(msg)

    @classmethod
    def from_holiday_entries(
        cls,
        entries: Iterable[HolidayEntry],
        *,
        year: int,
        extra_closures: Iterable[date] = (),
    ) -> TradingCalendar:
        """Build a one-year calendar from parsed schedule entries.

        ``extra_closures`` carries adjudicated unscheduled closures
        (typhoon days) that the published schedule cannot contain.
        """

        closures = {
            entry.entry_date
            for entry in entries
            if entry.kind is HolidayEntryKind.CLOSURE and entry.entry_date.year == year
        }
        for extra in extra_closures:
            if extra.year != year:
                msg = f"extra closure {extra.isoformat()} outside schedule year {year}"
                raise MarketDataValidationError(msg)
            closures.add(extra)
        return cls(covered_years=frozenset({year}), closures=frozenset(closures))

    def merge(self, other: TradingCalendar) -> TradingCalendar:
        """Combine calendars covering different years into one oracle."""

        return TradingCalendar(
            covered_years=self.covered_years | other.covered_years,
            closures=self.closures | other.closures,
        )

    def is_trading_day(self, value: date) -> bool:
        """True when the exchange trades on this date."""

        self._require_coverage(value)
        if value.weekday() >= _SATURDAY:
            return False
        return value not in self.closures

    def next_trading_day(self, value: date) -> date:
        """First trading day STRICTLY AFTER the given date."""

        cursor = value + timedelta(days=1)
        while True:
            self._require_coverage(cursor)
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)

    def previous_trading_day(self, value: date) -> date:
        """Last trading day STRICTLY BEFORE the given date."""

        cursor = value - timedelta(days=1)
        while True:
            self._require_coverage(cursor)
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)

    def trading_days_between(self, start: date, end: date) -> int:
        """Count trading days t with ``start < t <= end`` (staleness unit).

        A candle dated ``start`` observed on trading day ``end`` is
        ``trading_days_between(start, end)`` trading days old; weekends,
        holidays, and typhoon closures add zero.
        """

        if end < start:
            msg = "end must not be before start"
            raise MarketDataValidationError(msg)
        count = 0
        cursor = start + timedelta(days=1)
        while cursor <= end:
            self._require_coverage(cursor)
            if self.is_trading_day(cursor):
                count += 1
            cursor += timedelta(days=1)
        return count

    def trading_days(self, start: date, end: date) -> Iterator[date]:
        """Yield trading days in ``[start, end]`` inclusive, ascending."""

        if end < start:
            msg = "end must not be before start"
            raise MarketDataValidationError(msg)
        cursor = start
        while cursor <= end:
            self._require_coverage(cursor)
            if self.is_trading_day(cursor):
                yield cursor
            cursor += timedelta(days=1)

    def _require_coverage(self, value: date) -> None:
        if value.year not in self.covered_years:
            msg = (
                f"date {value.isoformat()} is outside calendar coverage "
                f"(covered years: {sorted(self.covered_years)})"
            )
            raise CalendarCoverageError(msg)


def write_calendar_json(calendar: TradingCalendar, directory: str | Path) -> tuple[Path, ...]:
    """Persist one JSON schedule file per covered year (auditable, mergeable)."""

    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for year in sorted(calendar.covered_years):
        closures = sorted(
            closure.isoformat() for closure in calendar.closures if closure.year == year
        )
        path = base / f"twse_calendar_{year}.json"
        payload = {"year": year, "closures": closures}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return tuple(written)


def read_calendar_json(directory: str | Path) -> TradingCalendar:
    """Load and merge every per-year schedule file in a directory."""

    base = Path(directory)
    paths = sorted(base.glob("twse_calendar_*.json"))
    if not paths:
        msg = f"no calendar schedule files found in {base}"
        raise MarketDataValidationError(msg)
    years: set[int] = set()
    closures: set[date] = set()
    for path in paths:
        payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(payload, Mapping):
            msg = f"{path} must contain a JSON object"
            raise MarketDataValidationError(msg)
        mapping = cast(Mapping[str, object], payload)
        raw_year = mapping.get("year")
        raw_closures = mapping.get("closures")
        if not isinstance(raw_year, int) or not isinstance(raw_closures, list):
            msg = f"{path} must contain integer year and closures list"
            raise MarketDataValidationError(msg)
        years.add(raw_year)
        for raw_closure in cast(list[object], raw_closures):
            closure = date.fromisoformat(str(raw_closure))
            if closure.year != raw_year:
                msg = f"{path} lists closure {closure.isoformat()} outside year {raw_year}"
                raise MarketDataValidationError(msg)
            closures.add(closure)
    return TradingCalendar(covered_years=frozenset(years), closures=frozenset(closures))
