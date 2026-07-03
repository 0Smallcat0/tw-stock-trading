"""Backtest value objects for the Core MVP daily replay engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from src.domain import VirtualFill
from src.execution import BrokerAcceptedOrder, BrokerRejectedOrder
from src.risk import RiskEvent


class BacktestError(ValueError):
    """Raised when backtest inputs or invariants are violated."""


@dataclass(frozen=True, slots=True)
class BacktestParameters:
    """Caller-adapted parameters for one daily backtest run."""

    risk_budgets: Mapping[str, Decimal]
    initial_cash: Decimal
    account_id: str
    fee_bps: Decimal
    slippage_bps: Decimal
    quantity_step: Decimal
    price_tick: Decimal
    min_notional_usdt: Decimal
    max_drawdown_fraction: Decimal
    daily_loss_pause_fraction: Decimal
    disaster_single_day_drop_fraction: Decimal
    stale_data_max_age_seconds: int
    cost_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not isinstance(self.risk_budgets, Mapping) or not self.risk_budgets:
            msg = "risk_budgets must be a non-empty mapping"
            raise BacktestError(msg)
        if self.initial_cash <= Decimal("0"):
            msg = "initial_cash must be positive"
            raise BacktestError(msg)
        if not self.account_id.strip():
            msg = "account_id must not be empty"
            raise BacktestError(msg)
        if self.cost_multiplier <= Decimal("0"):
            msg = "cost_multiplier must be positive"
            raise BacktestError(msg)
        if self.stale_data_max_age_seconds <= 0:
            msg = "stale_data_max_age_seconds must be positive"
            raise BacktestError(msg)
        object.__setattr__(self, "risk_budgets", MappingProxyType(dict(self.risk_budgets)))

    @property
    def effective_fee_bps(self) -> Decimal:
        """Fee bps after the cost-stress multiplier."""

        return self.fee_bps * self.cost_multiplier

    @property
    def effective_slippage_bps(self) -> Decimal:
        """Slippage bps after the cost-stress multiplier."""

        return self.slippage_bps * self.cost_multiplier


@dataclass(frozen=True, slots=True)
class SignalLogEntry:
    """One strategy decision recorded during replay."""

    symbol: str
    as_of: datetime
    exposure_fraction: Decimal
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TargetLogEntry:
    """One portfolio target set recorded during replay."""

    as_of: datetime
    target_weights: tuple[tuple[str, Decimal], ...]
    cash_weight: Decimal
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One equity curve point marked at a daily close."""

    close_time: datetime
    equity: Decimal
    drawdown: Decimal
    benchmark_equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Headline after-cost metrics for one replay."""

    final_equity: Decimal
    total_return_fraction: Decimal
    annualized_sharpe: Decimal
    max_drawdown_fraction: Decimal
    trade_count: int
    rejected_count: int
    total_fees: Decimal
    total_slippage: Decimal
    total_traded_notional: Decimal
    annualized_turnover: Decimal
    benchmark_final_equity: Decimal
    observation_days: int


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Full auditable output of one daily replay."""

    data_start: datetime
    data_end: datetime
    decision_days: int
    signals: tuple[SignalLogEntry, ...]
    targets: tuple[TargetLogEntry, ...]
    accepted_orders: tuple[BrokerAcceptedOrder, ...]
    fills: tuple[VirtualFill, ...]
    rejected_orders: tuple[BrokerRejectedOrder, ...]
    risk_rejections: tuple[tuple[str, datetime, tuple[str, ...]], ...]
    risk_events: tuple[RiskEvent, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: BacktestMetrics
    cost_assumptions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.data_end < self.data_start:
            msg = "data_end must not precede data_start"
            raise BacktestError(msg)
        object.__setattr__(self, "cost_assumptions", MappingProxyType(dict(self.cost_assumptions)))

    def to_json_dict(self) -> dict[str, object]:
        """JSON-serializable report for run artifacts."""

        return {
            "data_start": self.data_start.isoformat(),
            "data_end": self.data_end.isoformat(),
            "decision_days": self.decision_days,
            "signals": [
                {
                    "symbol": entry.symbol,
                    "as_of": entry.as_of.isoformat(),
                    "exposure_fraction": str(entry.exposure_fraction),
                    "reason_codes": list(entry.reason_codes),
                }
                for entry in self.signals
            ],
            "targets": [
                {
                    "as_of": entry.as_of.isoformat(),
                    "target_weights": {
                        symbol: str(weight) for symbol, weight in entry.target_weights
                    },
                    "cash_weight": str(entry.cash_weight),
                    "reason_codes": list(entry.reason_codes),
                }
                for entry in self.targets
            ],
            "accepted_orders": [
                {
                    "order_id": order.order_id,
                    "symbol": order.intent.symbol.value,
                    "side": order.intent.side.value,
                    "quantity": str(order.intent.quantity),
                    "accepted_at": order.accepted_at.isoformat(),
                }
                for order in self.accepted_orders
            ],
            "fills": [
                {
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "symbol": fill.symbol.value,
                    "side": fill.side.value,
                    "quantity": str(fill.quantity),
                    "price": str(fill.price),
                    "fee": str(fill.fee),
                    "slippage": str(fill.slippage),
                    "filled_at": fill.filled_at.isoformat(),
                }
                for fill in self.fills
            ],
            "rejected_orders": [
                {
                    "order_id": order.order_id,
                    "symbol": order.intent.symbol.value if order.intent else None,
                    "rejected_at": order.rejected_at.isoformat(),
                    "reason_codes": list(order.reason_codes),
                }
                for order in self.rejected_orders
            ],
            "risk_rejections": [
                {
                    "symbol": symbol,
                    "decided_at": decided_at.isoformat(),
                    "reason_codes": list(reason_codes),
                }
                for symbol, decided_at, reason_codes in self.risk_rejections
            ],
            "risk_events": [
                {
                    "symbol": event.symbol.value,
                    "event_type": event.event_type,
                    "observed_fraction": str(event.observed_fraction),
                    "threshold_fraction": str(event.threshold_fraction),
                    "occurred_at": event.occurred_at.isoformat(),
                    "reason_codes": list(event.reason_codes),
                }
                for event in self.risk_events
            ],
            "equity_curve": [
                {
                    "close_time": point.close_time.isoformat(),
                    "equity": str(point.equity),
                    "drawdown": str(point.drawdown),
                    "benchmark_equity": str(point.benchmark_equity),
                }
                for point in self.equity_curve
            ],
            "metrics": {
                "final_equity": str(self.metrics.final_equity),
                "total_return_fraction": str(self.metrics.total_return_fraction),
                "annualized_sharpe": str(self.metrics.annualized_sharpe),
                "max_drawdown_fraction": str(self.metrics.max_drawdown_fraction),
                "trade_count": self.metrics.trade_count,
                "rejected_count": self.metrics.rejected_count,
                "total_fees": str(self.metrics.total_fees),
                "total_slippage": str(self.metrics.total_slippage),
                "total_traded_notional": str(self.metrics.total_traded_notional),
                "annualized_turnover": str(self.metrics.annualized_turnover),
                "benchmark_final_equity": str(self.metrics.benchmark_final_equity),
                "observation_days": self.metrics.observation_days,
            },
            "cost_assumptions": dict(self.cost_assumptions),
        }


def require_utc(name: str, value: datetime) -> None:
    """Shared UTC-awareness guard for backtest inputs."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise BacktestError(msg)
