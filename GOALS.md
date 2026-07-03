# GOALS.md

Version: `v0.9-daily-signal-redefinition`
Status: Core MVP work queue (redefined after design research)
Last updated: `2026-07-02`
Project: `Crypto Quant Signal MVP v1.0`
Evidence basis: `docs/research/SIGNAL_DESIGN_RESEARCH.md` (2026-07-02)

---

## 0. Product Target

Build a crypto spot, long-only, public-data, DAILY signal notification system.

MVP output:

```text
Every day after the UTC daily close, the system tells the user WHAT to buy or sell and WHY.
The user places orders manually. The system never touches an exchange account.
A 1000 USDT virtual account follows every signal in parallel as the honest scoreboard.
```

Plain language:

```text
系統每天收盤後告訴你該進還是該出，你自己手動下單。
虛擬帳戶同步照做，誠實記錄「如果完全照做會怎樣」。
系統永遠不自動下單，永遠不需要 API key。
```

### 0.1 What Changed From v0.8 (And Why)

Every change below is grounded in adversarially verified evidence
(`docs/research/SIGNAL_DESIGN_RESEARCH.md`, 19 confirmed / 11 refuted claims):

| Decision | v0.8 | v0.9 | Evidence anchor |
| --- | --- | --- | --- |
| Product | Paper trading system | Signal notifications + manual execution + paper scoreboard | User requirement; execution constraints |
| Decision timeframe | 15 minutes | 1 day (UTC close) | No after-cost evidence for <=1h passed verification; daily MA/TSM rules beat buy-and-hold after 10-50bps in 2014-2023 samples |
| First strategy | Large Liquid Trend 15 | Daily Trend Ensemble (SMA 20/65/150/200 ladder) | Single-lookback superiority claims were refuted; ensemble spreads parameter risk and minimizes trials N |
| Universe | Large liquid USDT pairs (BTC/ETH/SOL) | BTC + ETH core; SOL candidate pending its own gate pass | Equal-weighted broad altcoin backtests inflated ~62.19%/yr by survivorship bias; P&D contamination in small caps |
| Validation | Baseline checks only | Six-gate qualification before trusting signals with real money | Unregistered-N backtests are "worthless" (Bailey & Lopez de Prado); random walks can show IS Sharpe 1.27 |
| Live trading | Future goal T (contract-gated) | PERMANENTLY EXCLUDED | Product definition: the user is the executor; no key custody, no order path, ever |

The v0.8 goal history (A-I) remains valid completed work. Its code carries forward;
only the strategy layer and defaults are superseded (see Goal J).

---

## 1. Goal Structure

Goals are split into three groups.

### 1.1 Core MVP

Must be done first, in order.

```text
A-I. Foundation (COMPLETED under v0.8 — see section 3)
J. Daily Signal Strategy Retrofit
K. Backtest + Validation Gate Tooling
L. Signal Runtime (notifications + parallel scoreboard)
M. Read-Only Dashboard
N. Core MVP Complete
```

### 1.2 Post-MVP Validation

```text
O. Signal-Live Qualification (the six-gate pass)
P. Research Lab (pre-registered experiments only)
```

### 1.3 Future Extensions (allowed later, by contract)

```text
Q. HMM / ML Regime Research (research-only)
R. Genetic Algorithm Parameter Research
S. Multi-Strategy Studio
T. Manual Fill Journal (user-entered fills; execution-quality tracking; no private API)
```

### 1.4 Permanently Excluded

```text
Live trading. Auto-execution. Private exchange API. API keys. Real account access.
```

These are not "future goals". They are excluded by product definition:
the user is the only executor. No new contract can re-introduce them into this product.

---

## 2. Global Rules

### 2.1 Allowed In Core MVP

- Public market data (Binance Spot REST/WebSocket).
- Daily closed candles for decisions; 15m candles as data granularity where useful.
- Backtesting with trial registration.
- Paper trading through the virtual account ledger (the scoreboard).
- Virtual orders and virtual fills.
- Spot long-only exposure-ladder portfolio logic.
- Persisted, idempotent signal notification events (advisory only).
- Read-only dashboard/API.

### 2.2 Forbidden In Core MVP (and beyond)

- Real order submission (permanent).
- Private exchange API (permanent).
- API keys (permanent).
- Real account balances (permanent).
- Margin, leverage, borrowing, lending, derivatives, perpetual futures.
- Short selling, negative position quantity, selling more than current holdings.
- Using still-open candles for signal generation.
- Same-bar execution.
- Running any backtest without recording it in the trial registry (from Goal K on).

### 2.3 Baseline Verification

Before claiming any goal complete:

```bash
ruff check .
ruff format --check .
mypy --strict src/
lint-imports
pytest -m "not network" tests -q
```

Network tests are not part of unit tests or CI. Manual public-data smoke tests must be
explicitly marked `pytest.mark.network`.

### 2.4 Signal And Notification Rules

- A notification is advisory output, never an execution instruction to an exchange.
- Every notification carries: symbol, action (increase/decrease ladder step), tranche
  size (fraction of the asset's risk budget), reason codes, decision price, decision
  timestamp (UTC daily close), and current risk status.
- Notifications are persisted before delivery and deduplicated by idempotency
  key; delivery success is recorded as a delivered-marker event. A restart must
  never re-send an already-DELIVERED notification; a persisted-but-undelivered
  one (channel outage) is retried until a marker exists.
- Expected cadence per asset is a handful of notifications per year
  (inference: roughly 4-15). Long silent stretches are correct behavior, not a bug.

### 2.5 Validation Gate (summary)

No strategy output may be represented as "qualified" until all six gates pass
(full text: `docs/contracts/VALIDATION_GATE_CONTRACT.md`):

```text
1. Trial registry: every backtest run recorded; unregistered results are void.
2. Data floor: >=1,000 daily observations spanning bull, bear, and recovery.
3. PBO <= 0.05 via CSCV (S=16).
4. DSR >= 0.95 (effective trial count N).
5. Final holdout: most recent ~12 months, single use, never iterated.
6. >=3 months paper trading with measured real costs within 1.5x assumption.
```

Passing the gate is a necessary condition to trust signals, not a profit guarantee.

---

## 3. Goals A-I: Completed Foundation (v0.8 history)

All nine foundation goals were completed and verified under v0.8
(151 unit tests passing; commits `8b6ac35`, `ca35428`, `f876f10`).
Full original text lives in git history. What carries forward:

- **A. Repository Foundation** — unchanged. Python 3.12, pip + pyproject,
  TimescaleDB/PostgreSQL compose, ruff/mypy/import-linter/pytest baseline.
- **B. Domain Types** — unchanged. `Signal` is LONG/FLAT only; Decimal money;
  UTC-aware timestamps; illegal states unrepresentable.
- **C. Config System** — carried forward; Goal J changes the default decision
  timeframe to `1d` and adds ensemble/ladder parameters. Real-trading and
  private-API flags remain rejected.
- **D. Public Market Data** — carried forward; Goal J adds daily closed-candle
  ingestion beside the existing 15m path. Closed-candle gate, gap/duplicate/stale
  detection, symbol filters, universe snapshot all reused.
- **E. Feature Pipeline** — architecture carried forward; Goal J adds daily SMA
  ensemble features. Existing 15m features remain valid code but are no longer
  the decision path.
- **F. First Strategy (Large Liquid Trend 15)** — SUPERSEDED as the active
  strategy by the Daily Trend Ensemble (Goal J). Code and contract remain in the
  repository as inactive reference; do not delete, do not wire into runtime.
- **G. Portfolio Targets** — carried forward; Goal J maps ladder fractions to
  target weights within per-asset risk budgets.
- **H. Risk Gate** — unchanged checks (no short, no negative, min notional,
  stale-data block, drawdown/daily-loss pause, filters); Goal J adds the
  disaster-notice risk event (single-day -20%).
- **I. Paper Broker + Accounting** — unchanged. The virtual account becomes the
  permanent scoreboard.

---

## Goal J: Daily Signal Strategy Retrofit

### Why

The research verdict: after-cost evidence exists only at daily frequency, for
long-only trend following, on large caps. The pipeline built in A-I is correct;
the decision layer must be retargeted from 15m to daily.

### Build

- Config: default decision timeframe `1d`; ensemble lookbacks `[20, 65, 150, 200]`
  fixed by contract; per-asset risk budgets (BTC 50% / ETH 50%); SOL present in
  config but disabled until it passes the gate.
- Data: ingest and replay Binance Spot public DAILY closed candles (UTC close).
  Keep 15m ingestion available as data granularity (cost measurement, later research).
- Features: daily SMA(20/65/150/200) per asset; sub-signal `close > SMA_n`;
  point-in-time snapshots from closed candles only; warmup rule (no signal until
  200 daily closes).
- Strategy: implement `docs/contracts/STRATEGY_DAILY_TREND_ENSEMBLE.md` —
  target exposure fraction = mean of the four sub-signals (0/25/50/75/100%),
  with reason codes per sub-signal and ladder-change events.
- Portfolio: ladder fraction × asset risk budget → target weight; cash is the
  default state; churn is naturally limited by the ladder (25% steps).
- Risk gate: unchanged; add disaster-notice event (single-day close-to-close
  return <= -20% → forced re-evaluation notification; risk event, NOT a strategy
  parameter).

### Done When

- daily candles can be ingested, validated, and replayed
- ensemble features prove no-lookahead in tests (feature at close t uses closes <= t)
- strategy output is deterministic, long-only, fraction in {0, .25, .5, .75, 1}
- ladder transitions emit correct delta events with reason codes
- portfolio targets never negative, never exceed risk budget, respect max exposure
- 15m path still compiles and passes its tests (not deleted)
- baseline verification passes

### Not Now

- no notification delivery channel yet
- no backtest engine yet (Goal K)
- no parameter search of any kind — lookbacks are contract-fixed
- no SOL activation

---

## Goal K: Backtest + Validation Gate Tooling

### Why

The single biggest unverified assumption is post-2023 edge persistence.
The backtest is the instrument that answers it — but only if every run is
registered and the gate math is built in from day one. An unregistered backtest
is worthless by construction.

### Build

- Historical replay loop over daily candles reusing the same strategy /
  portfolio / risk / execution / accounting code paths.
- Later-bar fill rule: a signal generated at close `t` fills no earlier than the
  next bar; fee + slippage bps from config; no hidden costs.
- Trial registry: EVERY backtest run persists {UTC time, config hash, code
  version, parameter set, data span, cost assumptions, headline metrics}.
  The registry maintains the running trial count N. Runs outside the registry
  are void and must not be cited.
- Validation tooling:
  - CSCV implementation (S=16 blocks, C(16,8)=12,870 splits) producing PBO.
  - DSR computation (skewness, kurtosis, T, variance of trial Sharpes, effective N).
  - Holdout ledger: the most recent ~12 months are locked at first backtest;
    unlocking is a single-use, logged, irreversible event.
  - Cost-stress rerun mode (2x fee assumption).
- Reports: config snapshot, signal/target/order/fill logs, equity curve,
  drawdown curve, metrics, rejected-order report — persisted per run.

### Done When

- end-to-end daily backtest runs on BTC/ETH from earliest clean data through
  the holdout boundary
- registry contains every run ever executed, with monotonically increasing N
- PBO and DSR are computable outputs for the pre-registered ensemble
- holdout lock provably prevents casual OOS reuse (test proves single-use)
- cost-stress rerun produces a comparable report
- no same-bar execution; no still-open candle logic (tests prove both)
- baseline verification passes

### Not Now

- no parameter sweep beyond the contract-fixed ensemble (any additional variant
  requires pre-registration in the research plan and counts toward N)
- no auto-deployment of anything into runtime
- no gate PASS/FAIL declaration yet (that consumes the holdout; see Goal O)

---

## Goal L: Signal Runtime (Notifications + Parallel Scoreboard)

### Why

This is the product: the loop that watches public daily closes, decides,
notifies the user, and keeps the scoreboard honest.

### Build

- Load config; connect public data; detect UTC daily close per asset.
- Compute features → strategy → portfolio targets → risk gate.
- On ladder change: persist a notification event (content per §2.4), then
  deliver through the configured channel(s): dashboard "current signal" view is
  mandatory; one push channel (webhook-based, e.g. Telegram or Discord) behind a
  config flag.
- Feed the same approved actions to the paper broker; the virtual account
  executes them as the scoreboard.
- Persist everything: config snapshot, candle events, feature snapshots,
  signals, targets, risk decisions, notifications, virtual orders/fills,
  account snapshots, health events.
- Restart safety: idempotency keys make both notifications and virtual orders
  duplicate-proof across restarts; every fill embeds a state checkpoint so a
  crash BETWEEN a fill and the end-of-cycle snapshot can never lose the fill.
- Stale data: halt new exposure increases; risk-reducing exits remain allowed;
  record stale-halt rejections and a stale health event (dashboard-visible).
- Missed days: a skipped calendar day means a skipped decision; the next cycle
  records a MISSED_DAYS health event and executes any pending ladder change at
  the latest candle's open (an honest gap, not a silent backfill).

### Done When

- runtime processes a recorded closed-candle replay end-to-end
- runtime processes a manual public-data one-cycle smoke
- restart produces zero duplicate notifications and zero duplicate orders (test proves it)
- every notification is persisted with reason codes before delivery
- scoreboard account state matches the ledger after every cycle
- no API keys anywhere; no real order path exists
- baseline verification passes

### Not Now

- no multi-channel notification fanout beyond the single configured channel
- no intraday decision loop
- no auto-execution, ever

---

## Goal M: Read-Only Dashboard

### Why

The user needs one place that answers: what is the system telling me to do
right now, why, and how has following it worked out?

### Build

Read-only views, priority order:

1. Current signal state per asset: ladder position (0-100%), each sub-signal,
   reason codes, last notification, decision price/time.
2. Scoreboard: equity curve, USDT cash, positions, realized/unrealized PnL,
   drawdown vs buy-and-hold reference.
3. Risk status: active pauses, disaster notices, stale-data state.
4. Audit: virtual orders, fills, rejected orders with reason codes.
5. Gate status: trial count N, PBO/DSR when computed, holdout lock state,
   paper-trading day counter.
6. Data freshness / runtime health.

MVP stack unchanged: FastAPI + Jinja2/static page + polling JSON endpoints.

### Done When

- dashboard shows current per-asset signal state with reasons
- dashboard shows why the scoreboard bought or sold, and why orders were rejected
- gate status page reflects the trial registry truthfully
- API cannot submit orders, change risk limits, or touch private data
- baseline verification passes

### Not Now

- no SPA framework
- no manual trading controls
- no notification-channel management UI (config file only)

---

## Goal N: Core MVP Complete

### Done When

1. baseline verification passes
2. daily public data can be ingested and replayed
3. the ensemble strategy produces deterministic ladder decisions on closed daily candles
4. portfolio targets respect risk budgets; risk gate approves/rejects with reason codes
5. paper broker + scoreboard update cash, positions, PnL, equity correctly
6. backtest runs end-to-end with trial registration, PBO/DSR tooling, and locked holdout
7. runtime replay smoke passes; restart duplicates nothing
8. notifications are persisted, idempotent, and visible on the dashboard
9. dashboard shows signal state, scoreboard, rejections, and gate status
10. no private API path exists; no real order path exists
11. 15m legacy path still present and passing its tests, inactive

### Explicitly NOT Required For Core MVP Complete

- gate PASS declaration (Goal O — needs the holdout spend and 3 months of paper time)
- external push channel beyond one config-gated webhook
- research lab, HMM, GA, multi-strategy, live anything

---

## Goal O: Signal-Live Qualification

### Status

Post-MVP validation gate. The moment of truth.

### Why

Core MVP completion means the machine works. This goal answers whether the
strategy deserves real money — by spending the single-use holdout and the
3-month paper run, then publishing the verdict.

### Procedure

1. Freeze code + config; register the final pre-registered trial set (N frozen).
2. Compute PBO (CSCV S=16) and DSR over the registered trials → require
   PBO <= 0.05 and DSR >= 0.95.
3. Unlock and spend the holdout (single use, logged): walk-forward the frozen
   strategy over the held-out ~12 months. No iteration. If it fails, the
   strategy family goes back to research with a fresh N and a new holdout
   accumulation period.
4. Run >=3 calendar months of signal runtime paper trading:
   - 0 real order attempts, 0 private API usage, 0 critical crashes
   - ledger reconciliation passes; every fill has fee/slippage; every reject has a reason code
   - measure notification→(simulated) execution delay and real spread/fees;
     if measured round-trip cost > 1.5x the 25-30bps assumption, recalibrate
     costs and return to step 2.
5. Publish the gate report (pass or fail) to `docs/reports/`.

### Done When

All six gates have recorded outcomes, and the report exists — whatever the verdict.

Plain language:

```text
就算結果是「不合格」，這個目標也算完成。
閘門的工作是說真話，不是放行。
```

---

## Goal P: Research Lab (Pre-Registered Experiments Only)

### Status

Post-MVP extension. Every experiment counts toward N.

### Backlog (each requires pre-registration before any backtest)

- Regime filter v1.1: hold ETH/SOL only when BTC > 200d SMA — adopt only if it
  passes the gate AND improves both after-cost Sharpe and max drawdown.
- Stop overlays: trailing / volatility-scaled / time stops vs pure signal-exit.
- Donchian ensemble vs MA ensemble (pick one, never run both live).
- SOL admission: SOL passes the full gate on its own data, or stays out.
- Execution-delay sensitivity: simulate 5min / 1h / 8h delays on daily signals.
- Cost stress: full report at 2x fees.
- Universe v2 exploration: top-N liquidity rotation (only with survivorship-bias-free
  local data including delisted symbols).

### Rules

- Research must not auto-deploy results into runtime.
- Research must not hide failed trials — the registry is append-only.
- Expanding the search space after seeing results requires a new registered
  experiment; silent expansion voids the gate.

---

## Future Goals (by contract only)

### Future Goal Q: HMM / ML Regime Research — research-only until a new contract.
### Future Goal R: Genetic Algorithm Parameter Research — only after the trial
registry and backtest reproducibility have proven reliable through Goal O.
### Future Goal S: Multi-Strategy Studio — only after one strategy passes Goal O.
### Future Goal T: Manual Fill Journal — user manually records real fills;
system compares them to scoreboard fills for execution-quality tracking.
No private API; the journal is user-entered data.

---

## Final Rule

```text
Core MVP should be small enough to finish.
The gate should be strict enough to trust.
Signals earn belief by surviving verification, not by looking good in-sample.
Advanced features must be added by contract, not by quietly expanding scope.
```
