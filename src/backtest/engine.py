"""Daily replay engine reusing the runtime strategy/portfolio/risk/execution path."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from src.accounting import AccountState, VirtualAccountLedger
from src.backtest.types import (
    BacktestError,
    BacktestMetrics,
    BacktestParameters,
    BacktestReport,
    EquityPoint,
    SignalLogEntry,
    TargetLogEntry,
)
from src.domain import (
    Candle,
    OrderIntent,
    OrderSide,
    Position,
    RiskDecisionStatus,
    Symbol,
    VirtualAccountSnapshot,
    VirtualFill,
    VirtualOrder,
)
from src.execution import (
    BrokerAccountView,
    PaperBroker,
    PaperBrokerParameters,
    PaperMarketPrice,
)
from src.features import FeatureSnapshot, build_daily_trend_snapshots
from src.portfolio import LadderPortfolioParameters, build_ladder_targets
from src.risk import (
    EXCHANGE_MIN_NOTIONAL_NOT_MET,
    MIN_NOTIONAL_NOT_MET,
    RiskEvent,
    RiskExchangeFilters,
    RiskGateContext,
    RiskGateParameters,
    RiskState,
    detect_single_day_disaster,
    evaluate_order_intent,
)
from src.strategies import DailyTrendEnsembleDecision, evaluate_daily_trend_ensemble

_BPS = Decimal("10000")
_DAYS_PER_YEAR = 365
ZERO_QUANTITY_AFTER_ROUNDING = "ZERO_QUANTITY_AFTER_ROUNDING"

# Hard rejections that would repeat identically every day if retried; the
# executed fraction is marked as reached so the (recorded) rejection stays a
# single auditable event instead of daily spam.
_NON_RETRIABLE_REJECTION_CODES = frozenset(
    {MIN_NOTIONAL_NOT_MET, EXCHANGE_MIN_NOTIONAL_NOT_MET, ZERO_QUANTITY_AFTER_ROUNDING}
)


def run_backtest(
    candles_by_symbol: Mapping[str, tuple[Candle, ...]],
    *,
    parameters: BacktestParameters,
) -> BacktestReport:
    """Replay daily candles through the full decision-to-accounting path."""

    _require_symbols_match_budgets(candles_by_symbol, parameters)
    snapshots_by_symbol = {
        symbol_value: build_daily_trend_snapshots(candles)
        for symbol_value, candles in candles_by_symbol.items()
    }
    decision_times = _aligned_decision_times(snapshots_by_symbol)
    if not decision_times:
        msg = "no decision days after the warmup floor; provide more history"
        raise BacktestError(msg)

    candle_lookup = {
        symbol_value: {candle.close_time: index for index, candle in enumerate(candles)}
        for symbol_value, candles in candles_by_symbol.items()
    }
    symbols = {
        symbol_value: candles[0].symbol for symbol_value, candles in candles_by_symbol.items()
    }
    snapshot_lookup = {
        symbol_value: {snapshot.as_of: snapshot for snapshot in snapshots}
        for symbol_value, snapshots in snapshots_by_symbol.items()
    }

    first_decision_time = decision_times[0]
    ledger = VirtualAccountLedger.open(
        account_id=parameters.account_id,
        initial_cash=parameters.initial_cash,
        opened_at=first_decision_time,
    )
    broker = PaperBroker(
        PaperBrokerParameters(
            fee_bps=parameters.effective_fee_bps,
            slippage_bps=parameters.effective_slippage_bps,
            quantity_step=parameters.quantity_step,
            price_tick=parameters.price_tick,
            min_notional=parameters.min_notional_usdt,
        )
    )
    gate_parameters = RiskGateParameters(
        min_notional_usdt=parameters.min_notional_usdt,
        stale_data_max_age_seconds=parameters.stale_data_max_age_seconds,
        max_drawdown_fraction=parameters.max_drawdown_fraction,
        daily_loss_pause_fraction=parameters.daily_loss_pause_fraction,
    )
    ladder_parameters = LadderPortfolioParameters(risk_budgets=parameters.risk_budgets)

    previous_decision_fraction: dict[str, Decimal] = dict.fromkeys(symbols, Decimal("0"))
    executed_fraction: dict[str, Decimal] = dict.fromkeys(symbols, Decimal("0"))
    signals: list[SignalLogEntry] = []
    targets: list[TargetLogEntry] = []
    risk_rejections: list[tuple[str, datetime, tuple[str, ...]]] = []
    risk_events: list[RiskEvent] = []
    equity_curve: list[EquityPoint] = []
    order_sequence = 0
    start_of_day_equity = parameters.initial_cash
    benchmark_open_prices: dict[str, Decimal] | None = None

    for decision_time in decision_times:
        decisions: dict[str, DailyTrendEnsembleDecision] = {}
        for symbol_value in sorted(symbols):
            snapshot = snapshot_lookup[symbol_value][decision_time]
            decision = evaluate_daily_trend_ensemble(
                snapshot,
                previous_fraction=previous_decision_fraction[symbol_value],
            )
            decisions[symbol_value] = decision
            previous_decision_fraction[symbol_value] = decision.exposure_fraction
            signals.append(
                SignalLogEntry(
                    symbol=symbol_value,
                    as_of=decision.generated_at_bar_close,
                    exposure_fraction=decision.exposure_fraction,
                    reason_codes=decision.reason_codes,
                )
            )
            risk_event = _disaster_event_for(
                candles_by_symbol[symbol_value],
                candle_lookup[symbol_value][decision_time],
                symbols[symbol_value],
                parameters.disaster_single_day_drop_fraction,
            )
            if risk_event is not None:
                risk_events.append(risk_event)

        target_set = build_ladder_targets(
            tuple(decisions[symbol_value] for symbol_value in sorted(symbols)),
            parameters=ladder_parameters,
        )
        targets.append(
            TargetLogEntry(
                as_of=decision_time,
                target_weights=tuple(
                    (target.symbol.value, target.target_weight) for target in target_set.targets
                ),
                cash_weight=target_set.cash_weight,
                reason_codes=target_set.reason_codes,
            )
        )

        execution_candles = _execution_candles(candles_by_symbol, candle_lookup, decision_time)
        if execution_candles is None:
            # Final decision day: signal recorded, nothing left to execute on.
            continue

        open_prices = {
            symbol_value: candle.open_price for symbol_value, candle in execution_candles.items()
        }
        if benchmark_open_prices is None:
            benchmark_open_prices = dict(open_prices)
        execution_time = next(iter(execution_candles.values())).open_time
        equity_at_open = _equity_at(ledger, open_prices)

        ordered_symbols = sorted(
            symbols,
            key=lambda value: (
                decisions[value].exposure_fraction >= executed_fraction[value],
                value,
            ),
        )
        for symbol_value in ordered_symbols:
            decision = decisions[symbol_value]
            desired_fraction = decision.exposure_fraction
            if desired_fraction == executed_fraction[symbol_value]:
                continue
            order_sequence += 1
            outcome = _execute_ladder_change(
                ledger=ledger,
                broker=broker,
                gate_parameters=gate_parameters,
                parameters=parameters,
                symbol=symbols[symbol_value],
                decision=decision,
                desired_fraction=desired_fraction,
                current_fraction=executed_fraction[symbol_value],
                budget=parameters.risk_budgets[symbol_value],
                equity_at_open=equity_at_open,
                open_prices=open_prices,
                symbols=symbols,
                execution_time=execution_time,
                order_id=f"bt-{order_sequence:06d}-{symbol_value}",
                start_of_day_equity=start_of_day_equity,
            )
            if outcome.achieved is not None:
                executed_fraction[symbol_value] = outcome.achieved
            else:
                if not outcome.broker_recorded:
                    # Broker rejections already live in broker.rejected_orders;
                    # recording them here too would double-count the metric.
                    risk_rejections.append((symbol_value, execution_time, outcome.reason_codes))
                if _NON_RETRIABLE_REJECTION_CODES.intersection(outcome.reason_codes):
                    executed_fraction[symbol_value] = desired_fraction

        close_prices = {
            symbol_value: candle.close_price for symbol_value, candle in execution_candles.items()
        }
        close_time = next(iter(execution_candles.values())).close_time
        close_snapshot = ledger.snapshot(
            mark_prices=_mark_prices(close_prices, symbols),
            captured_at=close_time,
        )
        benchmark_equity = _benchmark_equity(parameters, benchmark_open_prices, close_prices)
        equity_curve.append(
            EquityPoint(
                close_time=close_time,
                equity=close_snapshot.equity,
                drawdown=ledger.state.drawdown,
                benchmark_equity=benchmark_equity,
            )
        )
        start_of_day_equity = close_snapshot.equity

    metrics = _metrics(
        equity_curve,
        parameters=parameters,
        fills=broker.fills,
        rejected_count=len(broker.rejected_orders) + len(risk_rejections),
    )
    return BacktestReport(
        data_start=decision_times[0],
        data_end=decision_times[-1],
        decision_days=len(decision_times),
        signals=tuple(signals),
        targets=tuple(targets),
        accepted_orders=broker.accepted_orders,
        fills=broker.fills,
        rejected_orders=broker.rejected_orders,
        risk_rejections=tuple(risk_rejections),
        risk_events=tuple(risk_events),
        equity_curve=tuple(equity_curve),
        metrics=metrics,
        cost_assumptions={
            "fee_bps": str(parameters.effective_fee_bps),
            "slippage_bps": str(parameters.effective_slippage_bps),
            "cost_multiplier": str(parameters.cost_multiplier),
            "fill_rule": "next_bar_open",
        },
    )


@dataclass(frozen=True, slots=True)
class _LadderOutcome:
    """Result of one ladder-change attempt: achieved fraction or rejection."""

    achieved: Decimal | None
    reason_codes: tuple[str, ...] = ()
    broker_recorded: bool = False


def _execute_ladder_change(
    *,
    ledger: VirtualAccountLedger,
    broker: PaperBroker,
    gate_parameters: RiskGateParameters,
    parameters: BacktestParameters,
    symbol: Symbol,
    decision: DailyTrendEnsembleDecision,
    desired_fraction: Decimal,
    current_fraction: Decimal,
    budget: Decimal,
    equity_at_open: Decimal,
    open_prices: Mapping[str, Decimal],
    symbols: Mapping[str, Symbol],
    execution_time: datetime,
    order_id: str,
    start_of_day_equity: Decimal,
) -> _LadderOutcome:
    """Build, gate, and execute one ladder-change order."""

    reference_price = open_prices[symbol.value]
    state = ledger.state
    current_position = _domain_position(state, symbol)
    delta_fraction = desired_fraction - current_fraction
    # The ladder step's cash claim; costs live INSIDE it so simultaneous
    # full-budget buys cannot starve whichever symbol trades last.
    intended_notional = abs(delta_fraction) * budget * equity_at_open

    if delta_fraction < Decimal("0"):
        side = OrderSide.SELL
        held_quantity = current_position.quantity if current_position is not None else Decimal("0")
        if desired_fraction == Decimal("0"):
            quantity = held_quantity
        else:
            quantity = min(
                _round_down(intended_notional / reference_price, parameters.quantity_step),
                held_quantity,
            )
    else:
        side = OrderSide.BUY
        # Mirror the broker's cost math exactly (slippage then tick round-up,
        # fee on the gross fill notional) so an affordable-sized buy can never
        # bounce off the broker's cash check by a rounding hair.
        fee_rate = parameters.effective_fee_bps / _BPS
        cost_rate = (
            Decimal("1") + (parameters.effective_fee_bps + parameters.effective_slippage_bps) / _BPS
        )
        estimated_fill_price = _round_up(
            reference_price * (Decimal("1") + parameters.effective_slippage_bps / _BPS),
            parameters.price_tick,
        )
        target_quantity = _round_down(
            intended_notional / cost_rate / reference_price, parameters.quantity_step
        )
        affordable_quantity = _round_down(
            state.cash / (estimated_fill_price * (Decimal("1") + fee_rate)),
            parameters.quantity_step,
        )
        quantity = min(target_quantity, affordable_quantity)

    if quantity <= Decimal("0"):
        ledger.record_rejected_order(
            order_id=None,
            symbol=symbol,
            occurred_at=execution_time,
            reason_codes=(ZERO_QUANTITY_AFTER_ROUNDING,),
        )
        return _LadderOutcome(achieved=None, reason_codes=(ZERO_QUANTITY_AFTER_ROUNDING,))

    intent = OrderIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        created_at=execution_time,
    )
    account_snapshot = VirtualAccountSnapshot(
        account_id=state.account_id,
        cash=state.cash,
        equity=equity_at_open,
        positions=_domain_positions(state),
        captured_at=execution_time,
    )
    context = RiskGateContext(
        current_position=current_position,
        account_snapshot=account_snapshot,
        reference_price=reference_price,
        latest_market_data_at=execution_time,
        decision_time=execution_time,
        earliest_execution_time=decision.executable_from_next_bar,
        exchange_filters=RiskExchangeFilters(
            symbol=symbol,
            status="TRADING",
            is_spot_trading_allowed=True,
            price_tick_size=parameters.price_tick,
            quantity_step_size=parameters.quantity_step,
            min_quantity=parameters.quantity_step,
            min_notional=parameters.min_notional_usdt,
        ),
        risk_state=RiskState(
            peak_equity=ledger.state.peak_equity,
            start_of_day_equity=start_of_day_equity,
        ),
    )
    risk_decision = evaluate_order_intent(intent, context=context, parameters=gate_parameters)
    if risk_decision.status is not RiskDecisionStatus.APPROVED:
        ledger.record_rejected_order(
            order_id=order_id,
            symbol=symbol,
            occurred_at=execution_time,
            reason_codes=risk_decision.reason_codes,
        )
        return _LadderOutcome(achieved=None, reason_codes=risk_decision.reason_codes)

    order = VirtualOrder(
        order_id=order_id,
        intent=intent,
        risk_decision=risk_decision,
        approved_at=execution_time,
    )
    result = broker.submit_order(
        order,
        market_price=PaperMarketPrice(
            symbol=symbol, price=reference_price, observed_at=execution_time
        ),
        account_view=BrokerAccountView(cash=state.cash, positions=_domain_positions(state)),
        submitted_at=execution_time,
    )
    if result.fill is None:
        rejected = result.rejected_order
        reason_codes = rejected.reason_codes if rejected is not None else ("BROKER_REJECTED",)
        ledger.record_rejected_order(
            order_id=order_id,
            symbol=symbol,
            occurred_at=execution_time,
            reason_codes=reason_codes,
        )
        return _LadderOutcome(achieved=None, reason_codes=reason_codes, broker_recorded=True)

    ledger.apply_fill(result.fill, mark_prices=_mark_prices(open_prices, symbols))
    # A cash-capped fill must not masquerade as the full ladder step: track
    # the fraction actually achieved so the shortfall retries next cycle.
    actual_cost = result.fill.quantity * result.fill.price + result.fill.fee
    if intended_notional > Decimal("0") and actual_cost < intended_notional * Decimal("0.99"):
        achieved = current_fraction + delta_fraction * actual_cost / intended_notional
    else:
        achieved = desired_fraction
    return _LadderOutcome(achieved=achieved)


def _require_symbols_match_budgets(
    candles_by_symbol: Mapping[str, tuple[Candle, ...]],
    parameters: BacktestParameters,
) -> None:
    candle_symbols = set(candles_by_symbol)
    budget_symbols = set(parameters.risk_budgets)
    if candle_symbols != budget_symbols:
        msg = (
            "candles must cover exactly the budgeted decision universe; "
            f"candles={sorted(candle_symbols)} budgets={sorted(budget_symbols)}"
        )
        raise BacktestError(msg)


def _aligned_decision_times(
    snapshots_by_symbol: Mapping[str, tuple[FeatureSnapshot, ...]],
) -> tuple[datetime, ...]:
    time_sets = {
        symbol_value: {snapshot.as_of for snapshot in snapshots}
        for symbol_value, snapshots in snapshots_by_symbol.items()
    }
    reference: set[datetime] | None = None
    for symbol_value, times in time_sets.items():
        if reference is None:
            reference = times
            continue
        if times != reference:
            missing = sorted(reference.symmetric_difference(times))[:5]
            msg = (
                "decision days must align across all budgeted symbols; "
                f"first mismatches near {symbol_value}: "
                f"{[value.isoformat() for value in missing]}"
            )
            raise BacktestError(msg)
    return tuple(sorted(reference or ()))


def _execution_candles(
    candles_by_symbol: Mapping[str, tuple[Candle, ...]],
    candle_lookup: Mapping[str, Mapping[datetime, int]],
    decision_time: datetime,
) -> dict[str, Candle] | None:
    execution_candles: dict[str, Candle] = {}
    for symbol_value, candles in candles_by_symbol.items():
        index = candle_lookup[symbol_value][decision_time]
        next_index = index + 1
        if next_index >= len(candles):
            return None
        execution_candles[symbol_value] = candles[next_index]
    return execution_candles


def _disaster_event_for(
    candles: tuple[Candle, ...],
    close_index: int,
    symbol: Symbol,
    threshold_fraction: Decimal,
) -> RiskEvent | None:
    if close_index == 0:
        return None
    previous = candles[close_index - 1]
    current = candles[close_index]
    return detect_single_day_disaster(
        symbol=symbol,
        previous_close=previous.close_price,
        current_close=current.close_price,
        occurred_at=current.close_time,
        threshold_fraction=threshold_fraction,
    )


def _domain_positions(state: AccountState) -> tuple[Position, ...]:
    return tuple(
        Position(
            symbol=position.symbol,
            quantity=position.quantity,
            average_entry_price=position.average_entry_price,
        )
        for position in state.positions
    )


def _domain_position(state: AccountState, symbol: Symbol) -> Position | None:
    for position in _domain_positions(state):
        if position.symbol == symbol:
            return position
    return None


def _equity_at(ledger: VirtualAccountLedger, prices: Mapping[str, Decimal]) -> Decimal:
    equity = ledger.state.cash
    for position in ledger.state.positions:
        equity += position.quantity * prices[position.symbol.value]
    return equity


def _mark_prices(
    prices: Mapping[str, Decimal], symbols: Mapping[str, Symbol]
) -> dict[Symbol, Decimal]:
    return {symbols[symbol_value]: price for symbol_value, price in prices.items()}


def _benchmark_equity(
    parameters: BacktestParameters,
    benchmark_open_prices: Mapping[str, Decimal],
    close_prices: Mapping[str, Decimal],
) -> Decimal:
    invested_fraction = Decimal("0")
    growth = Decimal("0")
    for symbol_value, budget in parameters.risk_budgets.items():
        invested_fraction += budget
        growth += budget * close_prices[symbol_value] / benchmark_open_prices[symbol_value]
    cash_fraction = Decimal("1") - invested_fraction
    return parameters.initial_cash * (growth + cash_fraction)


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def _metrics(
    equity_curve: list[EquityPoint],
    *,
    parameters: BacktestParameters,
    fills: tuple[VirtualFill, ...],
    rejected_count: int,
) -> BacktestMetrics:
    final_equity = equity_curve[-1].equity if equity_curve else parameters.initial_cash
    benchmark_final = equity_curve[-1].benchmark_equity if equity_curve else parameters.initial_cash
    total_return = final_equity / parameters.initial_cash - Decimal("1")

    daily_returns: list[float] = []
    previous_equity = parameters.initial_cash
    for point in equity_curve:
        if previous_equity > Decimal("0"):
            daily_returns.append(float(point.equity / previous_equity) - 1.0)
        previous_equity = point.equity

    annualized_sharpe = Decimal("0")
    if len(daily_returns) >= 2:
        mean_return = statistics.fmean(daily_returns)
        stdev_return = statistics.stdev(daily_returns)
        if stdev_return > 0.0:
            annualized_sharpe = Decimal(
                str(round(mean_return / stdev_return * math.sqrt(_DAYS_PER_YEAR), 6))
            )

    max_drawdown = Decimal("0")
    peak = parameters.initial_cash
    for point in equity_curve:
        peak = max(peak, point.equity)
        if peak > Decimal("0"):
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)

    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    total_traded_notional = Decimal("0")
    for fill in fills:
        total_fees += fill.fee
        total_slippage += fill.slippage
        total_traded_notional += fill.quantity * fill.price

    annualized_turnover = Decimal("0")
    if equity_curve:
        mean_equity = sum((point.equity for point in equity_curve), Decimal("0")) / Decimal(
            len(equity_curve)
        )
        years = Decimal(len(equity_curve)) / Decimal(_DAYS_PER_YEAR)
        if mean_equity > Decimal("0") and years > Decimal("0"):
            annualized_turnover = total_traded_notional / mean_equity / years

    return BacktestMetrics(
        final_equity=final_equity,
        total_return_fraction=total_return,
        annualized_sharpe=annualized_sharpe,
        max_drawdown_fraction=max_drawdown,
        trade_count=len(fills),
        rejected_count=rejected_count,
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_traded_notional=total_traded_notional,
        annualized_turnover=annualized_turnover,
        benchmark_final_equity=benchmark_final,
        observation_days=len(equity_curve),
    )
