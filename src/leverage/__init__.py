"""Leverage-route research: synthetic daily-2x series and rebalanced-hold backtest.

Evidence basis: `docs/research/TW2_UNCONSTRAINED_RESEARCH.md`. This package
exists to adjudicate the pre-registered leverage claim (buy-and-hold a fraction
of 0050 正2 plus cash, quarterly rebalanced) against 0050 total-return
buy-and-hold, including a synthetic 2008-GFC stress that the real 00631L (listed
2014) never lived through.
"""

from src.leverage.rebalance import (
    RebalanceParameters,
    RebalanceResult,
    simulate_rebalanced_hold,
)
from src.leverage.synthetic import (
    LEVERAGE_DAILY_DRAG_BPS,
    LeveragePoint,
    build_leveraged_series,
    calibrate_daily_drag_bps,
    load_underlying_total_return,
    splice_leverage_series,
)

__all__ = [
    "LEVERAGE_DAILY_DRAG_BPS",
    "LeveragePoint",
    "RebalanceParameters",
    "RebalanceResult",
    "build_leveraged_series",
    "calibrate_daily_drag_bps",
    "load_underlying_total_return",
    "simulate_rebalanced_hold",
    "splice_leverage_series",
]
