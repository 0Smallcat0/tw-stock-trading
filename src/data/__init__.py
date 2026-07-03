"""Public market data entry points.

The TWSE/FinMind clients and the corporate-action adjustment pipeline land
in Goal TW-C per `GOALS.md`.
"""

from src.data.calendar import (
    CalendarCoverageError,
    HolidayEntry,
    HolidayEntryKind,
    TradingCalendar,
    parse_holiday_schedule,
    read_calendar_json,
    write_calendar_json,
)
from src.data.files import candle_file_name, read_candles_jsonl, write_candles_jsonl
from src.data.quality import inspect_candle_quality, require_closed_candles, timeframe_delta
from src.data.roc_dates import parse_roc_cjk, parse_roc_compact, parse_roc_slash
from src.data.types import (
    CandleIssueCode,
    CandleQualityIssue,
    CandleQualityReport,
    MarketDataError,
    MarketDataValidationError,
)

__all__ = [
    "CalendarCoverageError",
    "CandleIssueCode",
    "CandleQualityIssue",
    "CandleQualityReport",
    "HolidayEntry",
    "HolidayEntryKind",
    "MarketDataError",
    "MarketDataValidationError",
    "TradingCalendar",
    "candle_file_name",
    "inspect_candle_quality",
    "parse_holiday_schedule",
    "parse_roc_cjk",
    "parse_roc_compact",
    "parse_roc_slash",
    "read_calendar_json",
    "read_candles_jsonl",
    "require_closed_candles",
    "timeframe_delta",
    "write_calendar_json",
    "write_candles_jsonl",
]
