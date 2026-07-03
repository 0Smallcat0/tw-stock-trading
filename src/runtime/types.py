"""Signal runtime value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from src.domain import VirtualFill
from src.notify import NotificationEvent


class RuntimeEngineError(ValueError):
    """Raised when the signal runtime receives unusable inputs or state."""


@dataclass(frozen=True, slots=True)
class RuntimeParameters:
    """Caller-adapted parameters for the daily signal runtime."""

    risk_budgets: Mapping[str, Decimal]
    initial_cash: Decimal
    account_id: str
    fee_bps: Decimal
    slippage_bps: Decimal
    quantity_step: Decimal
    price_tick: Decimal
    min_notional_twd: Decimal
    max_drawdown_fraction: Decimal
    daily_loss_pause_fraction: Decimal
    disaster_single_day_drop_fraction: Decimal
    stale_data_max_age_seconds: int
    idempotency_namespace: str

    def __post_init__(self) -> None:
        if not isinstance(self.risk_budgets, Mapping) or not self.risk_budgets:
            msg = "risk_budgets must be a non-empty mapping"
            raise RuntimeEngineError(msg)
        if self.initial_cash <= Decimal("0"):
            msg = "initial_cash must be positive"
            raise RuntimeEngineError(msg)
        if not self.account_id.strip():
            msg = "account_id must not be empty"
            raise RuntimeEngineError(msg)
        if not self.idempotency_namespace.strip():
            msg = "idempotency_namespace must not be empty"
            raise RuntimeEngineError(msg)
        if self.stale_data_max_age_seconds <= 0:
            msg = "stale_data_max_age_seconds must be positive"
            raise RuntimeEngineError(msg)
        object.__setattr__(self, "risk_budgets", MappingProxyType(dict(self.risk_budgets)))


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Outcome of one runtime decision cycle."""

    processed: bool
    reason: str
    close_time: datetime | None
    notifications: tuple[NotificationEvent, ...]
    fills: tuple[VirtualFill, ...]
    rejection_reason_codes: tuple[tuple[str, tuple[str, ...]], ...]
    health_codes: tuple[str, ...]
    equity: Decimal | None
