"""Point-in-time feature contracts for the Core MVP."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from src.domain import Symbol, Timeframe


class FeaturePipelineValidationError(ValueError):
    """Raised when candles are not safe for feature generation."""


@dataclass(frozen=True, slots=True)
class FeatureSourceRange:
    """Closed candle range used to compute a feature snapshot."""

    symbol: Symbol
    timeframe: Timeframe
    start_open_time: datetime
    end_close_time: datetime

    def __post_init__(self) -> None:
        _require_utc("start_open_time", self.start_open_time)
        _require_utc("end_close_time", self.end_close_time)
        if self.end_close_time <= self.start_open_time:
            msg = "end_close_time must be after start_open_time"
            raise FeaturePipelineValidationError(msg)


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """Neutral feature values available after a closed candle."""

    symbol: Symbol
    timeframe: Timeframe
    as_of: datetime
    source_ranges: tuple[FeatureSourceRange, ...]
    values: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        _require_utc("as_of", self.as_of)
        if not self.source_ranges:
            msg = "source_ranges must not be empty"
            raise FeaturePipelineValidationError(msg)
        if any(source_range.end_close_time > self.as_of for source_range in self.source_ranges):
            msg = "source_ranges must not include data after as_of"
            raise FeaturePipelineValidationError(msg)
        copied_values = dict(self.values)
        if not copied_values:
            msg = "values must not be empty"
            raise FeaturePipelineValidationError(msg)
        for name, value in copied_values.items():
            if not name:
                msg = "feature names must not be empty"
                raise FeaturePipelineValidationError(msg)
            if not isinstance(value, Decimal) or not value.is_finite():
                msg = f"{name} must be a finite Decimal"
                raise FeaturePipelineValidationError(msg)
        object.__setattr__(self, "values", MappingProxyType(copied_values))


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise FeaturePipelineValidationError(msg)
