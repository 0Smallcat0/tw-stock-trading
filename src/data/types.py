"""Public market data contracts for the Core MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class MarketDataError(ValueError):
    """Raised when public market data cannot be parsed or fetched safely."""


class MarketDataValidationError(MarketDataError):
    """Raised when market data is not safe for downstream strategy input."""


class CandleIssueCode(Enum):
    """Visible candle quality issue codes."""

    OPEN_CANDLE = "OPEN_CANDLE"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class CandleQualityIssue:
    """One visible issue found in a candle sequence."""

    code: CandleIssueCode
    symbol: str
    timeframe: str
    open_time: datetime | None = None
    expected_open_time: datetime | None = None
    actual_open_time: datetime | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CandleQualityReport:
    """Candle quality report consumed by later feature/risk/runtime gates."""

    issues: tuple[CandleQualityIssue, ...]

    @property
    def is_usable_for_strategy(self) -> bool:
        return not self.issues


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise MarketDataValidationError(msg)
