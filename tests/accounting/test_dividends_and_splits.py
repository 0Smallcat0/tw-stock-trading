"""Dividend lifecycle and split-adjustment accounting tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.accounting import (
    AccountingError,
    LedgerEventType,
    VirtualAccountLedger,
    nhi_withholding_for,
)
from src.domain import OrderSide, Symbol, VirtualFill

_SYMBOL = Symbol(value="0050", base_asset="0050", quote_asset="TWD")
_OPENED_AT = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)


def _ledger_with_position(quantity: str = "1000", price: str = "100") -> VirtualAccountLedger:
    ledger = VirtualAccountLedger.open(
        account_id="paper-main", initial_cash=Decimal("200000"), opened_at=_OPENED_AT
    )
    fill = VirtualFill(
        fill_id="f-1",
        order_id="o-1",
        symbol=_SYMBOL,
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal("85.5"),
        slippage=Decimal("0"),
        filled_at=_OPENED_AT + timedelta(hours=1),
    )
    ledger.apply_fill(fill, mark_prices={_SYMBOL: Decimal(price)})
    return ledger


def test_sell_fill_tax_reduces_proceeds_and_realized_pnl() -> None:
    ledger = _ledger_with_position()
    sell = VirtualFill(
        fill_id="f-2",
        order_id="o-2",
        symbol=_SYMBOL,
        side=OrderSide.SELL,
        quantity=Decimal("1000"),
        price=Decimal("100"),
        fee=Decimal("85.5"),
        slippage=Decimal("0"),
        filled_at=_OPENED_AT + timedelta(days=1),
        tax=Decimal("100"),
    )

    state = ledger.apply_fill(sell, mark_prices={_SYMBOL: Decimal("100")})

    # proceeds = 100,000 - 85.5 fee - 100 tax
    assert state.cash == Decimal("200000") - Decimal("100000") - Decimal("85.5") + (
        Decimal("100000") - Decimal("85.5") - Decimal("100")
    )
    cash_events = [
        event for event in ledger.events if event.event_type is LedgerEventType.CASH_CHANGED
    ]
    assert cash_events[-1].tax == Decimal("100")


def test_dividend_accrues_into_equity_then_pays_out_as_cash() -> None:
    ledger = _ledger_with_position(quantity="1000", price="100")
    marks = {_SYMBOL: Decimal("99")}  # ex-date: raw price dropped by the dividend
    ex_date = _OPENED_AT + timedelta(days=10)

    accrued = ledger.accrue_dividend(
        symbol=_SYMBOL, amount_per_share=Decimal("1"), occurred_at=ex_date, mark_prices=marks
    )

    assert accrued.dividends_receivable == Decimal("1000")
    # Receivable offsets the ex-date price drop: equity holds steady.
    assert accrued.equity == accrued.cash + Decimal("99") * 1000 + Decimal("1000")

    paid = ledger.pay_dividend(
        symbol=_SYMBOL,
        gross_amount=Decimal("1000"),
        occurred_at=ex_date + timedelta(days=20),
        mark_prices=marks,
    )

    assert paid.dividends_receivable == Decimal("0")
    assert paid.cash == accrued.cash + Decimal("1000")
    kinds = [event.event_type for event in ledger.events]
    assert LedgerEventType.DIVIDEND_ACCRUED in kinds
    assert LedgerEventType.DIVIDEND_PAID in kinds


def test_dividend_payment_cannot_exceed_accrual() -> None:
    ledger = _ledger_with_position()
    with pytest.raises(AccountingError, match="accrued"):
        ledger.pay_dividend(
            symbol=_SYMBOL,
            gross_amount=Decimal("1"),
            occurred_at=_OPENED_AT + timedelta(days=1),
            mark_prices={_SYMBOL: Decimal("100")},
        )


def test_nhi_withholding_reduces_cash_and_is_recorded_as_tax() -> None:
    ledger = _ledger_with_position(quantity="30000", price="1")
    marks = {_SYMBOL: Decimal("1")}
    ex_date = _OPENED_AT + timedelta(days=10)
    ledger.accrue_dividend(
        symbol=_SYMBOL, amount_per_share=Decimal("1"), occurred_at=ex_date, mark_prices=marks
    )
    withholding = nhi_withholding_for(Decimal("30000"), enabled=True)
    assert withholding == Decimal("633.0000")  # 30,000 x 2.11%

    before = ledger.state.cash
    paid = ledger.pay_dividend(
        symbol=_SYMBOL,
        gross_amount=Decimal("30000"),
        occurred_at=ex_date + timedelta(days=20),
        mark_prices=marks,
        nhi_withholding=withholding,
    )

    assert paid.cash == before + Decimal("30000") - withholding
    paid_events = [
        event for event in ledger.events if event.event_type is LedgerEventType.DIVIDEND_PAID
    ]
    assert paid_events[-1].tax == withholding


def test_nhi_withholding_below_threshold_or_disabled_is_zero() -> None:
    assert nhi_withholding_for(Decimal("19999"), enabled=True) == Decimal("0")
    assert nhi_withholding_for(Decimal("30000"), enabled=False) == Decimal("0")


def test_split_multiplies_shares_divides_cost_and_keeps_equity() -> None:
    ledger = _ledger_with_position(quantity="1000", price="188.65")
    pre_state = ledger.state
    split_time = _OPENED_AT + timedelta(days=30)

    state = ledger.apply_split(
        symbol=_SYMBOL,
        share_multiplier=Decimal("4"),
        occurred_at=split_time,
        mark_prices={_SYMBOL: Decimal("47.1625")},  # post-split basis
    )

    position = state.positions[0]
    assert position.quantity == Decimal("4000")
    assert position.cost_basis == pre_state.positions[0].cost_basis
    assert position.average_entry_price == position.cost_basis / Decimal("4000")
    # 1000 x 188.65 == 4000 x 47.1625: marked equity is continuous.
    assert state.equity == pre_state.equity
    kinds = [event.event_type for event in ledger.events]
    assert LedgerEventType.SPLIT_ADJUSTED in kinds


def test_split_rejects_fractional_share_results() -> None:
    ledger = _ledger_with_position(quantity="3", price="100")
    with pytest.raises(AccountingError, match="fractional"):
        ledger.apply_split(
            symbol=_SYMBOL,
            share_multiplier=Decimal("0.5"),
            occurred_at=_OPENED_AT + timedelta(days=1),
            mark_prices={_SYMBOL: Decimal("200")},
        )
