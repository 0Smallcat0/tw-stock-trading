"""Low-volatility cross-sectional selection and rebalanced backtest.

Measurement domain: floats, not account money (like src.backtest.validation).
Selection uses RAW prices (clipped returns for the vol estimate, turnover for the
liquidity gate); the P&L marks the chosen basket to DIVIDEND-ADJUSTED total return
so the comparison against 0050 total-return is apples-to-apples. Costs follow the
TW stack: commission (bps, min fee), sell-side securities-transaction tax, slippage.

All parameters are frozen by docs/research/TW4_LOWVOL_PREREGISTRATION.md.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

_BPS = 10_000.0
_TRADING_DAYS_YEAR = 252.0


class FactorError(ValueError):
    """Raised on invalid factor-backtest inputs."""


@dataclass(frozen=True, slots=True)
class FactorParameters:
    """Frozen low-volatility policy + TW cost stack (see pre-registration)."""

    n: int = 50
    lookback_days: int = 252
    weight: str = "equal"  # "equal" | "inverse_vol"
    liquidity_floor: float = 50_000_000.0
    liquidity_window: int = 60
    min_price: float = 5.0
    return_clip: float = 0.10
    commission_bps: float = 8.55
    min_fee: float = 20.0
    sell_tax_bps: float = 30.0
    slippage_bps: float = 15.0
    signal_lag_days: int = 1
    initial_capital: float = 10_000_000.0
    cash_yield_annual_bps: float = 100.0

    def __post_init__(self) -> None:
        if self.n < 1:
            raise FactorError("n must be >= 1")
        if self.lookback_days < 2:
            raise FactorError("lookback_days must be >= 2")
        if self.weight not in ("equal", "inverse_vol"):
            raise FactorError("weight must be 'equal' or 'inverse_vol'")
        if self.signal_lag_days < 0:
            raise FactorError("signal_lag_days must be >= 0")
        if self.initial_capital <= 0.0:
            raise FactorError("initial_capital must be positive")


@dataclass(frozen=True, slots=True)
class RawSeries:
    """One stock's raw daily series (ascending, equal-length tuples)."""

    stock_id: str
    dates: tuple[date, ...]
    close: tuple[float, ...]
    turnover: tuple[float, ...]

    def __post_init__(self) -> None:
        if not (len(self.dates) == len(self.close) == len(self.turnover)):
            raise FactorError(f"{self.stock_id}: dates/close/turnover length mismatch")


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    """A dated selection: decide on ``rebalance_date``, trade on ``entry_date``."""

    rebalance_date: date
    entry_date: date
    weights: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class FactorResult:
    """Equity curve and headline metrics for one config."""

    equity_curve: tuple[tuple[date, float], ...]
    cagr: float
    max_drawdown: float
    annualized_sharpe: float
    daily_returns: tuple[float, ...]
    rebalances: int
    avg_names: float
    annual_turnover: float
    total_cost: float
    periods_per_year: float
    start: date
    end: date


def month_end_dates(dates: Sequence[date]) -> tuple[date, ...]:
    """Last trading day present in each calendar (year, month)."""

    last: dict[tuple[int, int], date] = {}
    for day in dates:
        key = (day.year, day.month)
        if key not in last or day > last[key]:
            last[key] = day
    return tuple(sorted(last.values()))


def select_holdings(
    candidates: Mapping[str, tuple[float, float, float]],
    parameters: FactorParameters,
) -> dict[str, float]:
    """Pick the lowest-vol names passing the floors; return weights summing to 1.

    ``candidates`` maps stock_id -> (volatility, price, liquidity). A name is
    eligible when price >= min_price and liquidity >= liquidity_floor and its
    volatility is finite and positive. The lowest-volatility ``n`` (or fewer, if
    the eligible set is smaller) are held; empty when nothing qualifies.
    """

    eligible: list[tuple[str, float]] = []
    for stock_id, (vol, price, liquidity) in candidates.items():
        if not math.isfinite(vol) or vol <= 0.0:
            continue
        if price < parameters.min_price or liquidity < parameters.liquidity_floor:
            continue
        eligible.append((stock_id, vol))
    if not eligible:
        return {}
    eligible.sort(key=lambda item: (item[1], item[0]))
    chosen = eligible[: parameters.n]
    if parameters.weight == "equal":
        weight = 1.0 / len(chosen)
        return {stock_id: weight for stock_id, _ in chosen}
    inverse = [(stock_id, 1.0 / vol) for stock_id, vol in chosen]
    total = sum(value for _, value in inverse)
    return {stock_id: value / total for stock_id, value in inverse}


def _metrics_at_month_ends(
    series: RawSeries,
    month_ends: frozenset[date],
    parameters: FactorParameters,
) -> dict[date, tuple[float, float, float]]:
    """(vol, price, liquidity) at each month-end where history is sufficient.

    Vol = stdev of the trailing ``lookback_days`` simple returns, each clipped to
    +/- ``return_clip`` to neutralize ex-dividend/split single-day artifacts.
    Liquidity = median of the trailing ``liquidity_window`` daily turnovers.
    """

    close = series.close
    turnover = series.turnover
    clip = parameters.return_clip
    n = len(close)
    # Clipped daily returns as prefix sums so a windowed sample variance is O(1)
    # instead of an O(lookback) stdev at every month-end (the hot path).
    prefix = [0.0] * n
    prefix_sq = [0.0] * n
    for i in range(1, n):
        prev = close[i - 1]
        raw = (close[i] / prev - 1.0) if prev > 0.0 else 0.0
        r = clip if raw > clip else (-clip if raw < -clip else raw)
        prefix[i] = prefix[i - 1] + r
        prefix_sq[i] = prefix_sq[i - 1] + r * r

    look = parameters.lookback_days
    liq_win = parameters.liquidity_window
    out: dict[date, tuple[float, float, float]] = {}
    for i, day in enumerate(series.dates):
        if day not in month_ends:
            continue
        if i < look or i < liq_win - 1 or close[i] <= 0.0:
            continue
        # sample variance (ddof=1) of returns[i-look+1 .. i] via prefix sums
        total = prefix[i] - prefix[i - look]
        total_sq = prefix_sq[i] - prefix_sq[i - look]
        var = (total_sq - total * total / look) / (look - 1)
        vol = var**0.5 if var > 0.0 else 0.0
        window = sorted(turnover[i - liq_win + 1 : i + 1])
        mid = liq_win // 2
        liq = window[mid] if liq_win % 2 else (window[mid - 1] + window[mid]) / 2.0
        out[day] = (vol, close[i], liq)
    return out


def select_schedule(
    raw_map: Mapping[str, RawSeries],
    all_dates: Sequence[date],
    start: date,
    end: date,
    parameters: FactorParameters,
) -> list[RebalancePlan]:
    """Build the month-end rebalance schedule with T+lag entry dates."""

    month_ends = frozenset(month_end_dates(all_dates))
    ordered = sorted(all_dates)
    index = {day: i for i, day in enumerate(ordered)}
    per_stock = {
        stock_id: _metrics_at_month_ends(series, month_ends, parameters)
        for stock_id, series in raw_map.items()
    }
    plans: list[RebalancePlan] = []
    for rebalance_date in sorted(month_ends):
        if rebalance_date < start or rebalance_date > end:
            continue
        entry_i = index[rebalance_date] + parameters.signal_lag_days
        if entry_i >= len(ordered):
            break
        candidates = {
            stock_id: metrics[rebalance_date]
            for stock_id, metrics in per_stock.items()
            if rebalance_date in metrics
        }
        weights = select_holdings(candidates, parameters)
        plans.append(RebalancePlan(rebalance_date, ordered[entry_i], weights))
    return plans


def _trade_cost(notional: float, *, is_sell: bool, parameters: FactorParameters) -> float:
    if notional <= 0.0:
        return 0.0
    commission = max(notional * parameters.commission_bps / _BPS, parameters.min_fee)
    slippage = notional * parameters.slippage_bps / _BPS
    tax = notional * parameters.sell_tax_bps / _BPS if is_sell else 0.0
    return commission + slippage + tax


def _rebalance_cost(
    carried: Mapping[str, float],
    target_values: Mapping[str, float],
    parameters: FactorParameters,
) -> tuple[float, float]:
    """Cost and one-way notional to move from carried to target positions."""

    cost = 0.0
    notional = 0.0
    for stock_id in set(carried) | set(target_values):
        delta = target_values.get(stock_id, 0.0) - carried.get(stock_id, 0.0)
        if delta == 0.0:
            continue
        notional += abs(delta)
        cost += _trade_cost(abs(delta), is_sell=delta < 0.0, parameters=parameters)
    return cost, notional


def simulate(
    plans: Sequence[RebalancePlan],
    price_lookup: Mapping[str, Mapping[date, float]],
    all_dates: Sequence[date],
    parameters: FactorParameters,
) -> FactorResult:
    """Mark the scheduled baskets to adjusted total return day by day.

    ``price_lookup`` maps held stock_id -> {date: adjusted_close}. A held name
    that stops trading (delist/halt) freezes at its last value until the next
    rebalance sells it — i.e. the collapse already in adjusted_close is realized,
    with no survivorship rescue.
    """

    if not plans:
        raise FactorError("no rebalance plans to simulate")
    ordered = sorted(all_dates)
    index = {day: i for i, day in enumerate(ordered)}
    cash_daily = 1.0 + parameters.cash_yield_annual_bps / _BPS / _TRADING_DAYS_YEAR

    equity = parameters.initial_capital
    carried: dict[str, float] = {}
    curve: list[tuple[date, float]] = []
    total_cost = 0.0
    total_notional = 0.0
    equity_sum = 0.0
    name_counts: list[int] = []

    for k, plan in enumerate(plans):
        target = {stock_id: equity * w for stock_id, w in plan.weights.items()}
        cost, notional = _rebalance_cost(carried, target, parameters)
        equity -= cost
        total_cost += cost
        total_notional += notional
        name_counts.append(len(plan.weights))

        positions = {stock_id: equity * w for stock_id, w in plan.weights.items()}
        cash = equity - sum(positions.values())

        entry_i = index[plan.entry_date]
        if k + 1 < len(plans):
            stop_i = index[plans[k + 1].entry_date]
        else:
            stop_i = len(ordered)
        period = ordered[entry_i:stop_i]

        for offset, day in enumerate(period):
            if offset > 0:
                prev_day = period[offset - 1]
                grown: dict[str, float] = {}
                for stock_id, value in positions.items():
                    lut = price_lookup.get(stock_id, {})
                    now = lut.get(day)
                    before = lut.get(prev_day)
                    if now is not None and before is not None and before > 0.0:
                        grown[stock_id] = value * (now / before)
                    else:
                        grown[stock_id] = value  # freeze through delist/halt
                positions = grown
                cash *= cash_daily
            equity = sum(positions.values()) + cash
            curve.append((day, equity))
        equity_sum += equity
        carried = positions

    return _finalize(
        curve,
        rebalances=len(plans),
        avg_names=statistics.fmean(name_counts) if name_counts else 0.0,
        total_cost=total_cost,
        total_notional=total_notional,
        avg_equity=equity_sum / len(plans),
        parameters=parameters,
    )


def run_backtest(
    raw_map: Mapping[str, RawSeries],
    price_lookup: Mapping[str, Mapping[date, float]],
    all_dates: Sequence[date],
    start: date,
    end: date,
    parameters: FactorParameters,
) -> FactorResult:
    """Convenience: schedule then simulate in one call."""

    plans = select_schedule(raw_map, all_dates, start, end, parameters)
    return simulate(plans, price_lookup, all_dates, parameters)


def _finalize(
    curve: list[tuple[date, float]],
    *,
    rebalances: int,
    avg_names: float,
    total_cost: float,
    total_notional: float,
    avg_equity: float,
    parameters: FactorParameters,
) -> FactorResult:
    if len(curve) < 2:
        raise FactorError("equity curve needs at least two points")
    start_day = curve[0][0]
    end_day = curve[-1][0]
    years = (end_day - start_day).days / 365.25
    final = curve[-1][1]
    cagr = (final / parameters.initial_capital) ** (1.0 / years) - 1.0 if years > 0.0 else 0.0

    peak = curve[0][1]
    max_dd = 0.0
    daily_returns: list[float] = []
    prev = curve[0][1]
    for _, value in curve:
        peak = max(peak, value)
        if peak > 0.0:
            max_dd = max(max_dd, (peak - value) / peak)
        if prev > 0.0:
            daily_returns.append(value / prev - 1.0)
        prev = value

    periods_per_year = len(curve) / years if years > 0.0 else 0.0
    sharpe = 0.0
    if len(daily_returns) >= 2:
        stdev = statistics.stdev(daily_returns)
        if stdev > 0.0:
            sharpe = statistics.fmean(daily_returns) / stdev * math.sqrt(periods_per_year)
    annual_turnover = 0.0
    if avg_equity > 0.0 and years > 0.0:
        annual_turnover = total_notional / avg_equity / years

    return FactorResult(
        equity_curve=tuple(curve),
        cagr=cagr,
        max_drawdown=max_dd,
        annualized_sharpe=sharpe,
        daily_returns=tuple(daily_returns),
        rebalances=rebalances,
        avg_names=avg_names,
        annual_turnover=annual_turnover,
        total_cost=total_cost,
        periods_per_year=periods_per_year,
        start=start_day,
        end=end_day,
    )
