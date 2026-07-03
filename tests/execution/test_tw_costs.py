"""TW cost-model tests: bracket ticks, min-fee floor, sell-only tax."""

from __future__ import annotations

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
    VirtualFill,
    VirtualOrder,
)
from src.execution import (
    BrokerAccountView,
    PaperBroker,
    PaperBrokerError,
    PaperBrokerParameters,
    PaperMarketPrice,
    tw_instrument_type,
    tw_tick_for,
)

_ETF = Symbol(value="0050", base_asset="0050", quote_asset="TWD")
_STOCK = Symbol(value="2330", base_asset="2330", quote_asset="TWD")


def _now() -> datetime:
    return datetime(2026, 7, 3, 1, 0, tzinfo=UTC)


def _parameters(**overrides: object) -> PaperBrokerParameters:
    values = {
        "commission_bps": Decimal("8.55"),
        "min_fee": Decimal("20"),
        "sell_tax_bps_etf": Decimal("10"),
        "sell_tax_bps_stock": Decimal("30"),
        "slippage_bps": Decimal("0"),
        "quantity_step": Decimal("1"),
        "min_notional": Decimal("1000"),
    }
    values.update(overrides)
    return PaperBrokerParameters(**values)  # type: ignore[arg-type]


def _order(symbol: Symbol, side: OrderSide, quantity: str) -> VirtualOrder:
    intent = OrderIntent(symbol=symbol, side=side, quantity=Decimal(quantity), created_at=_now())
    decision = RiskDecision(
        intent=intent,
        status=RiskDecisionStatus.APPROVED,
        reason_codes=("RISK_APPROVED",),
        decided_at=_now(),
    )
    return VirtualOrder(order_id="o-1", intent=intent, risk_decision=decision, approved_at=_now())


def _account(symbol: Symbol, quantity: str, cash: str = "1000000") -> BrokerAccountView:
    return BrokerAccountView(
        cash=Decimal(cash),
        positions=(
            Position(
                symbol=symbol,
                quantity=Decimal(quantity),
                average_entry_price=Decimal("100"),
            ),
        ),
    )


def test_instrument_classification_by_code() -> None:
    assert tw_instrument_type("0050") == "etf"
    assert tw_instrument_type("006208") == "etf"
    assert tw_instrument_type("00679B") == "etf"
    assert tw_instrument_type("2330") == "stock"
    with pytest.raises(PaperBrokerError):
        tw_instrument_type("  ")


@pytest.mark.parametrize(
    ("price", "instrument", "tick"),
    (
        ("9.99", "stock", "0.01"),
        ("10", "stock", "0.05"),
        ("49.95", "stock", "0.05"),
        ("50", "stock", "0.10"),
        ("99.9", "stock", "0.10"),
        ("100", "stock", "0.50"),
        ("499.5", "stock", "0.50"),
        ("500", "stock", "1"),
        ("999", "stock", "1"),
        ("1000", "stock", "5"),
        ("2465", "stock", "5"),
        ("49.99", "etf", "0.01"),
        ("50", "etf", "0.05"),
        ("108.35", "etf", "0.05"),
    ),
)
def test_tick_brackets(price: str, instrument: str, tick: str) -> None:
    assert tw_tick_for(Decimal(price), instrument) == Decimal(tick)  # type: ignore[arg-type]


def test_commission_hits_the_minimum_fee_floor_on_small_orders() -> None:
    broker = PaperBroker(_parameters())
    # 20 shares x NT$100 = NT$2,000 gross; 8.55bps commission = NT$1.71 < NT$20.
    result = broker.submit_order(
        _order(_ETF, OrderSide.BUY, "20"),
        market_price=PaperMarketPrice(symbol=_ETF, price=Decimal("100.00"), observed_at=_now()),
        account_view=_account(_ETF, "0"),
        submitted_at=_now(),
    )

    assert result.fill is not None
    assert result.fill.fee == Decimal("20")
    assert result.fill.tax == Decimal("0")


def test_large_orders_pay_rate_based_commission() -> None:
    broker = PaperBroker(_parameters())
    # 1000 shares x NT$100 = NT$100,000 gross; 8.55bps = NT$85.5 > NT$20.
    result = broker.submit_order(
        _order(_ETF, OrderSide.BUY, "1000"),
        market_price=PaperMarketPrice(symbol=_ETF, price=Decimal("100.00"), observed_at=_now()),
        account_view=_account(_ETF, "0"),
        submitted_at=_now(),
    )

    assert result.fill is not None
    assert result.fill.fee == Decimal("85.5000")


def test_etf_sell_carries_10bps_tax() -> None:
    broker = PaperBroker(_parameters())
    result = broker.submit_order(
        _order(_ETF, OrderSide.SELL, "1000"),
        market_price=PaperMarketPrice(symbol=_ETF, price=Decimal("100.00"), observed_at=_now()),
        account_view=_account(_ETF, "1000"),
        submitted_at=_now(),
    )

    assert result.fill is not None
    assert result.fill.tax == Decimal("100.0000")  # 100,000 x 10bps
    assert result.fill.fee == Decimal("85.5000")


def test_stock_sell_carries_30bps_tax() -> None:
    broker = PaperBroker(_parameters())
    result = broker.submit_order(
        _order(_STOCK, OrderSide.SELL, "100"),
        market_price=PaperMarketPrice(symbol=_STOCK, price=Decimal("1000"), observed_at=_now()),
        account_view=_account(_STOCK, "100"),
        submitted_at=_now(),
    )

    assert result.fill is not None
    assert result.fill.tax == Decimal("300.0000")  # 100,000 x 30bps


def test_buy_fills_never_carry_tax_by_domain_rule() -> None:
    with pytest.raises(DomainValidationError, match="tax"):
        VirtualFill(
            fill_id="f-1",
            order_id="o-1",
            symbol=_ETF,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("1"),
            slippage=Decimal("0"),
            filled_at=_now(),
            tax=Decimal("1"),
        )


def test_stock_prices_validate_against_their_own_bracket() -> None:
    broker = PaperBroker(_parameters())
    # 2330 at NT$2,465: tick 5 — NT$2,467 is not a valid tick.
    result = broker.submit_order(
        _order(_STOCK, OrderSide.BUY, "100"),
        market_price=PaperMarketPrice(symbol=_STOCK, price=Decimal("2467"), observed_at=_now()),
        account_view=_account(_STOCK, "0"),
        submitted_at=_now(),
    )

    assert result.rejected_order is not None
    assert "BROKER_REJECTED_PRICE_TICK_VIOLATION" in result.reason_codes
