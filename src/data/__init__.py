"""Public market data entry points.

The TWSE/FinMind clients, the trading calendar, and the corporate-action
adjustment pipeline land in Goals TW-B/TW-C per `GOALS.md`.
"""

from src.data.files import candle_file_name, read_candles_jsonl, write_candles_jsonl
from src.data.quality import inspect_candle_quality, require_closed_candles, timeframe_delta
from src.data.types import (
    CandleIssueCode,
    CandleQualityIssue,
    CandleQualityReport,
    MarketDataError,
    MarketDataValidationError,
)

__all__ = [
    "CandleIssueCode",
    "CandleQualityIssue",
    "CandleQualityReport",
    "MarketDataError",
    "MarketDataValidationError",
    "candle_file_name",
    "inspect_candle_quality",
    "read_candles_jsonl",
    "require_closed_candles",
    "timeframe_delta",
    "write_candles_jsonl",
]
