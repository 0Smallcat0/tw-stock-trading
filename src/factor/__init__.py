"""Low-volatility cross-sectional factor research (TW4).

A pure measurement layer (floats, not account money) that composes public price
data only — no trading, config, or orchestration imports. Selection ranks a
survivorship-mitigated common-stock universe by trailing realized volatility;
the backtest marks the chosen basket to dividend-adjusted total return daily and
charges the TW cost stack at each monthly rebalance. Composition with the
validation gates and trial registry happens in scripts/, never here.

Contract: docs/research/TW4_LOWVOL_PREREGISTRATION.md (claim locked pre-backtest).
"""

from __future__ import annotations

from src.factor.lowvol import (
    FactorParameters,
    FactorResult,
    RawSeries,
    RebalancePlan,
    month_end_dates,
    run_backtest,
    select_holdings,
    select_schedule,
    simulate,
)

__all__ = [
    "FactorParameters",
    "FactorResult",
    "RawSeries",
    "RebalancePlan",
    "month_end_dates",
    "run_backtest",
    "select_holdings",
    "select_schedule",
    "simulate",
]
