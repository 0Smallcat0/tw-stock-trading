"""Daily signal runtime: notify the human, keep the scoreboard honest.

One cycle per closed daily candle:

1. Execute the previous cycle's ladder changes on the scoreboard at THIS
   candle's open (same next-bar-open rule as the backtest engine).
2. Evaluate the ensemble at this close; persist signals, targets, and any
   ladder-change notification (persisted BEFORE delivery, idempotent by key).
3. Mark the scoreboard at this close and persist the cycle state so a restart
   resumes exactly here without duplicating anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from src.accounting import AccountingPosition, AccountState, VirtualAccountLedger
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
from src.features import DAILY_TREND_WARMUP_CANDLES, build_daily_trend_snapshots
from src.notify import (
    DECREASE_EXPOSURE,
    INCREASE_EXPOSURE,
    NotificationChannel,
    NotificationEvent,
    ladder_notification_id,
)
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
from src.runtime.store import JsonlEventStore
from src.runtime.types import CycleResult, RuntimeEngineError, RuntimeParameters
from src.strategies import DailyTrendEnsembleDecision, evaluate_daily_trend_ensemble

_BPS = Decimal("10000")

WARMUP_INSUFFICIENT_HISTORY = "WARMUP_INSUFFICIENT_HISTORY"
STALE_DATA_HALT = "STALE_DATA_HALT"
ZERO_QUANTITY_AFTER_ROUNDING = "ZERO_QUANTITY_AFTER_ROUNDING"

_NON_RETRIABLE_REJECTION_CODES = frozenset(
    {MIN_NOTIONAL_NOT_MET, EXCHANGE_MIN_NOTIONAL_NOT_MET, ZERO_QUANTITY_AFTER_ROUNDING}
)


class SignalRuntime:
    """Restartable daily signal engine over an idempotent event store."""

    def __init__(
        self,
        *,
        parameters: RuntimeParameters,
        store: JsonlEventStore,
        channel: NotificationChannel,
    ) -> None:
        self._parameters = parameters
        self._store = store
        self._channel = channel
        self._ledger: VirtualAccountLedger | None = None
        self._executed_fractions: dict[str, Decimal] = dict.fromkeys(
            parameters.risk_budgets, Decimal("0")
        )
        self._decision_fractions: dict[str, Decimal] = dict.fromkeys(
            parameters.risk_budgets, Decimal("0")
        )
        self._last_processed: datetime | None = None
        self._start_of_day_equity: Decimal = parameters.initial_cash
        self._restore()

    @property
    def last_processed(self) -> datetime | None:
        return self._last_processed

    def process_closed_candles(
        self,
        candles_by_symbol: Mapping[str, tuple[Candle, ...]],
        *,
        observed_at: datetime | None = None,
    ) -> CycleResult:
        """Run one decision cycle on the latest closed daily candle."""

        self._require_universe(candles_by_symbol)
        ordered = {
            symbol_value: _closed_sorted(candles, symbol_value)
            for symbol_value, candles in candles_by_symbol.items()
        }
        short_history = [
            symbol_value
            for symbol_value, candles in ordered.items()
            if len(candles) < DAILY_TREND_WARMUP_CANDLES
        ]
        if short_history:
            warmup_close = max(candles[-1].close_time for candles in ordered.values())
            self._append_health(
                warmup_close,
                WARMUP_INSUFFICIENT_HISTORY,
                {"symbols": sorted(short_history)},
            )
            return _skipped(WARMUP_INSUFFICIENT_HISTORY, health=(WARMUP_INSUFFICIENT_HISTORY,))

        latest = self._aligned_latest_candles(ordered)
        close_time = max(candle.close_time for candle in latest.values())
        decision_time = observed_at or close_time

        # Retry any persisted-but-undelivered notifications first, so a
        # same-day rerun after a webhook outage still gets the message out.
        self._flush_undelivered(decision_time)

        if self._last_processed is not None and close_time <= self._last_processed:
            return _skipped("ALREADY_PROCESSED")

        health_codes: list[str] = []
        if self._last_processed is not None:
            gap_days = (close_time.date() - self._last_processed.date()).days
            if gap_days > 1:
                health_codes.append("MISSED_DAYS")
                self._append_health(
                    close_time,
                    "MISSED_DAYS",
                    {"skipped_decision_days": gap_days - 1},
                )
        is_stale = decision_time - close_time > timedelta(
            seconds=self._parameters.stale_data_max_age_seconds
        )
        if is_stale:
            health_codes.append(STALE_DATA_HALT)
            self._append_health(
                close_time, STALE_DATA_HALT, {"decision_time": decision_time.isoformat()}
            )

        symbols = {symbol_value: candles[-1].symbol for symbol_value, candles in ordered.items()}
        open_prices = {symbol_value: candle.open_price for symbol_value, candle in latest.items()}
        close_prices = {symbol_value: candle.close_price for symbol_value, candle in latest.items()}
        execution_time = next(iter(latest.values())).open_time

        if self._ledger is None:
            self._ledger = VirtualAccountLedger.open(
                account_id=self._parameters.account_id,
                initial_cash=self._parameters.initial_cash,
                opened_at=execution_time,
            )
        ledger = self._ledger

        # Step 1: execute yesterday's ladder changes at today's open.
        fills, rejections = self._execute_pending(
            ledger=ledger,
            symbols=symbols,
            open_prices=open_prices,
            execution_time=execution_time,
            latest_market_data_at=execution_time,
            stale=is_stale,
        )

        # Step 2: today's decisions, signals, and notifications.
        notifications: list[NotificationEvent] = []
        decisions: dict[str, DailyTrendEnsembleDecision] = {}
        for symbol_value in sorted(symbols):
            snapshot = build_daily_trend_snapshots(
                ordered[symbol_value][-DAILY_TREND_WARMUP_CANDLES:]
            )[0]
            previous_fraction = self._decision_fractions[symbol_value]
            decision = evaluate_daily_trend_ensemble(snapshot, previous_fraction=previous_fraction)
            decisions[symbol_value] = decision
            self._decision_fractions[symbol_value] = decision.exposure_fraction
            self._store.append(
                kind="signal",
                key=f"signal:{symbol_value}:{close_time.isoformat()}",
                recorded_at=decision_time,
                payload={
                    "symbol": symbol_value,
                    "as_of": close_time.isoformat(),
                    "exposure_fraction": str(decision.exposure_fraction),
                    "reason_codes": list(decision.reason_codes),
                },
            )
            notification = self._notify_ladder_change(
                symbol_value=symbol_value,
                previous_fraction=previous_fraction,
                decision_fraction=decision.exposure_fraction,
                decision_price=close_prices[symbol_value],
                decision_time=close_time,
                reason_codes=decision.reason_codes,
                ledger=ledger,
                stale=is_stale,
            )
            if notification is not None:
                notifications.append(notification)

            disaster = _disaster_for(ordered[symbol_value], symbols[symbol_value], self._parameters)
            if disaster is not None:
                self._store.append(
                    kind="risk_event",
                    key=f"risk_event:{symbol_value}:{close_time.isoformat()}",
                    recorded_at=decision_time,
                    payload={
                        "symbol": symbol_value,
                        "event_type": disaster.event_type,
                        "observed_fraction": str(disaster.observed_fraction),
                        "reason_codes": list(disaster.reason_codes),
                    },
                )
                health_codes.append(disaster.event_type)

        target_set = build_ladder_targets(
            tuple(decisions[symbol_value] for symbol_value in sorted(symbols)),
            parameters=LadderPortfolioParameters(risk_budgets=self._parameters.risk_budgets),
        )
        self._store.append(
            kind="target",
            key=f"target:{close_time.isoformat()}",
            recorded_at=decision_time,
            payload={
                "as_of": close_time.isoformat(),
                "target_weights": {
                    target.symbol.value: str(target.target_weight) for target in target_set.targets
                },
                "cash_weight": str(target_set.cash_weight),
                "reason_codes": list(target_set.reason_codes),
            },
        )

        # Step 3: mark the scoreboard at this close and persist cycle state,
        # THEN deliver: a delivery failure can no longer corrupt the cycle.
        close_snapshot = ledger.snapshot(
            mark_prices={symbols[value]: close_prices[value] for value in symbols},
            captured_at=close_time,
        )
        self._start_of_day_equity = close_snapshot.equity
        self._last_processed = close_time
        self._persist_cycle(close_time, decision_time)
        self._flush_undelivered(decision_time)

        return CycleResult(
            processed=True,
            reason="PROCESSED",
            close_time=close_time,
            notifications=tuple(notifications),
            fills=fills,
            rejection_reason_codes=rejections,
            health_codes=tuple(health_codes),
            equity=close_snapshot.equity,
        )

    # ── execution ──────────────────────────────────────────────────────

    def _execute_pending(
        self,
        *,
        ledger: VirtualAccountLedger,
        symbols: Mapping[str, Symbol],
        open_prices: Mapping[str, Decimal],
        execution_time: datetime,
        latest_market_data_at: datetime,
        stale: bool,
    ) -> tuple[tuple[VirtualFill, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
        broker = PaperBroker(
            PaperBrokerParameters(
                fee_bps=self._parameters.fee_bps,
                slippage_bps=self._parameters.slippage_bps,
                quantity_step=self._parameters.quantity_step,
                price_tick=self._parameters.price_tick,
                min_notional=self._parameters.min_notional_usdt,
            )
        )
        # Sizing basis is frozen BEFORE any fill so per-symbol targets are
        # order-independent — the same rule the backtest engine uses.
        equity_at_open = ledger.state.cash
        for position in ledger.state.positions:
            equity_at_open += position.quantity * open_prices[position.symbol.value]

        fills: list[VirtualFill] = []
        rejections: list[tuple[str, tuple[str, ...]]] = []
        pending = sorted(
            (
                symbol_value
                for symbol_value in symbols
                if self._decision_fractions[symbol_value] != self._executed_fractions[symbol_value]
            ),
            key=lambda value: (
                self._decision_fractions[value] >= self._executed_fractions[value],
                value,
            ),
        )
        for symbol_value in pending:
            desired = self._decision_fractions[symbol_value]
            if stale and desired > self._executed_fractions[symbol_value]:
                # Stale data halts new exposure; risk-reducing sells continue.
                rejections.append((symbol_value, (STALE_DATA_HALT,)))
                self._store.append(
                    kind="rejection",
                    key=f"rejection:{symbol_value}:{execution_time.isoformat()}",
                    recorded_at=execution_time,
                    payload={"symbol": symbol_value, "reason_codes": [STALE_DATA_HALT]},
                )
                continue
            order_key = (
                f"order:{self._parameters.idempotency_namespace}"
                f":{symbol_value}:{execution_time.date().isoformat()}"
            )
            if self._store.has(f"fill:{order_key}"):
                # Crash recovery: the fill happened and its checkpoint already
                # restored both the ledger and the achieved fraction. Nothing
                # to replay; any shortfall retries on the next cycle's key.
                continue
            if self._store.has(order_key):
                # An attempt exists but never filled (broker rejection or a
                # crash before the fill). Never guess the outcome: leave the
                # executed fraction unchanged so a FRESH order retries on the
                # next cycle's key.
                self._append_health(
                    execution_time,
                    "ORDER_WITHOUT_FILL_SKIPPED",
                    {"symbol": symbol_value, "order_key": order_key},
                )
                continue
            outcome = self._execute_one(
                ledger=ledger,
                broker=broker,
                symbol=symbols[symbol_value],
                desired=desired,
                current=self._executed_fractions[symbol_value],
                reference_price=open_prices[symbol_value],
                open_prices=open_prices,
                symbols=symbols,
                execution_time=execution_time,
                latest_market_data_at=latest_market_data_at,
                order_key=order_key,
                equity_at_open=equity_at_open,
            )
            if outcome is None:
                fills.extend(broker.fills[len(fills) :])
            else:
                rejections.append((symbol_value, outcome))
                self._store.append(
                    kind="rejection",
                    key=f"rejection:{symbol_value}:{execution_time.isoformat()}",
                    recorded_at=execution_time,
                    payload={"symbol": symbol_value, "reason_codes": list(outcome)},
                )
                if _NON_RETRIABLE_REJECTION_CODES.intersection(outcome):
                    self._executed_fractions[symbol_value] = desired
        return tuple(fills), tuple(rejections)

    def _execute_one(
        self,
        *,
        ledger: VirtualAccountLedger,
        broker: PaperBroker,
        symbol: Symbol,
        desired: Decimal,
        current: Decimal,
        reference_price: Decimal,
        open_prices: Mapping[str, Decimal],
        symbols: Mapping[str, Symbol],
        execution_time: datetime,
        latest_market_data_at: datetime,
        order_key: str,
        equity_at_open: Decimal,
    ) -> tuple[str, ...] | None:
        parameters = self._parameters
        state = ledger.state
        budget = parameters.risk_budgets[symbol.value]
        current_position = _domain_position(state, symbol)
        delta = desired - current
        # The ladder step's cash claim; costs live INSIDE it so simultaneous
        # full-budget buys cannot starve the last symbol (fees are not an
        # excuse to underweight whoever trades last).
        intended_notional = abs(delta) * budget * equity_at_open

        if delta < Decimal("0"):
            side = OrderSide.SELL
            held = current_position.quantity if current_position is not None else Decimal("0")
            if desired == Decimal("0"):
                quantity = held
            else:
                quantity = min(
                    _round_down(intended_notional / reference_price, parameters.quantity_step),
                    held,
                )
        else:
            side = OrderSide.BUY
            fee_rate = parameters.fee_bps / _BPS
            cost_rate = Decimal("1") + (parameters.fee_bps + parameters.slippage_bps) / _BPS
            estimated_fill = _round_up(
                reference_price * (Decimal("1") + parameters.slippage_bps / _BPS),
                parameters.price_tick,
            )
            target_quantity = _round_down(
                intended_notional / cost_rate / reference_price,
                parameters.quantity_step,
            )
            affordable_quantity = _round_down(
                state.cash / (estimated_fill * (Decimal("1") + fee_rate)),
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
            return (ZERO_QUANTITY_AFTER_ROUNDING,)

        intent = OrderIntent(symbol=symbol, side=side, quantity=quantity, created_at=execution_time)
        context = RiskGateContext(
            current_position=current_position,
            account_snapshot=VirtualAccountSnapshot(
                account_id=state.account_id,
                cash=state.cash,
                equity=equity_at_open,
                positions=_domain_positions(state),
                captured_at=execution_time,
            ),
            reference_price=reference_price,
            latest_market_data_at=latest_market_data_at,
            decision_time=execution_time,
            earliest_execution_time=execution_time,
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
                start_of_day_equity=self._start_of_day_equity,
            ),
        )
        gate_parameters = RiskGateParameters(
            min_notional_usdt=parameters.min_notional_usdt,
            stale_data_max_age_seconds=parameters.stale_data_max_age_seconds,
            max_drawdown_fraction=parameters.max_drawdown_fraction,
            daily_loss_pause_fraction=parameters.daily_loss_pause_fraction,
        )
        risk_decision = evaluate_order_intent(intent, context=context, parameters=gate_parameters)
        self._store.append(
            kind="risk_decision",
            key=f"risk_decision:{symbol.value}:{execution_time.isoformat()}",
            recorded_at=execution_time,
            payload={
                "symbol": symbol.value,
                "status": risk_decision.status.value,
                "reason_codes": list(risk_decision.reason_codes),
            },
        )
        if risk_decision.status is not RiskDecisionStatus.APPROVED:
            ledger.record_rejected_order(
                order_id=order_key,
                symbol=symbol,
                occurred_at=execution_time,
                reason_codes=risk_decision.reason_codes,
            )
            return risk_decision.reason_codes

        if not self._store.append(
            kind="order",
            key=order_key,
            recorded_at=execution_time,
            payload={
                "symbol": symbol.value,
                "side": side.value,
                "quantity": str(quantity),
                "desired_fraction": str(desired),
            },
        ):
            return None

        order = VirtualOrder(
            order_id=order_key,
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
                order_id=order_key,
                symbol=symbol,
                occurred_at=execution_time,
                reason_codes=reason_codes,
            )
            return reason_codes

        ledger.apply_fill(
            result.fill,
            mark_prices={symbols[value]: open_prices[value] for value in symbols},
        )
        # Track the fraction actually achieved: a cash-capped fill must NOT be
        # marked as the full ladder step, or the shortfall becomes permanent.
        actual_cost = result.fill.quantity * result.fill.price + result.fill.fee
        if intended_notional > Decimal("0") and actual_cost < intended_notional * Decimal("0.99"):
            achieved = current + delta * actual_cost / intended_notional
        else:
            achieved = desired
        self._executed_fractions[symbol.value] = achieved
        # The fill event doubles as a crash checkpoint: it carries the full
        # post-fill account state so a restart between this append and the
        # end-of-cycle snapshot can never lose the fill from the scoreboard.
        self._store.append(
            kind="fill",
            key=f"fill:{order_key}",
            recorded_at=execution_time,
            payload={
                "symbol": symbol.value,
                "side": side.value,
                "quantity": str(result.fill.quantity),
                "price": str(result.fill.price),
                "fee": str(result.fill.fee),
                "slippage": str(result.fill.slippage),
                "checkpoint": self._state_payload(),
            },
        )
        return None

    # ── notifications ──────────────────────────────────────────────────

    def _notify_ladder_change(
        self,
        *,
        symbol_value: str,
        previous_fraction: Decimal,
        decision_fraction: Decimal,
        decision_price: Decimal,
        decision_time: datetime,
        reason_codes: tuple[str, ...],
        ledger: VirtualAccountLedger,
        stale: bool,
    ) -> NotificationEvent | None:
        if decision_fraction == previous_fraction:
            return None
        risk_codes: list[str] = []
        if stale:
            risk_codes.append(STALE_DATA_HALT)
        if ledger.state.drawdown >= self._parameters.max_drawdown_fraction:
            risk_codes.append("DRAWDOWN_PAUSE")
        event = NotificationEvent(
            notification_id=ladder_notification_id(
                namespace=self._parameters.idempotency_namespace,
                symbol_value=symbol_value,
                decision_time=decision_time,
                previous_fraction=previous_fraction,
                target_fraction=decision_fraction,
            ),
            symbol_value=symbol_value,
            action=(
                INCREASE_EXPOSURE if decision_fraction > previous_fraction else DECREASE_EXPOSURE
            ),
            previous_fraction=previous_fraction,
            target_fraction=decision_fraction,
            delta_fraction=abs(decision_fraction - previous_fraction),
            decision_price=decision_price,
            decision_time=decision_time,
            reason_codes=reason_codes,
            risk_status=",".join(risk_codes) if risk_codes else "OK",
            created_at=decision_time,
        )
        newly_persisted = self._store.append(
            kind="notification",
            key=event.notification_id,
            recorded_at=decision_time,
            payload=event.to_json_dict(),
        )
        if not newly_persisted:
            return None
        # Delivery is decoupled: _flush_undelivered sends after the cycle
        # state is safe, so a webhook outage can never corrupt the scoreboard.
        return event

    def _flush_undelivered(self, now: datetime) -> tuple[NotificationEvent, ...]:
        """Deliver every persisted-but-undelivered notification, exactly once.

        A delivered-marker event records success; failures leave the marker
        absent so the next cycle (or a same-day rerun) retries. Delivery
        errors are logged as health events and never abort a cycle.
        """

        delivered: list[NotificationEvent] = []
        for event in self._store.events_of_kind("notification"):
            marker_key = f"delivered:{event.key}"
            if self._store.has(marker_key):
                continue
            try:
                notification = _notification_from_payload(event.payload)
                self._channel.deliver(notification)
            except Exception as exc:  # noqa: BLE001 - delivery must never kill a cycle
                self._store.append(
                    kind="health",
                    key=f"health:NOTIFICATION_DELIVERY_FAILED:{event.key}:{now.date().isoformat()}",
                    recorded_at=now,
                    payload={
                        "code": "NOTIFICATION_DELIVERY_FAILED",
                        "notification": event.key,
                        "error": type(exc).__name__,
                    },
                )
                continue
            self._store.append(
                kind="notification_delivered",
                key=marker_key,
                recorded_at=now,
                payload={"notification": event.key},
            )
            delivered.append(notification)
        return tuple(delivered)

    # ── state persistence and restore ──────────────────────────────────

    def _state_payload(self) -> dict[str, object]:
        """Full restart checkpoint: account, fractions, and cycle cursor."""

        ledger = self._ledger
        if ledger is None:  # pragma: no cover - guarded by callers
            msg = "state cannot persist before the ledger exists"
            raise RuntimeEngineError(msg)
        state = ledger.state
        return {
            "last_processed": (self._last_processed.isoformat() if self._last_processed else None),
            "start_of_day_equity": str(self._start_of_day_equity),
            "executed_fractions": {
                key: str(value) for key, value in self._executed_fractions.items()
            },
            "decision_fractions": {
                key: str(value) for key, value in self._decision_fractions.items()
            },
            "account": {
                "account_id": state.account_id,
                "cash": str(state.cash),
                "realized_pnl": str(state.realized_pnl),
                "unrealized_pnl": str(state.unrealized_pnl),
                "equity": str(state.equity),
                "peak_equity": str(state.peak_equity),
                "drawdown": str(state.drawdown),
                "updated_at": state.updated_at.isoformat(),
                "positions": [
                    {
                        "symbol": position.symbol.value,
                        "base_asset": position.symbol.base_asset,
                        "quote_asset": position.symbol.quote_asset,
                        "quantity": str(position.quantity),
                        "average_entry_price": str(position.average_entry_price),
                        "cost_basis": str(position.cost_basis),
                    }
                    for position in state.positions
                ],
            },
        }

    def _persist_cycle(self, close_time: datetime, decision_time: datetime) -> None:
        self._store.append(
            kind="cycle",
            key=f"cycle:{close_time.isoformat()}",
            recorded_at=decision_time,
            payload={
                "close_time": close_time.isoformat(),
                **self._state_payload(),
            },
        )

    def _restore(self) -> None:
        checkpoint = self._latest_checkpoint()
        if checkpoint is None:
            return
        payload = checkpoint
        account = payload["account"]
        if not isinstance(account, dict):
            msg = "checkpoint account must be an object"
            raise RuntimeEngineError(msg)
        positions_raw = account.get("positions", [])
        if not isinstance(positions_raw, list):
            msg = "checkpoint positions must be a list"
            raise RuntimeEngineError(msg)
        positions = tuple(
            AccountingPosition(
                symbol=Symbol(
                    value=str(row["symbol"]),
                    base_asset=str(row["base_asset"]),
                    quote_asset=str(row["quote_asset"]),
                ),
                quantity=Decimal(str(row["quantity"])),
                average_entry_price=Decimal(str(row["average_entry_price"])),
                cost_basis=Decimal(str(row["cost_basis"])),
            )
            for row in positions_raw
            if isinstance(row, dict)
        )
        unbudgeted = [
            position.symbol.value
            for position in positions
            if position.symbol.value not in self._parameters.risk_budgets
        ]
        if unbudgeted:
            msg = (
                "restored positions exist for symbols outside the configured "
                f"risk budgets: {sorted(unbudgeted)}; restore the budget entry "
                "or exit the position before shrinking the universe"
            )
            raise RuntimeEngineError(msg)
        self._ledger = VirtualAccountLedger(
            account_id=str(account["account_id"]),
            cash=Decimal(str(account["cash"])),
            positions=positions,
            realized_pnl=Decimal(str(account["realized_pnl"])),
            unrealized_pnl=Decimal(str(account["unrealized_pnl"])),
            equity=Decimal(str(account["equity"])),
            peak_equity=Decimal(str(account["peak_equity"])),
            drawdown=Decimal(str(account["drawdown"])),
            updated_at=datetime.fromisoformat(str(account["updated_at"])),
            events=(),
        )
        # Cycle checkpoints written before the crash-safety upgrade carried
        # only close_time; fall back to it so existing stores restore cleanly.
        last_processed_raw = payload.get("last_processed") or payload.get("close_time")
        self._last_processed = (
            datetime.fromisoformat(str(last_processed_raw)) if last_processed_raw else None
        )
        self._start_of_day_equity = Decimal(str(payload["start_of_day_equity"]))
        executed = payload.get("executed_fractions", {})
        decisions = payload.get("decision_fractions", {})
        if isinstance(executed, dict):
            for key, value in executed.items():
                if key in self._executed_fractions:
                    self._executed_fractions[key] = Decimal(str(value))
        if isinstance(decisions, dict):
            for key, value in decisions.items():
                if key in self._decision_fractions:
                    self._decision_fractions[key] = Decimal(str(value))

    def _latest_checkpoint(self) -> dict[str, object] | None:
        """Most recent restart checkpoint: an end-of-cycle snapshot or, if a
        crash interrupted a cycle after fills, the last fill's checkpoint."""

        for event in reversed(self._store.all_events):
            if event.kind == "cycle":
                return dict(event.payload)
            if event.kind == "fill":
                embedded = event.payload.get("checkpoint")
                if isinstance(embedded, dict):
                    return dict(embedded)
        return None

    # ── validation helpers ─────────────────────────────────────────────

    def _require_universe(self, candles_by_symbol: Mapping[str, tuple[Candle, ...]]) -> None:
        if set(candles_by_symbol) != set(self._parameters.risk_budgets):
            msg = (
                "candles must cover exactly the budgeted decision universe; "
                f"candles={sorted(candles_by_symbol)} "
                f"budgets={sorted(self._parameters.risk_budgets)}"
            )
            raise RuntimeEngineError(msg)

    def _aligned_latest_candles(
        self, ordered: Mapping[str, tuple[Candle, ...]]
    ) -> dict[str, Candle]:
        latest = {symbol_value: candles[-1] for symbol_value, candles in ordered.items()}
        # Align by trading day, not exact close timestamp: exchange-maintenance
        # days produce truncated daily candles whose close times differ by
        # milliseconds across symbols (e.g. Binance 2018-02-08).
        open_dates = {candle.open_time.date() for candle in latest.values()}
        if len(open_dates) != 1:
            msg = "latest closed candles must align across all budgeted symbols"
            raise RuntimeEngineError(msg)
        return latest

    def _append_health(self, close_time: datetime, code: str, payload: dict[str, object]) -> None:
        self._store.append(
            kind="health",
            key=f"health:{code}:{close_time.isoformat()}",
            recorded_at=close_time,
            payload={"code": code, **payload},
        )


def _skipped(reason: str, *, health: tuple[str, ...] = ()) -> CycleResult:
    return CycleResult(
        processed=False,
        reason=reason,
        close_time=None,
        notifications=(),
        fills=(),
        rejection_reason_codes=(),
        health_codes=health,
        equity=None,
    )


def _closed_sorted(candles: tuple[Candle, ...], symbol_value: str) -> tuple[Candle, ...]:
    if not candles:
        msg = f"candles for {symbol_value} must not be empty"
        raise RuntimeEngineError(msg)
    open_candles = [candle for candle in candles if not candle.is_closed]
    if open_candles:
        msg = f"still-open candles are forbidden for decisions ({symbol_value})"
        raise RuntimeEngineError(msg)
    return tuple(sorted(candles, key=lambda candle: candle.open_time))


def _disaster_for(
    candles: tuple[Candle, ...],
    symbol: Symbol,
    parameters: RuntimeParameters,
) -> RiskEvent | None:
    if len(candles) < 2:
        return None
    return detect_single_day_disaster(
        symbol=symbol,
        previous_close=candles[-2].close_price,
        current_close=candles[-1].close_price,
        occurred_at=candles[-1].close_time,
        threshold_fraction=parameters.disaster_single_day_drop_fraction,
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


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def _notification_from_payload(payload: Mapping[str, object]) -> NotificationEvent:
    """Rebuild a persisted notification event for (re-)delivery."""

    reason_codes_raw = payload["reason_codes"]
    if not isinstance(reason_codes_raw, list):
        msg = "persisted notification reason_codes must be a list"
        raise RuntimeEngineError(msg)
    return NotificationEvent(
        notification_id=str(payload["notification_id"]),
        symbol_value=str(payload["symbol"]),
        action=str(payload["action"]),
        previous_fraction=Decimal(str(payload["previous_fraction"])),
        target_fraction=Decimal(str(payload["target_fraction"])),
        delta_fraction=Decimal(str(payload["delta_fraction"])),
        decision_price=Decimal(str(payload["decision_price"])),
        decision_time=datetime.fromisoformat(str(payload["decision_time"])),
        reason_codes=tuple(str(code) for code in reason_codes_raw),
        risk_status=str(payload["risk_status"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
    )
