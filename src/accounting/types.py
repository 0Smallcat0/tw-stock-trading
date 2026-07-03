"""Virtual account value objects for Core MVP accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from src.domain import Symbol


class AccountingError(ValueError):
    """Raised when virtual account state would become invalid."""


class LedgerEventType(Enum):
    """Append-only virtual ledger event type."""

    ACCOUNT_OPENED = "ACCOUNT_OPENED"
    CASH_CHANGED = "CASH_CHANGED"
    POSITION_CHANGED = "POSITION_CHANGED"
    REJECTED_ORDER_RECORDED = "REJECTED_ORDER_RECORDED"
    SNAPSHOT_MARKED = "SNAPSHOT_MARKED"
    DIVIDEND_ACCRUED = "DIVIDEND_ACCRUED"
    DIVIDEND_PAID = "DIVIDEND_PAID"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"


@dataclass(frozen=True, slots=True)
class AccountingPosition:
    """Average-cost long spot accounting position."""

    symbol: Symbol
    quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            msg = "symbol must be Symbol"
            raise AccountingError(msg)
        _require_non_negative_decimal("quantity", self.quantity)
        _require_non_negative_decimal("average_entry_price", self.average_entry_price)
        _require_non_negative_decimal("cost_basis", self.cost_basis)
        if self.quantity == Decimal("0") and self.cost_basis != Decimal("0"):
            msg = "zero quantity position must have zero cost basis"
            raise AccountingError(msg)
        if self.quantity > Decimal("0") and self.average_entry_price <= Decimal("0"):
            msg = "non-zero position average_entry_price must be positive"
            raise AccountingError(msg)


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One append-only virtual account ledger event."""

    event_id: str
    event_type: LedgerEventType
    account_id: str
    occurred_at: datetime
    reason_codes: tuple[str, ...]
    order_id: str | None = None
    fill_id: str | None = None
    symbol: Symbol | None = None
    cash_delta: Decimal = Decimal("0")
    position_quantity_delta: Decimal = Decimal("0")
    realized_pnl_delta: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    dividends_receivable_delta: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_non_empty("event_id", self.event_id)
        if not isinstance(self.event_type, LedgerEventType):
            msg = "event_type must be LedgerEventType"
            raise AccountingError(msg)
        _require_non_empty("account_id", self.account_id)
        _require_utc_datetime("occurred_at", self.occurred_at)
        _require_reason_codes("reason_codes", self.reason_codes)
        if self.order_id is not None:
            _require_non_empty("order_id", self.order_id)
        if self.fill_id is not None:
            _require_non_empty("fill_id", self.fill_id)
        if self.symbol is not None and not isinstance(self.symbol, Symbol):
            msg = "symbol must be Symbol or None"
            raise AccountingError(msg)
        _require_decimal("cash_delta", self.cash_delta)
        _require_decimal("position_quantity_delta", self.position_quantity_delta)
        _require_decimal("realized_pnl_delta", self.realized_pnl_delta)
        _require_non_negative_decimal("fee", self.fee)
        _require_non_negative_decimal("slippage", self.slippage)
        _require_non_negative_decimal("tax", self.tax)
        _require_decimal("dividends_receivable_delta", self.dividends_receivable_delta)


@dataclass(frozen=True, slots=True)
class AccountState:
    """Point-in-time virtual account accounting state.

    ``dividends_receivable`` holds gross dividends between the ex-date (when
    the raw price already dropped) and the payment date; it counts toward
    equity so the scoreboard never shows a phantom loss across that window.
    """

    account_id: str
    cash: Decimal
    positions: tuple[AccountingPosition, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    updated_at: datetime
    dividends_receivable: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_non_negative_decimal("cash", self.cash)
        _require_non_negative_decimal("dividends_receivable", self.dividends_receivable)
        if not isinstance(self.positions, tuple):
            msg = "positions must be a tuple"
            raise AccountingError(msg)
        if any(not isinstance(position, AccountingPosition) for position in self.positions):
            msg = "positions must contain AccountingPosition values"
            raise AccountingError(msg)
        _require_decimal("realized_pnl", self.realized_pnl)
        _require_decimal("unrealized_pnl", self.unrealized_pnl)
        _require_non_negative_decimal("equity", self.equity)
        _require_positive_decimal("peak_equity", self.peak_equity)
        _require_non_negative_decimal("drawdown", self.drawdown)
        if self.drawdown > Decimal("1"):
            msg = "drawdown must be at most 1"
            raise AccountingError(msg)
        _require_utc_datetime("updated_at", self.updated_at)


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must not be empty"
        raise AccountingError(msg)


def _require_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        msg = f"{name} must be a finite Decimal"
        raise AccountingError(msg)


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value < Decimal("0"):
        msg = f"{name} must not be negative"
        raise AccountingError(msg)


def _require_positive_decimal(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise AccountingError(msg)


def _require_utc_datetime(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise AccountingError(msg)


def _require_reason_codes(name: str, value: tuple[str, ...]) -> None:
    if not isinstance(value, tuple) or not value:
        msg = f"{name} must be a non-empty tuple"
        raise AccountingError(msg)
    if any(not isinstance(reason_code, str) or not reason_code for reason_code in value):
        msg = f"{name} must contain non-empty strings"
        raise AccountingError(msg)
