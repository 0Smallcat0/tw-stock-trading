"""Backfill orchestration: splice FinMind history with TWSE official data.

Splice rule (docs/contracts/DATA_ADAPTER_TWSE.md): FinMind supplies the
pre-2010 tail that TWSE RWD lacks; TWSE rows win wherever both exist, but
only AFTER the overlap reconciles — any close-price mismatch is a hard
error, never a silent preference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.data.adjustments import (
    CorporateActionEvent,
    adjustment_factors_file_name,
    apply_adjustments,
    suspension_dates,
    write_adjustment_factors,
)
from src.data.calendar import TradingCalendar
from src.data.files import adjusted_candle_file_name, candle_file_name, write_candles_jsonl
from src.data.quality import inspect_daily_candle_quality
from src.data.types import CandleQualityReport, MarketDataValidationError
from src.domain import Candle, Symbol


@dataclass(frozen=True, slots=True)
class SplicedSeries:
    """Result of reconciling and splicing the two public sources."""

    candles: tuple[Candle, ...]
    overlap_days: int
    finmind_only_days: int


@dataclass(frozen=True, slots=True)
class BackfillArtifacts:
    """Files written for one symbol's backfill."""

    raw_path: Path
    adjusted_path: Path
    factors_path: Path
    candle_count: int
    overlap_days: int
    quality_report: CandleQualityReport


def reconcile_and_splice(
    *,
    finmind_candles: tuple[Candle, ...],
    twse_candles: tuple[Candle, ...],
) -> SplicedSeries:
    """Verify close-price equality on overlapping dates, then splice.

    TWSE rows are kept where both sources have the date (official volume
    figures); FinMind supplies dates before the TWSE floor.
    """

    twse_by_date = {candle.trading_date: candle for candle in twse_candles}
    finmind_by_date = {candle.trading_date: candle for candle in finmind_candles}
    if None in twse_by_date or None in finmind_by_date:
        msg = "backfill requires trading_date on every candle"
        raise MarketDataValidationError(msg)

    mismatches: list[str] = []
    overlap = sorted(set(twse_by_date) & set(finmind_by_date), key=lambda d: (d is None, d))
    for trading_date in overlap:
        twse_close = twse_by_date[trading_date].close_price
        finmind_close = finmind_by_date[trading_date].close_price
        if twse_close != finmind_close:
            assert trading_date is not None
            mismatches.append(
                f"{trading_date.isoformat()}: twse={twse_close} finmind={finmind_close}"
            )
    if mismatches:
        preview = "; ".join(mismatches[:5])
        msg = (
            f"close-price reconciliation failed on {len(mismatches)} day(s): {preview}"
            " — refusing to splice disagreeing sources"
        )
        raise MarketDataValidationError(msg)

    spliced = dict(finmind_by_date)
    spliced.update(twse_by_date)
    ordered = tuple(sorted(spliced.values(), key=lambda candle: candle.open_time))
    return SplicedSeries(
        candles=ordered,
        overlap_days=len(overlap),
        finmind_only_days=len(set(finmind_by_date) - set(twse_by_date)),
    )


def write_backfill_artifacts(
    *,
    symbol: Symbol,
    candles: tuple[Candle, ...],
    events: tuple[CorporateActionEvent, ...],
    calendar: TradingCalendar,
    observed_on: date,
    stale_trading_days: int,
    candles_directory: str | Path,
    adjustments_directory: str | Path,
    overlap_days: int,
    verified_suspensions: tuple[date, ...] = (),
) -> BackfillArtifacts:
    """Quality-check the spliced series, then persist raw + adjusted + factors.

    Gap checking exempts corporate-action halt windows (e.g. the 0050
    2025-06 split halt) plus any caller-supplied verified suspensions —
    days both independent sources agree the symbol did not trade while
    the market was open. Any OTHER issue fails the backfill loudly.
    """

    symbol_events = tuple(event for event in events if event.symbol_value == symbol.value)
    halt_dates = tuple(suspension_dates(candles, symbol_events, calendar)) + verified_suspensions
    report = inspect_daily_candle_quality(
        candles,
        calendar=calendar,
        observed_on=observed_on,
        stale_trading_days=stale_trading_days,
        expected_missing_dates=halt_dates,
    )
    if not report.is_usable_for_strategy:
        first = report.issues[0]
        msg = (
            f"backfill quality check failed with {len(report.issues)} issue(s); "
            f"first: {first.code.value} {first.symbol} {first.detail}"
        )
        raise MarketDataValidationError(msg)

    timeframe_value = candles[0].timeframe.value if candles else "1d"
    candles_base = Path(candles_directory)
    adjustments_base = Path(adjustments_directory)
    raw_path = write_candles_jsonl(
        candles, candles_base / candle_file_name(symbol.value, timeframe_value)
    )
    adjusted = apply_adjustments(candles, symbol_events)
    adjusted_path = write_candles_jsonl(
        adjusted, candles_base / adjusted_candle_file_name(symbol.value, timeframe_value)
    )
    factors_path = write_adjustment_factors(
        symbol_events, adjustments_base / adjustment_factors_file_name(symbol.value)
    )
    return BackfillArtifacts(
        raw_path=raw_path,
        adjusted_path=adjusted_path,
        factors_path=factors_path,
        candle_count=len(candles),
        overlap_days=overlap_days,
        quality_report=report,
    )
