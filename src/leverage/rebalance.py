"""Buy-and-hold-a-fraction-plus-cash with periodic rebalancing.

The product the user chose: hold ``fraction`` of equity in 0050 正2 and the
rest in cash, rebalanced on calendar-quarter boundaries. No signals, no
timing. Rebalancing trades pay the TW ETF cost stack (commission with a
minimum fee, the 0.1% sell-side transaction tax, slippage); the buy-and-hold
core pays nothing between rebalances. Cash may earn a yield (sensitivity
assumption; the registered claim is adjudicated at 0).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.leverage.synthetic import LeveragePoint

_BPS = Decimal("10000")
_DAYS_PER_YEAR = Decimal("365.25")


class RebalanceError(ValueError):
    """Raised when rebalance inputs are invalid."""


@dataclass(frozen=True, slots=True)
class RebalanceParameters:
    """Cost and policy inputs for a rebalanced-hold backtest."""

    fraction: Decimal
    initial_capital: Decimal = Decimal("100000")
    commission_bps: Decimal = Decimal("8.55")
    min_fee: Decimal = Decimal("20")
    sell_tax_bps: Decimal = Decimal("10")  # ETF transaction tax, sell side only
    slippage_bps: Decimal = Decimal("5")
    cash_yield_annual_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.fraction <= Decimal("1")):
            msg = "fraction must be in (0, 1]"
            raise RebalanceError(msg)
        if self.initial_capital <= Decimal("0"):
            msg = "initial_capital must be positive"
            raise RebalanceError(msg)
        for name in ("commission_bps", "min_fee", "sell_tax_bps", "slippage_bps"):
            if getattr(self, name) < Decimal("0"):
                msg = f"{name} must not be negative"
                raise RebalanceError(msg)


@dataclass(frozen=True, slots=True)
class RebalanceResult:
    """Equity curve and headline metrics for one rebalanced-hold run."""

    equity_curve: tuple[tuple[str, Decimal], ...]
    final_equity: Decimal
    cagr: Decimal
    max_drawdown_fraction: Decimal
    annualized_sharpe: Decimal
    rebalance_count: int
    total_costs: Decimal
    years: Decimal


def simulate_rebalanced_hold(
    leverage_series: Sequence[LeveragePoint],
    parameters: RebalanceParameters,
) -> RebalanceResult:
    """Simulate f-in-leverage + (1-f)-in-cash with quarterly rebalancing."""

    series = sorted(leverage_series, key=lambda p: p.trading_date)
    if len(series) < 2:
        msg = "leverage_series needs at least two points"
        raise RebalanceError(msg)

    fraction = parameters.fraction
    lev_value = parameters.initial_capital * fraction
    cash = parameters.initial_capital - lev_value
    total_costs = Decimal("0")
    rebalance_count = 0
    daily_cash_growth = Decimal("1") + parameters.cash_yield_annual_bps / _BPS / _DAYS_PER_YEAR

    equity_curve: list[tuple[str, Decimal]] = [
        (series[0].trading_date.isoformat(), lev_value + cash)
    ]
    for prev, point in zip(series, series[1:]):
        # Mark the leverage sleeve to the new NAV; accrue cash yield.
        lev_value = lev_value * (point.nav / prev.nav)
        if daily_cash_growth != Decimal("1"):
            cash = cash * daily_cash_growth

        # Rebalance on the first session of a new calendar quarter.
        if _is_quarter_start(prev.trading_date.month, point.trading_date.month) or (
            point.trading_date.year != prev.trading_date.year
            and _quarter(point.trading_date.month) != _quarter(prev.trading_date.month)
        ):
            cost = _rebalance_to_target(lev_value, cash, fraction, parameters)
            if cost is not None:
                new_lev, new_cash, trade_cost = cost
                lev_value, cash = new_lev, new_cash
                total_costs += trade_cost
                rebalance_count += 1

        equity_curve.append((point.trading_date.isoformat(), lev_value + cash))

    return _finalize(tuple(equity_curve), rebalance_count, total_costs, parameters)


def _quarter(month: int) -> int:
    return (month - 1) // 3


def _is_quarter_start(prev_month: int, cur_month: int) -> bool:
    return _quarter(cur_month) != _quarter(prev_month)


def _rebalance_to_target(
    lev_value: Decimal,
    cash: Decimal,
    fraction: Decimal,
    parameters: RebalanceParameters,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Trade the leverage sleeve back to its target weight; return costs.

    Returns None when the drift is below one minimum fee (not worth trading).
    """

    total = lev_value + cash
    target_lev = total * fraction
    delta = target_lev - lev_value
    if abs(delta) < parameters.min_fee:
        return None
    notional = abs(delta)
    commission = max(notional * parameters.commission_bps / _BPS, parameters.min_fee)
    slippage = notional * parameters.slippage_bps / _BPS
    if delta < Decimal("0"):
        # Selling leverage: commission + ETF transaction tax + slippage.
        tax = notional * parameters.sell_tax_bps / _BPS
        trade_cost = commission + tax + slippage
        new_lev = lev_value - notional
        new_cash = cash + notional - trade_cost
    else:
        # Buying leverage: commission + slippage, no transaction tax.
        trade_cost = commission + slippage
        if notional + trade_cost > cash:
            # Never let cash go negative: buy only what cash affords.
            affordable = (cash - trade_cost) if cash > trade_cost else Decimal("0")
            if affordable <= Decimal("0"):
                return None
            notional = affordable
            commission = max(notional * parameters.commission_bps / _BPS, parameters.min_fee)
            slippage = notional * parameters.slippage_bps / _BPS
            trade_cost = commission + slippage
        new_lev = lev_value + notional
        new_cash = cash - notional - trade_cost
    return new_lev, new_cash, trade_cost


def _finalize(
    equity_curve: tuple[tuple[str, Decimal], ...],
    rebalance_count: int,
    total_costs: Decimal,
    parameters: RebalanceParameters,
) -> RebalanceResult:
    from datetime import date

    first_date = date.fromisoformat(equity_curve[0][0])
    last_date = date.fromisoformat(equity_curve[-1][0])
    years = Decimal(str((last_date - first_date).days / 365.25))
    final_equity = equity_curve[-1][1]
    cagr = Decimal("0")
    if years > Decimal("0") and parameters.initial_capital > Decimal("0"):
        cagr = Decimal(
            str(float(final_equity / parameters.initial_capital) ** (1 / float(years)) - 1)
        )

    peak = equity_curve[0][1]
    max_dd = Decimal("0")
    daily_returns: list[float] = []
    prev = equity_curve[0][1]
    for _, equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > Decimal("0"):
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
        if prev > Decimal("0"):
            daily_returns.append(float(equity / prev) - 1.0)
        prev = equity

    sharpe = Decimal("0")
    periods_per_year = len(equity_curve) / float(years) if years > Decimal("0") else 0.0
    if len(daily_returns) >= 2 and periods_per_year > 0.0:
        stdev = statistics.stdev(daily_returns)
        if stdev > 0.0:
            sharpe = Decimal(
                str(round(statistics.fmean(daily_returns) / stdev * math.sqrt(periods_per_year), 6))
            )

    return RebalanceResult(
        equity_curve=equity_curve,
        final_equity=final_equity,
        cagr=cagr,
        max_drawdown_fraction=max_dd,
        annualized_sharpe=sharpe,
        rebalance_count=rebalance_count,
        total_costs=total_costs,
        years=years,
    )
