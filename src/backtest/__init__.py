"""Backtest composition layer: daily replay, trial registry, gate tooling."""

from src.backtest.engine import ZERO_QUANTITY_AFTER_ROUNDING, run_backtest
from src.backtest.holdout import (
    HoldoutState,
    HoldoutViolationError,
    initialize_holdout,
    load_holdout,
    require_data_allowed,
    spend_holdout,
)
from src.backtest.registry import (
    TrialRecord,
    TrialRegistryError,
    append_trial,
    config_hash_for,
    load_trials,
    trial_count,
)
from src.backtest.runner import (
    DEFAULT_HOLDOUT_DAYS,
    RegisteredBacktestResult,
    run_registered_backtest,
)
from src.backtest.types import (
    BacktestError,
    BacktestMetrics,
    BacktestParameters,
    BacktestReport,
    EquityPoint,
    SignalLogEntry,
    TargetLogEntry,
)
from src.backtest.validation import (
    DeflatedSharpeResult,
    PBOResult,
    ValidationInputError,
    deflated_sharpe_ratio,
    non_annualized_sharpe_variance,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

__all__ = [
    "DEFAULT_HOLDOUT_DAYS",
    "ZERO_QUANTITY_AFTER_ROUNDING",
    "BacktestError",
    "BacktestMetrics",
    "BacktestParameters",
    "BacktestReport",
    "DeflatedSharpeResult",
    "EquityPoint",
    "HoldoutState",
    "HoldoutViolationError",
    "PBOResult",
    "RegisteredBacktestResult",
    "SignalLogEntry",
    "TargetLogEntry",
    "TrialRecord",
    "TrialRegistryError",
    "ValidationInputError",
    "append_trial",
    "config_hash_for",
    "deflated_sharpe_ratio",
    "initialize_holdout",
    "load_holdout",
    "load_trials",
    "non_annualized_sharpe_variance",
    "probability_of_backtest_overfitting",
    "require_data_allowed",
    "run_backtest",
    "run_registered_backtest",
    "sharpe_ratio",
    "spend_holdout",
    "trial_count",
]
