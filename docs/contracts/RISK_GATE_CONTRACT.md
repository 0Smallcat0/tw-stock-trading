# Risk Gate Contract

Status: Core MVP contract (carried from the crypto edition; TW amendments below)

## TW Amendments (2026-07-03, land with Goal TW-D)

Taiwan market structure changes four risk semantics; the gate interface and
everything else in this contract carry over unchanged:

1. **Disaster event redefined.** Under the ±10% daily price limit a -20%
   single-day close is impossible. TW triggers: single-day close-to-close
   return <= -9% (limit-down territory; precedent 2025-04-07, 0050's
   first-ever limit-down close) OR rolling 3-session cumulative return
   <= -15% (precedent: 2025-04 tariff crash, ~-18% in three sessions).
   Both produce the forced re-evaluation notification; they are risk events,
   not strategy parameters.
2. **Staleness in trading days, not seconds.** Weekends, holidays (Lunar New
   Year: up to 11 calendar days), make-up-workday closures, typhoon closures,
   and corporate-action suspensions are not staleness. Rule: data older than
   `stale_trading_days` TRADING days per the exchange calendar → halt new
   exposure increases; risk-reducing exits remain allowed.
3. **Drawdown pause recalibrated.** Expected strategy drawdown band is
   15-25% (vs 0050 buy-and-hold worst -58% in 2008); the pause default is
   0.40 — above the expected band, low enough to matter. The crypto lesson
   stands: a pause inside the normal band locks the strategy out permanently.
4. **Price-limit awareness.** Fills whose execution-day open sits at the
   daily limit are flagged `LIMIT_DAY` (health event + fill annotation);
   the deferred-fill model for locked sessions is a registered experiment,
   not MVP behavior. Exchange filters for TW are the bracketed tick table
   and share-lot granularity (odd lot = 1 share) instead of Binance-style
   filter payloads.

## Purpose

The risk gate is the only Core MVP component allowed to approve a virtual order intent before paper execution.

It evaluates caller-provided public-market, account, position, and risk-state facts. It does not fetch data, submit orders, mutate ledger state, or call any private API.

## Inputs

The risk gate accepts:

- an `OrderIntent`
- risk-local parameters
- risk-local public exchange filters
- current position for the symbol, if any
- virtual account snapshot
- latest public market-data timestamp
- decision timestamp
- earliest legal execution timestamp
- caller-owned drawdown, daily-loss, account-stop, and trailing-stop state

`src/risk` must not import `src.config`, `src.data`, `src.portfolio`, `src.execution`, `src.accounting`, `src.backtest`, `src.runtime`, `src.api`, `src.monitoring`, or `scripts`.

## Outputs

The risk gate returns a `RiskDecision` with one of:

- `APPROVED`
- `REJECTED`
- `PAUSED`
- `STOPPED`

Every non-approved decision must include at least one reason code.

Approved decisions include `RISK_APPROVED`.

## Required Checks

Goal H checks:

- no short exposure
- no negative quantity
- sell cannot exceed holdings
- stale-data rejection or pause
- no same-bar execution
- configured minimum notional
- exchange filter checks
- exchange minimum notional
- drawdown pause
- daily loss pause
- account stop
- trailing stop

Missing or incomplete exchange filters make a symbol untradable.

## Pause And Stop Rules

Pause conditions block new buys and other risk-increasing actions.

An otherwise valid risk-reducing sell may remain approved during stale-data, drawdown, daily-loss, or trailing-stop pauses.

Account stop is a full account halt. It blocks all virtual orders.

## Broker Boundary

Goal H makes the bypass contract explicit:

```text
The Goal I paper broker must accept only an APPROVED risk decision for the same OrderIntent before it can create or process a virtual order.
```

The domain `VirtualOrder` structure enforces this approved-risk-decision requirement, so broker code cannot construct a virtual order from a raw `OrderIntent` alone.

Goal I remains responsible for implementing and testing broker-level enforcement.

## Forbidden

This contract does not authorize:

- real orders
- private exchange API
- real account access
- live exchange kill switch
- VaR
- advanced portfolio risk model
- paper broker implementation
- accounting ledger implementation
