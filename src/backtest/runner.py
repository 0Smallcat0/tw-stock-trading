"""Registered backtest entry point: holdout enforcement + trial registry + report."""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.backtest.engine import run_backtest
from src.backtest.holdout import (
    HoldoutState,
    initialize_holdout,
    load_holdout,
    require_data_allowed,
    spend_holdout,
)
from src.backtest.registry import TrialRecord, append_trial
from src.backtest.types import BacktestError, BacktestParameters, BacktestReport
from src.domain import Candle

DEFAULT_HOLDOUT_DAYS = 365

_ENSEMBLE_PARAMETERS = {
    "lookbacks": "20,65,150,200",
    "ladder": "0,0.25,0.5,0.75,1",
    "fill_rule": "next_bar_open",
}


@dataclass(frozen=True, slots=True)
class RegisteredBacktestResult:
    """Everything one registered run produced."""

    report: BacktestReport
    trial: TrialRecord
    report_path: Path
    holdout: HoldoutState


def run_registered_backtest(
    candles_by_symbol: Mapping[str, tuple[Candle, ...]],
    *,
    parameters: BacktestParameters,
    config_hash: str,
    code_version: str,
    registry_path: str | Path,
    holdout_path: str | Path,
    reports_directory: str | Path,
    recorded_at: datetime,
    operator_note: str = "",
    strategy_id: str = "daily_trend_ensemble",
    spend_holdout_single_use: bool = False,
) -> RegisteredBacktestResult:
    """Run one backtest under gate rules: lock, trim, register, report.

    Regular runs never see holdout data: on the first run the lock is created
    (most recent DEFAULT_HOLDOUT_DAYS locked) and inputs are trimmed to end
    before the holdout starts. A qualification run passes
    ``spend_holdout_single_use=True``: the spend is recorded BEFORE execution
    and can never happen twice.
    """

    if not candles_by_symbol:
        msg = "candles_by_symbol must not be empty"
        raise BacktestError(msg)
    data_end = max(candles[-1].close_time for candles in candles_by_symbol.values() if candles)
    # A future-dated candle at first-lock time would anchor the holdout years
    # ahead and silently disable Gate 5 forever. Candle data cannot postdate
    # the run that observes it.
    if data_end > recorded_at:
        msg = (
            f"data_end {data_end.isoformat()} is later than recorded_at "
            f"{recorded_at.isoformat()}; refusing to lock or run on future-dated candles"
        )
        raise BacktestError(msg)
    holdout = load_holdout(holdout_path)
    if holdout is None:
        holdout = initialize_holdout(
            holdout_path,
            holdout_start=data_end - timedelta(days=DEFAULT_HOLDOUT_DAYS),
            locked_at=recorded_at,
        )

    if spend_holdout_single_use:
        holdout = spend_holdout(holdout_path, spent_at=recorded_at)
        run_candles = dict(candles_by_symbol)
    else:
        run_candles = {
            symbol_value: tuple(
                candle for candle in candles if candle.close_time < holdout.holdout_start
            )
            for symbol_value, candles in candles_by_symbol.items()
        }
        if any(not candles for candles in run_candles.values()):
            msg = (
                "no candles remain before the holdout start "
                f"({holdout.holdout_start.isoformat()}); provide more history"
            )
            raise BacktestError(msg)
        trimmed_end = max(candles[-1].close_time for candles in run_candles.values())
        require_data_allowed(holdout_path, data_end=trimmed_end)

    report = run_backtest(run_candles, parameters=parameters)

    metrics = {
        "final_equity": str(report.metrics.final_equity),
        "total_return_fraction": str(report.metrics.total_return_fraction),
        "annualized_sharpe": str(report.metrics.annualized_sharpe),
        "max_drawdown_fraction": str(report.metrics.max_drawdown_fraction),
        "trade_count": str(report.metrics.trade_count),
        "rejected_count": str(report.metrics.rejected_count),
        "annualized_turnover": str(report.metrics.annualized_turnover),
        "total_traded_notional": str(report.metrics.total_traded_notional),
        "benchmark_final_equity": str(report.metrics.benchmark_final_equity),
        "observation_days": str(report.metrics.observation_days),
    }
    if spend_holdout_single_use:
        # The single-use verdict must isolate the holdout window: blended
        # full-history numbers could mask a failing holdout segment.
        metrics.update(
            _segment_metrics(report, segment_start=holdout.holdout_start, prefix="holdout_")
        )

    trial = append_trial(
        registry_path,
        recorded_at=recorded_at,
        config_hash=config_hash,
        code_version=code_version,
        strategy_id=strategy_id,
        parameters={
            **_ENSEMBLE_PARAMETERS,
            "cost_multiplier": str(parameters.cost_multiplier),
            "holdout_spend": str(spend_holdout_single_use),
        },
        universe=tuple(sorted(parameters.risk_budgets)),
        data_start=report.data_start,
        data_end=report.data_end,
        cost_assumptions=dict(report.cost_assumptions),
        metrics=metrics,
        operator_note=operator_note,
    )
    _write_trial_returns(registry_path, trial.trial_id, report)

    report_path = Path(reports_directory) / f"trial-{trial.trial_id:06d}" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {"trial": trial.to_json_dict(), "report": report.to_json_dict()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return RegisteredBacktestResult(
        report=report,
        trial=trial,
        report_path=report_path,
        holdout=holdout,
    )


def _segment_metrics(
    report: BacktestReport,
    *,
    segment_start: datetime,
    prefix: str,
) -> dict[str, str]:
    """After-cost metrics restricted to equity points at or after segment_start."""

    segment = [point for point in report.equity_curve if point.close_time >= segment_start]
    if len(segment) < 2:
        return {f"{prefix}observation_days": str(len(segment))}
    baseline = segment[0].equity
    returns: list[float] = []
    previous = baseline
    peak = baseline
    max_drawdown = Decimal("0")
    for point in segment[1:]:
        if previous > Decimal("0"):
            returns.append(float(point.equity / previous) - 1.0)
        previous = point.equity
        peak = max(peak, point.equity)
        if peak > Decimal("0"):
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)
    sharpe = 0.0
    if len(returns) >= 2:
        stdev = statistics.stdev(returns)
        if stdev > 0.0:
            sharpe = statistics.fmean(returns) / stdev * math.sqrt(365)
    total_return = (
        segment[-1].equity / baseline - Decimal("1") if baseline > Decimal("0") else Decimal("0")
    )
    return {
        f"{prefix}observation_days": str(len(segment)),
        f"{prefix}total_return_fraction": str(total_return),
        f"{prefix}annualized_sharpe": str(round(sharpe, 6)),
        f"{prefix}max_drawdown_fraction": str(max_drawdown),
    }


def _write_trial_returns(registry_path: str | Path, trial_id: int, report: BacktestReport) -> Path:
    """Persist the trial's daily return series next to the registry.

    Gate 3 (CSCV/PBO) and Gate 4 (DSR) need per-period returns for EVERY
    registered trial; bulk reports are gitignored, so this compact series is
    the durable, committable input for the qualification math.
    """

    returns_dir = Path(registry_path).parent / "trial_returns"
    returns_dir.mkdir(parents=True, exist_ok=True)
    returns: list[float] = []
    dates: list[str] = []
    previous: Decimal | None = None
    for point in report.equity_curve:
        if previous is not None and previous > Decimal("0"):
            returns.append(float(point.equity / previous) - 1.0)
            dates.append(point.close_time.date().isoformat())
        previous = point.equity
    path = returns_dir / f"trial-{trial_id:06d}.json"
    path.write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "first_return_date": dates[0] if dates else None,
                "last_return_date": dates[-1] if dates else None,
                "daily_returns": returns,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
