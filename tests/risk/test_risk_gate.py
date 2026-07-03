from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.domain import (
    OrderIntent,
    OrderSide,
    Position,
    RiskDecisionStatus,
    Symbol,
    VirtualAccountSnapshot,
)
from src.risk import (
    ACCOUNT_STOP,
    DAILY_LOSS_PAUSE,
    DRAWDOWN_PAUSE,
    EXCHANGE_MIN_NOTIONAL_NOT_MET,
    MIN_NOTIONAL_NOT_MET,
    MIN_QUANTITY_NOT_MET,
    MISSING_EXCHANGE_FILTERS,
    MISSING_RISK_CONTEXT,
    PRICE_TICK_VIOLATION,
    QUANTITY_STEP_VIOLATION,
    RISK_APPROVED,
    RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE,
    SAME_BAR_EXECUTION,
    SELL_EXCEEDS_HOLDINGS,
    SELL_REQUIRES_HOLDING,
    SHORT_EXPOSURE_FORBIDDEN,
    STALE_DATA,
    SYMBOL_NOT_TRADABLE,
    TRAILING_STOP,
    RiskExchangeFilters,
    RiskGateContext,
    RiskGateError,
    RiskGateParameters,
    RiskState,
    evaluate_order_intent,
)


def _symbol() -> Symbol:
    return Symbol(value="BTCUSDT", base_asset="BTC", quote_asset="USDT")


def _now() -> datetime:
    return datetime(2026, 5, 20, 0, 0, tzinfo=UTC)


_UNSET = object()


def _intent(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("0.01"),
    created_at: datetime | None = None,
) -> OrderIntent:
    return OrderIntent(
        symbol=_symbol(),
        side=side,
        quantity=quantity,
        created_at=created_at or _now(),
    )


def _position(quantity: Decimal = Decimal("1")) -> Position:
    return Position(
        symbol=_symbol(),
        quantity=quantity,
        average_entry_price=Decimal("50000"),
    )


def _account(equity: Decimal = Decimal("1000")) -> VirtualAccountSnapshot:
    return VirtualAccountSnapshot(
        account_id="paper-main",
        cash=equity,
        equity=equity,
        positions=(_position(),),
        captured_at=_now(),
    )


def _parameters(
    *,
    min_notional_twd: Decimal = Decimal("10"),
    max_drawdown_fraction: Decimal = Decimal("0.20"),
    daily_loss_pause_fraction: Decimal = Decimal("0.05"),
) -> RiskGateParameters:
    return RiskGateParameters(
        min_notional_twd=min_notional_twd,
        max_drawdown_fraction=max_drawdown_fraction,
        daily_loss_pause_fraction=daily_loss_pause_fraction,
    )


def _filters(
    *,
    status: str = "TRADING",
    is_spot_trading_allowed: bool = True,
    price_tick_size: Decimal | None = Decimal("0.01"),
    quantity_step_size: Decimal | None = Decimal("0.000001"),
    min_quantity: Decimal | None = Decimal("0.000001"),
    min_notional: Decimal | None = Decimal("10"),
) -> RiskExchangeFilters:
    return RiskExchangeFilters(
        symbol=_symbol(),
        status=status,
        is_spot_trading_allowed=is_spot_trading_allowed,
        price_tick_size=price_tick_size,
        quantity_step_size=quantity_step_size,
        min_quantity=min_quantity,
        min_notional=min_notional,
    )


def _risk_state(
    *,
    peak_equity: Decimal | None = Decimal("1000"),
    start_of_day_equity: Decimal | None = Decimal("1000"),
    account_stop_active: bool = False,
    trailing_stop_active_symbols: tuple[Symbol, ...] = (),
) -> RiskState:
    return RiskState(
        peak_equity=peak_equity,
        start_of_day_equity=start_of_day_equity,
        account_stop_active=account_stop_active,
        trailing_stop_active_symbols=trailing_stop_active_symbols,
    )


def _context(
    *,
    current_position: Position | None = None,
    account_snapshot: VirtualAccountSnapshot | None | object = _UNSET,
    reference_price: Decimal | None | object = _UNSET,
    latest_market_data_at: datetime | None | object = _UNSET,
    decision_time: datetime | None | object = _UNSET,
    earliest_execution_time: datetime | None | object = _UNSET,
    exchange_filters: RiskExchangeFilters | None | object = _UNSET,
    risk_state: RiskState | None | object = _UNSET,
    market_data_is_stale: bool = False,
) -> RiskGateContext:
    return RiskGateContext(
        current_position=current_position,
        account_snapshot=_account() if account_snapshot is _UNSET else account_snapshot,
        reference_price=Decimal("50000") if reference_price is _UNSET else reference_price,
        latest_market_data_at=_now() if latest_market_data_at is _UNSET else latest_market_data_at,
        decision_time=_now() if decision_time is _UNSET else decision_time,
        earliest_execution_time=_now()
        if earliest_execution_time is _UNSET
        else earliest_execution_time,
        exchange_filters=_filters() if exchange_filters is _UNSET else exchange_filters,
        risk_state=_risk_state() if risk_state is _UNSET else risk_state,
        market_data_is_stale=market_data_is_stale,
    )


def _evaluate(
    intent: OrderIntent,
    *,
    context: RiskGateContext | None = None,
    parameters: RiskGateParameters | None = None,
):
    return evaluate_order_intent(
        intent,
        context=context if context is not None else _context(),
        parameters=parameters if parameters is not None else _parameters(),
    )


def test_valid_buy_is_approved_with_reason_code() -> None:
    decision = _evaluate(_intent())

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.reason_codes == (RISK_APPROVED,)


def test_sell_with_no_matching_holding_is_rejected() -> None:
    decision = _evaluate(_intent(side=OrderSide.SELL))

    assert decision.status is RiskDecisionStatus.REJECTED
    assert SELL_REQUIRES_HOLDING in decision.reason_codes
    assert SHORT_EXPOSURE_FORBIDDEN in decision.reason_codes


def test_sell_with_wrong_symbol_holding_is_rejected() -> None:
    wrong_position = Position(
        symbol=Symbol(value="ETHUSDT", base_asset="ETH", quote_asset="USDT"),
        quantity=Decimal("1"),
        average_entry_price=Decimal("3000"),
    )

    decision = _evaluate(
        _intent(side=OrderSide.SELL),
        context=_context(current_position=wrong_position),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert SELL_REQUIRES_HOLDING in decision.reason_codes


def test_sell_above_holding_is_rejected() -> None:
    decision = _evaluate(
        _intent(side=OrderSide.SELL, quantity=Decimal("1.1")),
        context=_context(current_position=_position(quantity=Decimal("1"))),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert SELL_EXCEEDS_HOLDINGS in decision.reason_codes
    assert SHORT_EXPOSURE_FORBIDDEN in decision.reason_codes


@pytest.mark.parametrize(
    "context",
    [
        _context(account_snapshot=None),
        _context(risk_state=None),
        _context(reference_price=None),
        _context(latest_market_data_at=None),
        _context(decision_time=None),
        _context(earliest_execution_time=None),
        _context(risk_state=_risk_state(peak_equity=None)),
        _context(risk_state=_risk_state(start_of_day_equity=None)),
    ],
)
def test_missing_required_context_is_rejected(context: RiskGateContext) -> None:
    decision = _evaluate(_intent(), context=context)

    assert decision.status is RiskDecisionStatus.REJECTED
    assert MISSING_RISK_CONTEXT in decision.reason_codes


def test_invalid_risk_context_values_raise() -> None:
    with pytest.raises(RiskGateError, match="reference_price"):
        _context(reference_price=Decimal("0"))

    with pytest.raises(RiskGateError, match="peak_equity"):
        _risk_state(peak_equity=Decimal("0"))


def test_same_bar_execution_is_rejected_before_earliest_execution_time() -> None:
    generated_at = _now()
    executable_from_next_bar = generated_at + timedelta(milliseconds=1)

    decision = _evaluate(
        _intent(created_at=generated_at),
        context=_context(earliest_execution_time=executable_from_next_bar),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert SAME_BAR_EXECUTION in decision.reason_codes


def test_next_bar_execution_time_passes_timing_check() -> None:
    generated_at = _now()
    executable_from_next_bar = generated_at + timedelta(milliseconds=1)

    decision = _evaluate(
        _intent(created_at=executable_from_next_bar),
        context=_context(
            decision_time=executable_from_next_bar,
            earliest_execution_time=executable_from_next_bar,
        ),
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert SAME_BAR_EXECUTION not in decision.reason_codes


def test_missing_exchange_filters_make_symbol_untradable() -> None:
    decision = _evaluate(_intent(), context=_context(exchange_filters=None))

    assert decision.status is RiskDecisionStatus.REJECTED
    assert MISSING_EXCHANGE_FILTERS in decision.reason_codes


def test_incomplete_exchange_filters_make_symbol_untradable() -> None:
    decision = _evaluate(
        _intent(),
        context=_context(exchange_filters=_filters(min_notional=None)),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert MISSING_EXCHANGE_FILTERS in decision.reason_codes


@pytest.mark.parametrize(
    "filters",
    [
        _filters(status="HALT"),
        _filters(is_spot_trading_allowed=False),
    ],
)
def test_non_tradable_symbol_filters_are_rejected(filters: RiskExchangeFilters) -> None:
    decision = _evaluate(_intent(), context=_context(exchange_filters=filters))

    assert decision.status is RiskDecisionStatus.REJECTED
    assert SYMBOL_NOT_TRADABLE in decision.reason_codes


def test_price_tick_violation_is_rejected() -> None:
    decision = _evaluate(_intent(), context=_context(reference_price=Decimal("50000.005")))

    assert decision.status is RiskDecisionStatus.REJECTED
    assert PRICE_TICK_VIOLATION in decision.reason_codes


def test_quantity_step_violation_is_rejected() -> None:
    decision = _evaluate(
        _intent(quantity=Decimal("0.100005")),
        context=_context(exchange_filters=_filters(quantity_step_size=Decimal("0.01"))),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert QUANTITY_STEP_VIOLATION in decision.reason_codes


def test_minimum_quantity_violation_is_rejected() -> None:
    decision = _evaluate(
        _intent(quantity=Decimal("0.0005")),
        context=_context(exchange_filters=_filters(min_quantity=Decimal("0.001"))),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert MIN_QUANTITY_NOT_MET in decision.reason_codes


def test_configured_minimum_notional_violation_is_rejected() -> None:
    decision = _evaluate(
        _intent(quantity=Decimal("0.0002")),
        parameters=_parameters(min_notional_twd=Decimal("20")),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert MIN_NOTIONAL_NOT_MET in decision.reason_codes


def test_exchange_minimum_notional_violation_is_rejected() -> None:
    decision = _evaluate(
        _intent(quantity=Decimal("0.0002")),
        parameters=_parameters(min_notional_twd=Decimal("5")),
        context=_context(exchange_filters=_filters(min_notional=Decimal("11"))),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert EXCHANGE_MIN_NOTIONAL_NOT_MET in decision.reason_codes


def test_stale_data_pauses_new_buy() -> None:
    decision = _evaluate(
        _intent(),
        context=_context(market_data_is_stale=True),
    )

    assert decision.status is RiskDecisionStatus.PAUSED
    assert decision.reason_codes == (STALE_DATA,)


def test_drawdown_threshold_pauses_new_buy() -> None:
    decision = _evaluate(
        _intent(),
        context=_context(
            account_snapshot=_account(equity=Decimal("800")),
            risk_state=_risk_state(
                peak_equity=Decimal("1000"),
                start_of_day_equity=Decimal("800"),
            ),
        ),
    )

    assert decision.status is RiskDecisionStatus.PAUSED
    assert decision.reason_codes == (DRAWDOWN_PAUSE,)


def test_daily_loss_threshold_pauses_new_buy() -> None:
    decision = _evaluate(
        _intent(),
        context=_context(
            account_snapshot=_account(equity=Decimal("940")),
            risk_state=_risk_state(
                peak_equity=Decimal("1000"),
                start_of_day_equity=Decimal("1000"),
            ),
        ),
    )

    assert decision.status is RiskDecisionStatus.PAUSED
    assert DAILY_LOSS_PAUSE in decision.reason_codes


def test_account_stop_blocks_all_orders() -> None:
    decision = _evaluate(
        _intent(side=OrderSide.SELL),
        context=_context(
            current_position=_position(),
            risk_state=_risk_state(account_stop_active=True),
        ),
    )

    assert decision.status is RiskDecisionStatus.STOPPED
    assert decision.reason_codes == (ACCOUNT_STOP,)


def test_trailing_stop_pauses_new_buy() -> None:
    decision = _evaluate(
        _intent(),
        context=_context(risk_state=_risk_state(trailing_stop_active_symbols=(_symbol(),))),
    )

    assert decision.status is RiskDecisionStatus.PAUSED
    assert decision.reason_codes == (TRAILING_STOP,)


@pytest.mark.parametrize(
    "context",
    [
        _context(
            current_position=_position(),
            market_data_is_stale=True,
        ),
        _context(
            current_position=_position(),
            account_snapshot=_account(equity=Decimal("800")),
            risk_state=_risk_state(
                peak_equity=Decimal("1000"),
                start_of_day_equity=Decimal("800"),
            ),
        ),
        _context(
            current_position=_position(),
            account_snapshot=_account(equity=Decimal("940")),
            risk_state=_risk_state(
                peak_equity=Decimal("1000"),
                start_of_day_equity=Decimal("1000"),
            ),
        ),
        _context(
            current_position=_position(),
            risk_state=_risk_state(trailing_stop_active_symbols=(_symbol(),)),
        ),
    ],
)
def test_risk_reducing_sell_is_allowed_during_pause(context: RiskGateContext) -> None:
    decision = _evaluate(
        _intent(side=OrderSide.SELL, quantity=Decimal("0.1")),
        context=context,
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.reason_codes == (
        RISK_APPROVED,
        RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE,
    )


def test_account_stop_blocks_risk_reducing_sell() -> None:
    decision = _evaluate(
        _intent(side=OrderSide.SELL, quantity=Decimal("0.1")),
        context=_context(
            current_position=_position(),
            risk_state=_risk_state(account_stop_active=True),
        ),
    )

    assert decision.status is RiskDecisionStatus.STOPPED
    assert decision.reason_codes == (ACCOUNT_STOP,)


def test_reason_codes_are_deterministically_ordered() -> None:
    decision = _evaluate(
        _intent(side=OrderSide.SELL, created_at=_now(), quantity=Decimal("0.0002")),
        context=_context(
            current_position=None,
            market_data_is_stale=True,
            earliest_execution_time=_now() + timedelta(milliseconds=1),
            exchange_filters=None,
        ),
        parameters=_parameters(min_notional_twd=Decimal("20")),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_codes == (
        SHORT_EXPOSURE_FORBIDDEN,
        SELL_REQUIRES_HOLDING,
        SAME_BAR_EXECUTION,
        MISSING_EXCHANGE_FILTERS,
        MIN_NOTIONAL_NOT_MET,
        STALE_DATA,
    )


def test_account_stop_has_status_precedence_over_other_failures() -> None:
    decision = _evaluate(
        _intent(created_at=_now()),
        context=_context(
            earliest_execution_time=_now() + timedelta(milliseconds=1),
            risk_state=_risk_state(account_stop_active=True),
        ),
    )

    assert decision.status is RiskDecisionStatus.STOPPED
    assert decision.reason_codes[:2] == (ACCOUNT_STOP, SAME_BAR_EXECUTION)


def test_hard_rejection_has_status_precedence_over_pause() -> None:
    decision = _evaluate(
        _intent(created_at=_now()),
        context=_context(
            market_data_is_stale=True,
            earliest_execution_time=_now() + timedelta(milliseconds=1),
        ),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.reason_codes == (SAME_BAR_EXECUTION, STALE_DATA)


def test_goal_h_contract_records_goal_i_broker_enforcement_boundary() -> None:
    contract = Path("docs/contracts/RISK_GATE_CONTRACT.md").read_text(encoding="utf-8")

    assert "APPROVED risk decision" in contract
    assert "Goal I" in contract
