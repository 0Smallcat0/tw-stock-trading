"""Deterministic paper broker for Core MVP virtual fills."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from src.domain import OrderIntent, OrderSide, RiskDecisionStatus, VirtualFill, VirtualOrder
from src.execution.ticks import tw_instrument_type, tw_tick_for
from src.execution.types import (
    BROKER_REJECTED_INSUFFICIENT_CASH,
    BROKER_REJECTED_INSUFFICIENT_HOLDINGS,
    BROKER_REJECTED_LEVERAGE_FORBIDDEN,
    BROKER_REJECTED_MARGIN_FORBIDDEN,
    BROKER_REJECTED_MIN_NOTIONAL,
    BROKER_REJECTED_NON_VIRTUAL_ORDER,
    BROKER_REJECTED_PRICE_TICK_VIOLATION,
    BROKER_REJECTED_PRIVATE_API_FORBIDDEN,
    BROKER_REJECTED_REAL_ORDERS_FORBIDDEN,
    BROKER_REJECTED_RISK_INTENT_MISMATCH,
    BROKER_REJECTED_RISK_NOT_APPROVED,
    BROKER_REJECTED_SYMBOL_MISMATCH,
    BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING,
    BrokerAcceptedOrder,
    BrokerAccountView,
    BrokerRejectedOrder,
    PaperBrokerParameters,
    PaperBrokerResult,
    PaperMarketPrice,
)

_BPS_DENOMINATOR = Decimal("10000")
# Matches src.data.adjustments.adjusted_price quantization: every adjusted
# price is a multiple of this quantum, so tick math stays exact when the
# bracket-tick check is disabled for adjusted-series replays.
_ADJUSTED_PRICE_QUANTUM = Decimal("0.000001")


class PaperBroker:
    """Paper-only broker that records accepted orders, rejects, and virtual fills."""

    def __init__(self, parameters: PaperBrokerParameters) -> None:
        self._parameters = parameters
        self._accepted_orders: list[BrokerAcceptedOrder] = []
        self._rejected_orders: list[BrokerRejectedOrder] = []
        self._fills: list[VirtualFill] = []

    @property
    def accepted_orders(self) -> tuple[BrokerAcceptedOrder, ...]:
        """Immutable view of accepted virtual orders."""

        return tuple(self._accepted_orders)

    @property
    def rejected_orders(self) -> tuple[BrokerRejectedOrder, ...]:
        """Immutable view of rejected virtual orders."""

        return tuple(self._rejected_orders)

    @property
    def fills(self) -> tuple[VirtualFill, ...]:
        """Immutable view of virtual fills."""

        return tuple(self._fills)

    def submit_order(
        self,
        order: object,
        *,
        market_price: PaperMarketPrice,
        account_view: BrokerAccountView,
        submitted_at: datetime,
    ) -> PaperBrokerResult:
        """Validate and simulate a virtual fill for one risk-approved order."""

        reason_codes = _forbidden_safety_reason_codes(self._parameters)
        if not isinstance(order, VirtualOrder):
            return self._reject(
                order_id=None,
                intent=None,
                rejected_at=submitted_at,
                reason_codes=(BROKER_REJECTED_NON_VIRTUAL_ORDER, *reason_codes),
            )

        instrument_type = tw_instrument_type(order.intent.symbol.value)
        price_tick = (
            tw_tick_for(market_price.price, instrument_type)
            if self._parameters.enforce_price_tick
            else _ADJUSTED_PRICE_QUANTUM
        )
        if order.risk_decision.status is not RiskDecisionStatus.APPROVED:
            reason_codes.append(BROKER_REJECTED_RISK_NOT_APPROVED)
        if order.risk_decision.intent != order.intent:
            reason_codes.append(BROKER_REJECTED_RISK_INTENT_MISMATCH)
        if market_price.symbol != order.intent.symbol:
            reason_codes.append(BROKER_REJECTED_SYMBOL_MISMATCH)
        if not _is_multiple(market_price.price, price_tick):
            reason_codes.append(BROKER_REJECTED_PRICE_TICK_VIOLATION)

        rounded_quantity = _round_down_to_step(
            order.intent.quantity, self._parameters.quantity_step
        )
        if rounded_quantity <= Decimal("0"):
            reason_codes.append(BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING)

        fill_price = _slippage_adjusted_price(
            side=order.intent.side,
            market_price=market_price.price,
            price_tick=price_tick,
            parameters=self._parameters,
        )
        gross_notional = rounded_quantity * fill_price
        fee = _commission_for(gross_notional, self._parameters)
        tax = _sell_tax_for(
            gross_notional,
            side=order.intent.side,
            instrument_type=instrument_type,
            parameters=self._parameters,
        )
        slippage = abs(fill_price - market_price.price) * rounded_quantity

        if gross_notional < self._parameters.min_notional:
            reason_codes.append(BROKER_REJECTED_MIN_NOTIONAL)

        if order.intent.side is OrderSide.BUY:
            required_cash = gross_notional + fee
            if required_cash > account_view.cash:
                reason_codes.append(BROKER_REJECTED_INSUFFICIENT_CASH)
        else:
            current_quantity = _position_quantity(account_view, order.intent.symbol.value)
            if current_quantity < rounded_quantity:
                reason_codes.append(BROKER_REJECTED_INSUFFICIENT_HOLDINGS)

        if reason_codes:
            return self._reject(
                order_id=order.order_id,
                intent=order.intent,
                rejected_at=submitted_at,
                reason_codes=tuple(dict.fromkeys(reason_codes)),
            )

        accepted_order = BrokerAcceptedOrder(
            order_id=order.order_id,
            intent=order.intent,
            accepted_at=submitted_at,
        )
        fill = VirtualFill(
            fill_id=f"{order.order_id}-fill",
            order_id=order.order_id,
            symbol=order.intent.symbol,
            side=order.intent.side,
            quantity=rounded_quantity,
            price=fill_price,
            fee=fee,
            slippage=slippage,
            filled_at=submitted_at,
            tax=tax,
        )
        self._accepted_orders.append(accepted_order)
        self._fills.append(fill)
        return PaperBrokerResult(
            accepted_order=accepted_order,
            rejected_order=None,
            fill=fill,
        )

    def _reject(
        self,
        *,
        order_id: str | None,
        intent: object,
        rejected_at: datetime,
        reason_codes: tuple[str, ...],
    ) -> PaperBrokerResult:
        rejected_order = BrokerRejectedOrder(
            order_id=order_id,
            intent=intent if isinstance(intent, OrderIntent) else None,
            rejected_at=rejected_at,
            reason_codes=reason_codes,
        )
        self._rejected_orders.append(rejected_order)
        return PaperBrokerResult(
            accepted_order=None,
            rejected_order=rejected_order,
            fill=None,
        )


def _forbidden_safety_reason_codes(parameters: PaperBrokerParameters) -> list[str]:
    reason_codes: list[str] = []
    if parameters.real_orders_enabled:
        reason_codes.append(BROKER_REJECTED_REAL_ORDERS_FORBIDDEN)
    if parameters.private_api_enabled:
        reason_codes.append(BROKER_REJECTED_PRIVATE_API_FORBIDDEN)
    if parameters.margin_enabled:
        reason_codes.append(BROKER_REJECTED_MARGIN_FORBIDDEN)
    if parameters.leverage_enabled:
        reason_codes.append(BROKER_REJECTED_LEVERAGE_FORBIDDEN)
    return reason_codes


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def _round_up_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def _is_multiple(value: Decimal, step: Decimal) -> bool:
    return value % step == Decimal("0")


def _slippage_adjusted_price(
    *,
    side: OrderSide,
    market_price: Decimal,
    price_tick: Decimal,
    parameters: PaperBrokerParameters,
) -> Decimal:
    multiplier = parameters.slippage_bps / _BPS_DENOMINATOR
    if side is OrderSide.BUY:
        return _round_up_to_step(market_price * (Decimal("1") + multiplier), price_tick)
    return _round_down_to_step(market_price * (Decimal("1") - multiplier), price_tick)


def _commission_for(gross_notional: Decimal, parameters: PaperBrokerParameters) -> Decimal:
    """Per-side brokerage commission with the minimum-fee floor."""

    commission = gross_notional * parameters.commission_bps / _BPS_DENOMINATOR
    return max(commission, parameters.min_fee)


def _sell_tax_for(
    gross_notional: Decimal,
    *,
    side: OrderSide,
    instrument_type: str,
    parameters: PaperBrokerParameters,
) -> Decimal:
    """Securities transaction tax: SELL side only, rate by instrument type."""

    if side is not OrderSide.SELL:
        return Decimal("0")
    return gross_notional * parameters.sell_tax_bps_for(instrument_type) / _BPS_DENOMINATOR


def _position_quantity(account_view: BrokerAccountView, symbol_value: str) -> Decimal:
    for position in account_view.positions:
        if position.symbol.value == symbol_value:
            return position.quantity
    return Decimal("0")
