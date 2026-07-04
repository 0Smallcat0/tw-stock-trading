"""Paper broker value objects for Core MVP virtual execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.domain import OrderIntent, Position, Symbol, VirtualFill


class PaperBrokerError(ValueError):
    """Raised when paper broker inputs cannot be evaluated safely."""


BROKER_APPROVED = "BROKER_APPROVED"
BROKER_REJECTED_NON_VIRTUAL_ORDER = "BROKER_REJECTED_NON_VIRTUAL_ORDER"
BROKER_REJECTED_RISK_NOT_APPROVED = "BROKER_REJECTED_RISK_NOT_APPROVED"
BROKER_REJECTED_RISK_INTENT_MISMATCH = "BROKER_REJECTED_RISK_INTENT_MISMATCH"
BROKER_REJECTED_REAL_ORDERS_FORBIDDEN = "BROKER_REJECTED_REAL_ORDERS_FORBIDDEN"
BROKER_REJECTED_PRIVATE_API_FORBIDDEN = "BROKER_REJECTED_PRIVATE_API_FORBIDDEN"
BROKER_REJECTED_MARGIN_FORBIDDEN = "BROKER_REJECTED_MARGIN_FORBIDDEN"
BROKER_REJECTED_LEVERAGE_FORBIDDEN = "BROKER_REJECTED_LEVERAGE_FORBIDDEN"
BROKER_REJECTED_SYMBOL_MISMATCH = "BROKER_REJECTED_SYMBOL_MISMATCH"
BROKER_REJECTED_PRICE_TICK_VIOLATION = "BROKER_REJECTED_PRICE_TICK_VIOLATION"
BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING = "BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING"
BROKER_REJECTED_MIN_NOTIONAL = "BROKER_REJECTED_MIN_NOTIONAL"
BROKER_REJECTED_INSUFFICIENT_CASH = "BROKER_REJECTED_INSUFFICIENT_CASH"
BROKER_REJECTED_INSUFFICIENT_HOLDINGS = "BROKER_REJECTED_INSUFFICIENT_HOLDINGS"


@dataclass(frozen=True, slots=True)
class PaperBrokerParameters:
    """Caller-adapted paper execution parameters (TW cost model).

    ``commission_bps`` is the EFFECTIVE per-side brokerage rate (statutory
    ceiling × broker discount, computed at config time), floored per order by
    ``min_fee``. The securities transaction tax applies to SELLS only and
    depends on the instrument type (ETF 10bps / stock 30bps). Tick sizes are
    price-dependent brackets resolved by ``src.execution.ticks``, never a
    static parameter.
    """

    commission_bps: Decimal
    min_fee: Decimal
    sell_tax_bps_etf: Decimal
    sell_tax_bps_stock: Decimal
    slippage_bps: Decimal
    quantity_step: Decimal
    min_notional: Decimal
    real_orders_enabled: bool = False
    private_api_enabled: bool = False
    margin_enabled: bool = False
    leverage_enabled: bool = False
    # Exchange ticks exist on RAW prices only. Adjusted-series replays set
    # this False and use the adjusted-price quantum (1e-6) instead: factor-
    # scaled prices are intentionally off-tick and validating them against
    # bracket ticks rejects every order (observed on trial #1).
    enforce_price_tick: bool = True

    def __post_init__(self) -> None:
        _require_non_negative_decimal("commission_bps", self.commission_bps)
        _require_non_negative_decimal("min_fee", self.min_fee)
        _require_non_negative_decimal("sell_tax_bps_etf", self.sell_tax_bps_etf)
        _require_non_negative_decimal("sell_tax_bps_stock", self.sell_tax_bps_stock)
        _require_non_negative_decimal("slippage_bps", self.slippage_bps)
        _require_positive_decimal("quantity_step", self.quantity_step)
        _require_positive_decimal("min_notional", self.min_notional)
        _require_bool("real_orders_enabled", self.real_orders_enabled)
        _require_bool("private_api_enabled", self.private_api_enabled)
        _require_bool("margin_enabled", self.margin_enabled)
        _require_bool("leverage_enabled", self.leverage_enabled)

    def sell_tax_bps_for(self, instrument_type: str) -> Decimal:
        """Sell-side securities-transaction-tax rate for an instrument type."""

        if instrument_type == "etf":
            return self.sell_tax_bps_etf
        if instrument_type == "stock":
            return self.sell_tax_bps_stock
        msg = f"unknown instrument type: {instrument_type!r}"
        raise PaperBrokerError(msg)


@dataclass(frozen=True, slots=True)
class PaperMarketPrice:
    """Public market price used for one virtual fill simulation."""

    symbol: Symbol
    price: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            msg = "symbol must be Symbol"
            raise PaperBrokerError(msg)
        _require_positive_decimal("price", self.price)
        _require_utc_datetime("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class BrokerAccountView:
    """Execution-local account facts needed before simulating a fill."""

    cash: Decimal
    positions: tuple[Position, ...] = ()

    def __post_init__(self) -> None:
        _require_non_negative_decimal("cash", self.cash)
        if not isinstance(self.positions, tuple):
            msg = "positions must be a tuple"
            raise PaperBrokerError(msg)
        if any(not isinstance(position, Position) for position in self.positions):
            msg = "positions must contain Position values"
            raise PaperBrokerError(msg)


@dataclass(frozen=True, slots=True)
class BrokerAcceptedOrder:
    """Accepted virtual order audit record."""

    order_id: str
    intent: OrderIntent
    accepted_at: datetime
    reason_codes: tuple[str, ...] = (BROKER_APPROVED,)

    def __post_init__(self) -> None:
        _require_non_empty("order_id", self.order_id)
        if not isinstance(self.intent, OrderIntent):
            msg = "intent must be OrderIntent"
            raise PaperBrokerError(msg)
        _require_utc_datetime("accepted_at", self.accepted_at)
        _require_reason_codes("reason_codes", self.reason_codes)


@dataclass(frozen=True, slots=True)
class BrokerRejectedOrder:
    """Rejected virtual order audit record."""

    order_id: str | None
    intent: OrderIntent | None
    rejected_at: datetime
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.order_id is not None:
            _require_non_empty("order_id", self.order_id)
        if self.intent is not None and not isinstance(self.intent, OrderIntent):
            msg = "intent must be OrderIntent or None"
            raise PaperBrokerError(msg)
        _require_utc_datetime("rejected_at", self.rejected_at)
        _require_reason_codes("reason_codes", self.reason_codes)


@dataclass(frozen=True, slots=True)
class PaperBrokerResult:
    """One paper broker submission result."""

    accepted_order: BrokerAcceptedOrder | None
    rejected_order: BrokerRejectedOrder | None
    fill: VirtualFill | None

    def __post_init__(self) -> None:
        accepted = self.accepted_order is not None
        rejected = self.rejected_order is not None
        if accepted == rejected:
            msg = "result must contain exactly one accepted_order or rejected_order"
            raise PaperBrokerError(msg)
        if accepted and not isinstance(self.accepted_order, BrokerAcceptedOrder):
            msg = "accepted_order must be BrokerAcceptedOrder"
            raise PaperBrokerError(msg)
        if rejected and not isinstance(self.rejected_order, BrokerRejectedOrder):
            msg = "rejected_order must be BrokerRejectedOrder"
            raise PaperBrokerError(msg)
        if accepted:
            accepted_order = self.accepted_order
            fill = self.fill
            if not isinstance(fill, VirtualFill):
                msg = "accepted broker result must include a VirtualFill"
                raise PaperBrokerError(msg)
            if accepted_order is None:
                msg = "accepted broker result must include an accepted order"
                raise PaperBrokerError(msg)
            if fill.order_id != accepted_order.order_id:
                msg = "fill order_id must match accepted order"
                raise PaperBrokerError(msg)
        elif self.fill is not None:
            msg = "rejected broker result must not include a fill"
            raise PaperBrokerError(msg)

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Reason codes for the accepted or rejected result."""

        if self.accepted_order is not None:
            return self.accepted_order.reason_codes
        if self.rejected_order is None:
            msg = "broker result has no accepted or rejected order"
            raise PaperBrokerError(msg)
        return self.rejected_order.reason_codes


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must not be empty"
        raise PaperBrokerError(msg)


def _require_bool(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        msg = f"{name} must be bool"
        raise PaperBrokerError(msg)


def _require_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        msg = f"{name} must be a finite Decimal"
        raise PaperBrokerError(msg)


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value < Decimal("0"):
        msg = f"{name} must not be negative"
        raise PaperBrokerError(msg)


def _require_positive_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise PaperBrokerError(msg)


def _require_utc_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise PaperBrokerError(msg)


def _require_reason_codes(name: str, value: tuple[str, ...]) -> None:
    if not isinstance(value, tuple) or not value:
        msg = f"{name} must be a non-empty tuple"
        raise PaperBrokerError(msg)
    if any(not isinstance(reason_code, str) or not reason_code for reason_code in value):
        msg = f"{name} must contain non-empty strings"
        raise PaperBrokerError(msg)
