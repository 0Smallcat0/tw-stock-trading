"""TWSE tick-size brackets and instrument-type classification.

Verified brackets (docs/contracts/DATA_ADAPTER_TWSE.md appendix sources):

- common stocks: <10: 0.01 | 10-<50: 0.05 | 50-<100: 0.10 | 100-<500: 0.50 |
  500-<1000: 1 | >=1000: 5
- ETFs: <50: 0.01 | >=50: 0.05

Prices cross brackets over a 20-year series (0050 traded 30 → 188 → 47 →
108), so the tick is a function of price, never a static config constant.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from src.execution.types import PaperBrokerError

InstrumentType = Literal["etf", "stock"]

_STOCK_BRACKETS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.10")),
    (Decimal("500"), Decimal("0.50")),
    (Decimal("1000"), Decimal("1")),
)
_STOCK_TOP_TICK = Decimal("5")

_ETF_BRACKETS: tuple[tuple[Decimal, Decimal], ...] = ((Decimal("50"), Decimal("0.01")),)
_ETF_TOP_TICK = Decimal("0.05")


def tw_instrument_type(symbol_value: str) -> InstrumentType:
    """Classify a TWSE code: ETF codes start with ``00`` (0050, 006208, 00679B)."""

    cleaned = symbol_value.strip()
    if not cleaned:
        msg = "symbol_value must not be empty"
        raise PaperBrokerError(msg)
    return "etf" if cleaned.startswith("00") else "stock"


def tw_tick_for(price: Decimal, instrument_type: InstrumentType) -> Decimal:
    """Return the exchange tick size for a price level and instrument type."""

    if not isinstance(price, Decimal) or not price.is_finite() or price <= Decimal("0"):
        msg = "price must be a positive Decimal"
        raise PaperBrokerError(msg)
    brackets, top_tick = (
        (_ETF_BRACKETS, _ETF_TOP_TICK)
        if instrument_type == "etf"
        else (_STOCK_BRACKETS, _STOCK_TOP_TICK)
    )
    for upper_bound, tick in brackets:
        if price < upper_bound:
            return tick
    return top_tick
