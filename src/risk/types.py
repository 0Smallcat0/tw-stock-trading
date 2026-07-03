"""Risk gate value objects for the Core MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.domain import Position, Symbol, VirtualAccountSnapshot


class RiskGateError(ValueError):
    """Raised when risk gate inputs cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class RiskGateParameters:
    """Risk thresholds copied into risk-local values by the composition layer.

    Staleness is a caller-provided FACT (``RiskGateContext.market_data_is_stale``),
    not a gate computation: under the TW exchange calendar only the composition
    layer can count trading days (weekends, holidays, typhoon closures add
    zero age), and `src.risk` must not import the data layer.
    """

    min_notional_twd: Decimal
    max_drawdown_fraction: Decimal
    daily_loss_pause_fraction: Decimal

    def __post_init__(self) -> None:
        _require_positive_decimal("min_notional_twd", self.min_notional_twd)
        _require_fraction("max_drawdown_fraction", self.max_drawdown_fraction)
        _require_fraction("daily_loss_pause_fraction", self.daily_loss_pause_fraction)


@dataclass(frozen=True, slots=True)
class RiskExchangeFilters:
    """Public exchange filter facts needed by the risk gate."""

    symbol: Symbol
    status: str
    is_spot_trading_allowed: bool
    price_tick_size: Decimal | None
    quantity_step_size: Decimal | None
    min_quantity: Decimal | None
    min_notional: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            msg = "symbol must be Symbol"
            raise RiskGateError(msg)
        if not isinstance(self.status, str) or not self.status.strip():
            msg = "status must not be empty"
            raise RiskGateError(msg)
        if not isinstance(self.is_spot_trading_allowed, bool):
            msg = "is_spot_trading_allowed must be bool"
            raise RiskGateError(msg)
        _require_optional_positive_decimal("price_tick_size", self.price_tick_size)
        _require_optional_positive_decimal("quantity_step_size", self.quantity_step_size)
        _require_optional_positive_decimal("min_quantity", self.min_quantity)
        _require_optional_positive_decimal("min_notional", self.min_notional)


@dataclass(frozen=True, slots=True)
class RiskState:
    """Caller-owned account risk state facts."""

    peak_equity: Decimal | None
    start_of_day_equity: Decimal | None
    account_stop_active: bool = False
    trailing_stop_active_symbols: tuple[Symbol, ...] = ()

    def __post_init__(self) -> None:
        _require_optional_positive_decimal("peak_equity", self.peak_equity)
        _require_optional_positive_decimal("start_of_day_equity", self.start_of_day_equity)
        if not isinstance(self.account_stop_active, bool):
            msg = "account_stop_active must be bool"
            raise RiskGateError(msg)
        if not isinstance(self.trailing_stop_active_symbols, tuple):
            msg = "trailing_stop_active_symbols must be a tuple"
            raise RiskGateError(msg)
        if any(not isinstance(symbol, Symbol) for symbol in self.trailing_stop_active_symbols):
            msg = "trailing_stop_active_symbols must contain Symbol values"
            raise RiskGateError(msg)


@dataclass(frozen=True, slots=True)
class RiskGateContext:
    """All caller-provided facts needed for one risk decision.

    ``market_data_is_stale`` is computed by the composition layer against the
    exchange trading calendar (trading-day age, not wall-clock seconds).
    """

    current_position: Position | None
    account_snapshot: VirtualAccountSnapshot | None
    reference_price: Decimal | None
    latest_market_data_at: datetime | None
    decision_time: datetime | None
    earliest_execution_time: datetime | None
    exchange_filters: RiskExchangeFilters | None
    risk_state: RiskState | None
    market_data_is_stale: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.market_data_is_stale, bool):
            msg = "market_data_is_stale must be bool"
            raise RiskGateError(msg)
        if self.current_position is not None and not isinstance(self.current_position, Position):
            msg = "current_position must be Position or None"
            raise RiskGateError(msg)
        if self.account_snapshot is not None and not isinstance(
            self.account_snapshot, VirtualAccountSnapshot
        ):
            msg = "account_snapshot must be VirtualAccountSnapshot or None"
            raise RiskGateError(msg)
        _require_optional_positive_decimal("reference_price", self.reference_price)
        _require_optional_utc_datetime("latest_market_data_at", self.latest_market_data_at)
        _require_optional_utc_datetime("decision_time", self.decision_time)
        _require_optional_utc_datetime("earliest_execution_time", self.earliest_execution_time)
        if self.exchange_filters is not None and not isinstance(
            self.exchange_filters, RiskExchangeFilters
        ):
            msg = "exchange_filters must be RiskExchangeFilters or None"
            raise RiskGateError(msg)
        if self.risk_state is not None and not isinstance(self.risk_state, RiskState):
            msg = "risk_state must be RiskState or None"
            raise RiskGateError(msg)


def _require_positive_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        msg = f"{name} must be a finite Decimal"
        raise RiskGateError(msg)
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise RiskGateError(msg)


def _require_optional_positive_decimal(name: str, value: Decimal | None) -> None:
    if value is None:
        return
    _require_positive_decimal(name, value)


def _require_fraction(name: str, value: Decimal) -> None:
    _require_positive_decimal(name, value)
    if value > Decimal("1"):
        msg = f"{name} must be at most 1"
        raise RiskGateError(msg)


def _require_optional_utc_datetime(name: str, value: datetime | None) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise RiskGateError(msg)
