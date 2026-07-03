"""Public market data entry points for the TW stock signal MVP."""

from src.data.adjustments import (
    ADJUSTED_PRICE_PRECISION,
    CorporateActionEvent,
    CorporateActionKind,
    adjusted_price,
    adjustment_factors_file_name,
    apply_adjustments,
    cumulative_factor,
    read_adjustment_factors,
    split_share_multiplier,
    suspension_dates,
    write_adjustment_factors,
)
from src.data.backfill import (
    BackfillArtifacts,
    SplicedSeries,
    reconcile_and_splice,
    write_backfill_artifacts,
)
from src.data.calendar import (
    CalendarCoverageError,
    HolidayEntry,
    HolidayEntryKind,
    TradingCalendar,
    parse_holiday_schedule,
    read_calendar_json,
    write_calendar_json,
)
from src.data.files import (
    adjusted_candle_file_name,
    candle_file_name,
    read_candles_jsonl,
    write_candles_jsonl,
)
from src.data.finmind import (
    FINMIND_SOURCE,
    FinMindPublicClient,
    parse_taiwan_stock_price_payload,
    parse_trading_date_payload,
)
from src.data.quality import inspect_daily_candle_quality, require_closed_candles
from src.data.roc_dates import parse_roc_cjk, parse_roc_compact, parse_roc_slash
from src.data.sessions import session_close_utc, session_open_utc
from src.data.twse import (
    TWSE_SOURCE,
    TWSE_STOCK_DAY_FLOOR,
    TwsePublicClient,
    parse_ex_right_payload,
    parse_reduction_payload,
    parse_split_payload,
    parse_stock_day_payload,
)
from src.data.types import (
    CandleIssueCode,
    CandleQualityIssue,
    CandleQualityReport,
    MarketDataError,
    MarketDataValidationError,
)

__all__ = [
    "ADJUSTED_PRICE_PRECISION",
    "FINMIND_SOURCE",
    "TWSE_SOURCE",
    "TWSE_STOCK_DAY_FLOOR",
    "BackfillArtifacts",
    "CalendarCoverageError",
    "CandleIssueCode",
    "CandleQualityIssue",
    "CandleQualityReport",
    "CorporateActionEvent",
    "CorporateActionKind",
    "FinMindPublicClient",
    "HolidayEntry",
    "HolidayEntryKind",
    "MarketDataError",
    "MarketDataValidationError",
    "SplicedSeries",
    "TradingCalendar",
    "TwsePublicClient",
    "adjusted_candle_file_name",
    "adjusted_price",
    "adjustment_factors_file_name",
    "apply_adjustments",
    "candle_file_name",
    "cumulative_factor",
    "inspect_daily_candle_quality",
    "parse_ex_right_payload",
    "parse_holiday_schedule",
    "parse_reduction_payload",
    "parse_roc_cjk",
    "parse_roc_compact",
    "parse_roc_slash",
    "parse_split_payload",
    "parse_stock_day_payload",
    "parse_taiwan_stock_price_payload",
    "parse_trading_date_payload",
    "read_adjustment_factors",
    "read_calendar_json",
    "read_candles_jsonl",
    "reconcile_and_splice",
    "require_closed_candles",
    "session_close_utc",
    "session_open_utc",
    "split_share_multiplier",
    "suspension_dates",
    "write_adjustment_factors",
    "write_backfill_artifacts",
    "write_calendar_json",
    "write_candles_jsonl",
]
