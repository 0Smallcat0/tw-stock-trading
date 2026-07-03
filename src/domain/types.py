"""Core trading domain types for the paper-trading MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

type Money = Decimal
type Price = Decimal
type Quantity = Decimal
type Fee = Decimal


class DomainValidationError(ValueError):
    """Raised when a domain object would represent an invalid trading state."""


class Signal(Enum):
    """Long-only strategy signal."""

    LONG = "LONG"
    FLAT = "FLAT"


class OrderSide(Enum):
    """Virtual spot order side."""

    BUY = "BUY"
    SELL = "SELL"


class RiskDecisionStatus(Enum):
    """Pre-trade risk decision outcome."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


def _require_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        msg = f"{name} must be Decimal"
        raise DomainValidationError(msg)


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value < Decimal("0"):
        msg = f"{name} must not be negative"
        raise DomainValidationError(msg)


def _require_positive_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise DomainValidationError(msg)


def _require_utc_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise DomainValidationError(msg)


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must not be empty"
        raise DomainValidationError(msg)


def _require_order_side(name: str, value: OrderSide) -> None:
    if not isinstance(value, OrderSide):
        msg = f"{name} must be OrderSide"
        raise DomainValidationError(msg)


def _require_risk_decision_status(name: str, value: RiskDecisionStatus) -> None:
    if not isinstance(value, RiskDecisionStatus):
        msg = f"{name} must be RiskDecisionStatus"
        raise DomainValidationError(msg)


@dataclass(frozen=True, slots=True)
class Symbol:
    """Exchange-native trading symbol with explicit assets.

    TW securities use the numeric exchange code as both value and base asset
    (for example value ``0050``, base ``0050``, quote ``TWD``).
    """

    value: str
    base_asset: str
    quote_asset: str

    def __post_init__(self) -> None:
        _require_non_empty("value", self.value)
        _require_non_empty("base_asset", self.base_asset)
        _require_non_empty("quote_asset", self.quote_asset)


@dataclass(frozen=True, slots=True)
class Timeframe:
    """Market data candle timeframe."""

    value: str

    def __post_init__(self) -> None:
        _require_non_empty("value", self.value)


@dataclass(frozen=True, slots=True)
class Candle:
    """Closed or open OHLCV market candle."""

    symbol: Symbol
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open_price: Price
    high_price: Price
    low_price: Price
    close_price: Price
    volume: Quantity
    is_closed: bool

    def __post_init__(self) -> None:
        _require_utc_datetime("open_time", self.open_time)
        _require_utc_datetime("close_time", self.close_time)
        if self.close_time <= self.open_time:
            msg = "close_time must be after open_time"
            raise DomainValidationError(msg)
        _require_positive_decimal("open_price", self.open_price)
        _require_positive_decimal("high_price", self.high_price)
        _require_positive_decimal("low_price", self.low_price)
        _require_positive_decimal("close_price", self.close_price)
        _require_non_negative_decimal("volume", self.volume)


@dataclass(frozen=True, slots=True)
class Position:
    """Current long spot position for one symbol."""

    symbol: Symbol
    quantity: Quantity
    average_entry_price: Price

    def __post_init__(self) -> None:
        _require_non_negative_decimal("quantity", self.quantity)
        _require_non_negative_decimal("average_entry_price", self.average_entry_price)


@dataclass(frozen=True, slots=True)
class TargetPosition:
    """Desired long spot position for one symbol."""

    symbol: Symbol
    quantity: Quantity

    def __post_init__(self) -> None:
        _require_non_negative_decimal("quantity", self.quantity)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Desired virtual spot order before risk approval and simulated execution."""

    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    created_at: datetime

    def __post_init__(self) -> None:
        _require_order_side("side", self.side)
        _require_positive_decimal("quantity", self.quantity)
        _require_utc_datetime("created_at", self.created_at)

    def validate_against_position(self, position: Position) -> None:
        """Reject sell intents that would exceed the current long holding."""

        if self.symbol != position.symbol:
            msg = "order intent symbol must match position symbol"
            raise DomainValidationError(msg)
        if self.side is OrderSide.SELL and self.quantity > position.quantity:
            msg = "sell quantity cannot exceed current holding"
            raise DomainValidationError(msg)


@dataclass(frozen=True, slots=True)
class VirtualOrder:
    """Risk-approved virtual order for the paper broker."""

    order_id: str
    intent: OrderIntent
    risk_decision: RiskDecision
    approved_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("order_id", self.order_id)
        if not isinstance(self.intent, OrderIntent):
            msg = "intent must be OrderIntent"
            raise DomainValidationError(msg)
        if not isinstance(self.risk_decision, RiskDecision):
            msg = "risk_decision must be RiskDecision"
            raise DomainValidationError(msg)
        if self.risk_decision.intent != self.intent:
            msg = "risk decision intent must match virtual order intent"
            raise DomainValidationError(msg)
        if self.risk_decision.status is not RiskDecisionStatus.APPROVED:
            msg = "virtual order requires an approved risk decision"
            raise DomainValidationError(msg)
        _require_utc_datetime("approved_at", self.approved_at)


@dataclass(frozen=True, slots=True)
class VirtualFill:
    """Simulated fill for a virtual order."""

    fill_id: str
    order_id: str
    symbol: Symbol
    side: OrderSide
    quantity: Quantity
    price: Price
    fee: Fee
    slippage: Money
    filled_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("fill_id", self.fill_id)
        _require_non_empty("order_id", self.order_id)
        _require_order_side("side", self.side)
        _require_positive_decimal("quantity", self.quantity)
        _require_positive_decimal("price", self.price)
        _require_non_negative_decimal("fee", self.fee)
        _require_non_negative_decimal("slippage", self.slippage)
        _require_utc_datetime("filled_at", self.filled_at)


@dataclass(frozen=True, slots=True)
class VirtualAccountSnapshot:
    """Point-in-time virtual account state."""

    account_id: str
    cash: Money
    equity: Money
    positions: tuple[Position, ...]
    captured_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_non_negative_decimal("cash", self.cash)
        _require_non_negative_decimal("equity", self.equity)
        if not isinstance(self.positions, tuple):
            msg = "positions must be a tuple"
            raise DomainValidationError(msg)
        if any(not isinstance(position, Position) for position in self.positions):
            msg = "positions must contain Position values"
            raise DomainValidationError(msg)
        _require_utc_datetime("captured_at", self.captured_at)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Pre-trade risk decision for an order intent."""

    intent: OrderIntent
    status: RiskDecisionStatus
    reason_codes: tuple[str, ...]
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_risk_decision_status("status", self.status)
        if not isinstance(self.reason_codes, tuple):
            msg = "reason_codes must be a tuple"
            raise DomainValidationError(msg)
        if any(
            not isinstance(reason_code, str) or not reason_code for reason_code in self.reason_codes
        ):
            msg = "reason_codes must contain non-empty strings"
            raise DomainValidationError(msg)
        _require_utc_datetime("decided_at", self.decided_at)
        if self.status is not RiskDecisionStatus.APPROVED and not self.reason_codes:
            msg = "non-approved risk decision must include at least one reason code"
            raise DomainValidationError(msg)
