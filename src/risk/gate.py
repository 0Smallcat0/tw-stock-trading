"""Evaluate Core MVP virtual order intents against risk rules."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.domain import OrderIntent, OrderSide, RiskDecision, RiskDecisionStatus
from src.risk.types import RiskExchangeFilters, RiskGateContext, RiskGateError, RiskGateParameters

RISK_APPROVED = "RISK_APPROVED"
RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE = "RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE"

ACCOUNT_STOP = "ACCOUNT_STOP"

SHORT_EXPOSURE_FORBIDDEN = "SHORT_EXPOSURE_FORBIDDEN"
NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"
MISSING_RISK_CONTEXT = "MISSING_RISK_CONTEXT"
SELL_REQUIRES_HOLDING = "SELL_REQUIRES_HOLDING"
SELL_EXCEEDS_HOLDINGS = "SELL_EXCEEDS_HOLDINGS"
SAME_BAR_EXECUTION = "SAME_BAR_EXECUTION"
MISSING_EXCHANGE_FILTERS = "MISSING_EXCHANGE_FILTERS"
SYMBOL_NOT_TRADABLE = "SYMBOL_NOT_TRADABLE"
PRICE_TICK_VIOLATION = "PRICE_TICK_VIOLATION"
QUANTITY_STEP_VIOLATION = "QUANTITY_STEP_VIOLATION"
MIN_QUANTITY_NOT_MET = "MIN_QUANTITY_NOT_MET"
MIN_NOTIONAL_NOT_MET = "MIN_NOTIONAL_NOT_MET"
EXCHANGE_MIN_NOTIONAL_NOT_MET = "EXCHANGE_MIN_NOTIONAL_NOT_MET"

STALE_DATA = "STALE_DATA"
DRAWDOWN_PAUSE = "DRAWDOWN_PAUSE"
DAILY_LOSS_PAUSE = "DAILY_LOSS_PAUSE"
TRAILING_STOP = "TRAILING_STOP"

_STOP_CODES = frozenset({ACCOUNT_STOP})
_HARD_REJECTION_CODES = frozenset(
    {
        SHORT_EXPOSURE_FORBIDDEN,
        NEGATIVE_QUANTITY,
        MISSING_RISK_CONTEXT,
        SELL_REQUIRES_HOLDING,
        SELL_EXCEEDS_HOLDINGS,
        SAME_BAR_EXECUTION,
        MISSING_EXCHANGE_FILTERS,
        SYMBOL_NOT_TRADABLE,
        PRICE_TICK_VIOLATION,
        QUANTITY_STEP_VIOLATION,
        MIN_QUANTITY_NOT_MET,
        MIN_NOTIONAL_NOT_MET,
        EXCHANGE_MIN_NOTIONAL_NOT_MET,
    }
)
_PAUSE_CODES = frozenset({STALE_DATA, DRAWDOWN_PAUSE, DAILY_LOSS_PAUSE, TRAILING_STOP})
_REASON_ORDER = (
    ACCOUNT_STOP,
    SHORT_EXPOSURE_FORBIDDEN,
    NEGATIVE_QUANTITY,
    MISSING_RISK_CONTEXT,
    SELL_REQUIRES_HOLDING,
    SELL_EXCEEDS_HOLDINGS,
    SAME_BAR_EXECUTION,
    MISSING_EXCHANGE_FILTERS,
    SYMBOL_NOT_TRADABLE,
    PRICE_TICK_VIOLATION,
    QUANTITY_STEP_VIOLATION,
    MIN_QUANTITY_NOT_MET,
    MIN_NOTIONAL_NOT_MET,
    EXCHANGE_MIN_NOTIONAL_NOT_MET,
    STALE_DATA,
    DRAWDOWN_PAUSE,
    DAILY_LOSS_PAUSE,
    TRAILING_STOP,
)
_REASON_RANK = {reason_code: index for index, reason_code in enumerate(_REASON_ORDER)}


def evaluate_order_intent(
    intent: OrderIntent,
    *,
    context: RiskGateContext | None,
    parameters: RiskGateParameters | None,
) -> RiskDecision:
    """Return the deterministic risk decision for one virtual order intent."""

    if not isinstance(intent, OrderIntent):
        msg = "intent must be OrderIntent"
        raise RiskGateError(msg)

    reason_codes: list[str] = []
    decision_time = intent.created_at
    if context is not None and context.decision_time is not None:
        decision_time = context.decision_time

    if context is None or parameters is None:
        return RiskDecision(
            intent=intent,
            status=RiskDecisionStatus.REJECTED,
            reason_codes=(MISSING_RISK_CONTEXT,),
            decided_at=decision_time,
        )

    if context.risk_state is not None and context.risk_state.account_stop_active:
        reason_codes.append(ACCOUNT_STOP)

    _check_required_context(context, reason_codes)
    _check_long_only_order(intent, context, reason_codes)
    _check_timing(intent, context, reason_codes)
    _check_exchange_filters(intent, context, reason_codes)
    _check_notional(intent, context, parameters, reason_codes)
    _check_pauses(intent, context, parameters, reason_codes)

    ordered_reasons = _ordered_reason_codes(reason_codes)
    if _STOP_CODES.intersection(ordered_reasons):
        status = RiskDecisionStatus.STOPPED
    elif _HARD_REJECTION_CODES.intersection(ordered_reasons):
        status = RiskDecisionStatus.REJECTED
    elif _PAUSE_CODES.intersection(ordered_reasons):
        if _is_risk_reducing_sell(intent, context):
            status = RiskDecisionStatus.APPROVED
            ordered_reasons = (RISK_APPROVED, RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE)
        else:
            status = RiskDecisionStatus.PAUSED
    else:
        status = RiskDecisionStatus.APPROVED
        ordered_reasons = (RISK_APPROVED,)

    return RiskDecision(
        intent=intent,
        status=status,
        reason_codes=ordered_reasons,
        decided_at=decision_time,
    )


def _check_required_context(context: RiskGateContext, reason_codes: list[str]) -> None:
    if (
        context.account_snapshot is None
        or context.risk_state is None
        or context.reference_price is None
        or context.latest_market_data_at is None
        or context.decision_time is None
        or context.earliest_execution_time is None
        or context.risk_state.peak_equity is None
        or context.risk_state.start_of_day_equity is None
    ):
        reason_codes.append(MISSING_RISK_CONTEXT)


def _check_long_only_order(
    intent: OrderIntent, context: RiskGateContext, reason_codes: list[str]
) -> None:
    if intent.quantity <= Decimal("0"):
        reason_codes.append(NEGATIVE_QUANTITY)

    if intent.side is not OrderSide.SELL:
        return

    if context.current_position is None or context.current_position.symbol != intent.symbol:
        reason_codes.extend([SHORT_EXPOSURE_FORBIDDEN, SELL_REQUIRES_HOLDING])
        return
    if intent.quantity > context.current_position.quantity:
        reason_codes.extend([SHORT_EXPOSURE_FORBIDDEN, SELL_EXCEEDS_HOLDINGS])


def _check_timing(intent: OrderIntent, context: RiskGateContext, reason_codes: list[str]) -> None:
    if context.earliest_execution_time is None:
        return
    if intent.created_at < context.earliest_execution_time:
        reason_codes.append(SAME_BAR_EXECUTION)


def _check_exchange_filters(
    intent: OrderIntent, context: RiskGateContext, reason_codes: list[str]
) -> None:
    filters = context.exchange_filters
    if filters is None:
        reason_codes.append(MISSING_EXCHANGE_FILTERS)
        return
    if _filters_are_incomplete(filters):
        reason_codes.append(MISSING_EXCHANGE_FILTERS)
        return
    if filters.symbol != intent.symbol or filters.status != "TRADING":
        reason_codes.append(SYMBOL_NOT_TRADABLE)
    if not filters.is_spot_trading_allowed:
        reason_codes.append(SYMBOL_NOT_TRADABLE)

    reference_price = context.reference_price
    if reference_price is not None and not _is_multiple(reference_price, filters.price_tick_size):
        reason_codes.append(PRICE_TICK_VIOLATION)
    if not _is_multiple(intent.quantity, filters.quantity_step_size):
        reason_codes.append(QUANTITY_STEP_VIOLATION)
    if filters.min_quantity is not None and intent.quantity < filters.min_quantity:
        reason_codes.append(MIN_QUANTITY_NOT_MET)


def _check_notional(
    intent: OrderIntent,
    context: RiskGateContext,
    parameters: RiskGateParameters,
    reason_codes: list[str],
) -> None:
    if context.reference_price is None:
        return

    notional = intent.quantity * context.reference_price
    if notional < parameters.min_notional_twd:
        reason_codes.append(MIN_NOTIONAL_NOT_MET)

    filters = context.exchange_filters
    if filters is None or _filters_are_incomplete(filters):
        return
    if filters.min_notional is not None and notional < filters.min_notional:
        reason_codes.append(EXCHANGE_MIN_NOTIONAL_NOT_MET)


def _check_pauses(
    intent: OrderIntent,
    context: RiskGateContext,
    parameters: RiskGateParameters,
    reason_codes: list[str],
) -> None:
    if (
        context.latest_market_data_at is not None
        and context.decision_time is not None
        and context.decision_time - context.latest_market_data_at
        > timedelta(seconds=parameters.stale_data_max_age_seconds)
    ):
        reason_codes.append(STALE_DATA)

    if context.account_snapshot is None or context.risk_state is None:
        return

    current_equity = context.account_snapshot.equity
    peak_equity = context.risk_state.peak_equity
    if peak_equity is not None:
        drawdown_fraction = (peak_equity - current_equity) / peak_equity
        if drawdown_fraction >= parameters.max_drawdown_fraction:
            reason_codes.append(DRAWDOWN_PAUSE)

    start_of_day_equity = context.risk_state.start_of_day_equity
    if start_of_day_equity is not None:
        daily_loss_fraction = (start_of_day_equity - current_equity) / start_of_day_equity
        if daily_loss_fraction >= parameters.daily_loss_pause_fraction:
            reason_codes.append(DAILY_LOSS_PAUSE)

    if intent.symbol in context.risk_state.trailing_stop_active_symbols:
        reason_codes.append(TRAILING_STOP)


def _filters_are_incomplete(filters: RiskExchangeFilters) -> bool:
    return (
        filters.price_tick_size is None
        or filters.quantity_step_size is None
        or filters.min_quantity is None
        or filters.min_notional is None
    )


def _is_multiple(value: Decimal, step: Decimal | None) -> bool:
    if step is None:
        return False
    return value % step == Decimal("0")


def _is_risk_reducing_sell(intent: OrderIntent, context: RiskGateContext) -> bool:
    return (
        intent.side is OrderSide.SELL
        and context.current_position is not None
        and context.current_position.symbol == intent.symbol
        and intent.quantity <= context.current_position.quantity
    )


def _ordered_reason_codes(reason_codes: list[str]) -> tuple[str, ...]:
    unique_codes = set(reason_codes)
    return tuple(
        sorted(
            unique_codes,
            key=lambda reason_code: _REASON_RANK.get(reason_code, len(_REASON_ORDER)),
        )
    )
