from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.domain import (
    DomainValidationError,
    OrderIntent,
    OrderSide,
    Position,
    RiskDecision,
    RiskDecisionStatus,
    Symbol,
    VirtualOrder,
)
from src.execution import (
    BROKER_APPROVED,
    BROKER_REJECTED_INSUFFICIENT_CASH,
    BROKER_REJECTED_INSUFFICIENT_HOLDINGS,
    BROKER_REJECTED_LEVERAGE_FORBIDDEN,
    BROKER_REJECTED_MARGIN_FORBIDDEN,
    BROKER_REJECTED_MIN_NOTIONAL,
    BROKER_REJECTED_NON_VIRTUAL_ORDER,
    BROKER_REJECTED_PRICE_TICK_VIOLATION,
    BROKER_REJECTED_PRIVATE_API_FORBIDDEN,
    BROKER_REJECTED_REAL_ORDERS_FORBIDDEN,
    BROKER_REJECTED_SYMBOL_MISMATCH,
    BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING,
    BrokerAccountView,
    PaperBroker,
    PaperBrokerError,
    PaperBrokerParameters,
    PaperMarketPrice,
)


def _now() -> datetime:
    return datetime(2026, 5, 21, 0, 0, tzinfo=UTC)


def _symbol() -> Symbol:
    return Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def _second_symbol() -> Symbol:
    return Symbol(value="0056", base_asset="0056", quote_asset="TWD")


def _parameters(**overrides: object) -> PaperBrokerParameters:
    values = {
        "commission_bps": Decimal("10"),
        "min_fee": Decimal("0"),
        "sell_tax_bps_etf": Decimal("0"),
        "sell_tax_bps_stock": Decimal("0"),
        "slippage_bps": Decimal("5"),
        "quantity_step": Decimal("0.000001"),
        "min_notional": Decimal("10"),
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "margin_enabled": False,
        "leverage_enabled": False,
    }
    values.update(overrides)
    return PaperBrokerParameters(**values)  # type: ignore[arg-type]


def _virtual_order(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("0.01"),
) -> VirtualOrder:
    intent = OrderIntent(
        symbol=_symbol(),
        side=side,
        quantity=quantity,
        created_at=_now(),
    )
    decision = RiskDecision(
        intent=intent,
        status=RiskDecisionStatus.APPROVED,
        reason_codes=("RISK_APPROVED",),
        decided_at=_now(),
    )
    return VirtualOrder(
        order_id="order-1",
        intent=intent,
        risk_decision=decision,
        approved_at=_now(),
    )


def _market_price(price: Decimal = Decimal("50000.00")) -> PaperMarketPrice:
    return PaperMarketPrice(symbol=_symbol(), price=price, observed_at=_now())


def _account_view(
    *,
    cash: Decimal = Decimal("1000"),
    position_quantity: Decimal = Decimal("1"),
) -> BrokerAccountView:
    return BrokerAccountView(
        cash=cash,
        positions=(
            Position(
                symbol=_symbol(),
                quantity=position_quantity,
                average_entry_price=Decimal("49000"),
            ),
        ),
    )


def test_valid_risk_approved_buy_records_accepted_order_and_fill() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(quantity=Decimal("0.01")),
        market_price=_market_price(),
        account_view=_account_view(cash=Decimal("1000")),
        submitted_at=_now(),
    )

    assert result.accepted_order is not None
    assert result.fill is not None
    assert result.reason_codes == (BROKER_APPROVED,)
    assert result.fill.price == Decimal("50025.00")
    assert result.fill.fee == Decimal("0.500250")
    assert result.fill.slippage == Decimal("0.250000")
    assert broker.accepted_orders == (result.accepted_order,)
    assert broker.fills == (result.fill,)


def test_valid_risk_approved_sell_records_accepted_order_and_fill() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(side=OrderSide.SELL, quantity=Decimal("0.01")),
        market_price=_market_price(),
        account_view=_account_view(position_quantity=Decimal("0.02")),
        submitted_at=_now(),
    )

    assert result.accepted_order is not None
    assert result.fill is not None
    assert result.fill.price == Decimal("49975.00")
    assert result.fill.fee == Decimal("0.499750")
    assert result.fill.slippage == Decimal("0.250000")


def test_non_virtual_order_is_rejected() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        object(),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.fill is None
    assert result.rejected_order is not None
    assert result.reason_codes == (BROKER_REJECTED_NON_VIRTUAL_ORDER,)


def test_non_approved_risk_decision_cannot_construct_virtual_order() -> None:
    intent = OrderIntent(
        symbol=_symbol(),
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        created_at=_now(),
    )
    rejected_decision = RiskDecision(
        intent=intent,
        status=RiskDecisionStatus.REJECTED,
        reason_codes=("MIN_NOTIONAL_NOT_MET",),
        decided_at=_now(),
    )

    with pytest.raises(DomainValidationError, match="approved risk decision"):
        VirtualOrder(
            order_id="order-1",
            intent=intent,
            risk_decision=rejected_decision,
            approved_at=_now(),
        )


@pytest.mark.parametrize(
    ("flag_name", "reason_code"),
    [
        ("real_orders_enabled", BROKER_REJECTED_REAL_ORDERS_FORBIDDEN),
        ("private_api_enabled", BROKER_REJECTED_PRIVATE_API_FORBIDDEN),
        ("margin_enabled", BROKER_REJECTED_MARGIN_FORBIDDEN),
        ("leverage_enabled", BROKER_REJECTED_LEVERAGE_FORBIDDEN),
    ],
)
def test_forbidden_safety_flags_are_rejected(flag_name: str, reason_code: str) -> None:
    broker = PaperBroker(_parameters(**{flag_name: True}))

    result = broker.submit_order(
        _virtual_order(),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.fill is None
    assert result.rejected_order is not None
    assert result.reason_codes == (reason_code,)


def test_quantity_is_rounded_down_to_step() -> None:
    broker = PaperBroker(_parameters(quantity_step=Decimal("0.01")))

    result = broker.submit_order(
        _virtual_order(quantity=Decimal("0.019")),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.fill is not None
    assert result.fill.quantity == Decimal("0.01")


def test_zero_quantity_after_rounding_is_rejected() -> None:
    broker = PaperBroker(_parameters(quantity_step=Decimal("0.01")))

    result = broker.submit_order(
        _virtual_order(quantity=Decimal("0.009")),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.reason_codes == (
        BROKER_REJECTED_ZERO_QUANTITY_AFTER_ROUNDING,
        BROKER_REJECTED_MIN_NOTIONAL,
    )


def test_market_price_must_be_positive() -> None:
    with pytest.raises(PaperBrokerError, match="price"):
        PaperMarketPrice(symbol=_symbol(), price=Decimal("0"), observed_at=_now())


def test_public_market_price_must_respect_bracket_tick() -> None:
    # 0050 is an ETF; at price >= NT$50 the bracket tick is 0.05.
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(),
        market_price=_market_price(price=Decimal("50000.03")),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.reason_codes == (BROKER_REJECTED_PRICE_TICK_VIOLATION,)


def test_minimum_notional_violation_is_rejected() -> None:
    broker = PaperBroker(_parameters(min_notional=Decimal("20")))

    result = broker.submit_order(
        _virtual_order(quantity=Decimal("0.0002")),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert BROKER_REJECTED_MIN_NOTIONAL in result.reason_codes


def test_buy_with_insufficient_cash_is_rejected() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(quantity=Decimal("0.01")),
        market_price=_market_price(),
        account_view=_account_view(cash=Decimal("100")),
        submitted_at=_now(),
    )

    assert result.reason_codes == (BROKER_REJECTED_INSUFFICIENT_CASH,)


def test_sell_with_insufficient_holdings_is_rejected() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(side=OrderSide.SELL, quantity=Decimal("0.02")),
        market_price=_market_price(),
        account_view=_account_view(position_quantity=Decimal("0.01")),
        submitted_at=_now(),
    )

    assert result.reason_codes == (BROKER_REJECTED_INSUFFICIENT_HOLDINGS,)


def test_symbol_mismatch_is_rejected() -> None:
    broker = PaperBroker(_parameters())

    result = broker.submit_order(
        _virtual_order(),
        market_price=PaperMarketPrice(
            symbol=_second_symbol(),
            price=Decimal("3000.00"),
            observed_at=_now(),
        ),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    assert result.reason_codes == (BROKER_REJECTED_SYMBOL_MISMATCH,)


def test_broker_history_is_exposed_as_immutable_tuples() -> None:
    broker = PaperBroker(_parameters())
    result = broker.submit_order(
        _virtual_order(),
        market_price=_market_price(),
        account_view=_account_view(),
        submitted_at=_now(),
    )

    accepted_orders = broker.accepted_orders
    fills = broker.fills

    assert isinstance(accepted_orders, tuple)
    assert isinstance(fills, tuple)
    assert accepted_orders == (result.accepted_order,)
    assert fills == (result.fill,)
