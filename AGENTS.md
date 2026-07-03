# AGENTS.md

Version: `v1.0-tw-adaptation`
Status: agent and contributor operating contract
Last updated: `2026-07-03`
Project: `TW Stock Signal MVP v1.0`
Evidence basis: `docs/research/TW_SIGNAL_DESIGN_RESEARCH.md` (2026-07-03)

---

## 0. Purpose

This file tells AI agents, coding assistants, and contributors how to work in
this repository.

The product is a DAILY signal notification system for Taiwan-listed equities
with a paper-trading scoreboard:

```text
daily (Taipei close) keyless public market data decisions
long-only exposure ladder on 0050
signal notifications to the user, who executes manually next trading day
100,000 TWD virtual account follows every signal as the honest scoreboard
   (dividends, fees, and taxes included)
no real orders, no broker API, no credentials — ever (permanent)
```

The registered value proposition is DRAWDOWN REDUCTION, not return
enhancement: the system is not expected to beat 0050 buy-and-hold on CAGR,
and the validation gate adjudicates exactly that claim
(`docs/contracts/VALIDATION_GATE_CONTRACT.md`).

Plain language:

```text
不要把系統做成死路。
也不要把 MVP 做成研究平台或技術百科。
先完成可運行核心，再用合約擴充。
主張是「降回撤」，永遠不要偷偷升級成「賺更多」。
```

---

## 1. Golden Rule

```text
Keep TW Core MVP narrow.
Keep architecture expandable.
Keep documents readable.
Never weaken safety rules.
```

Agents must not add future features just because the architecture has a place
for them. If a feature is not in the current goal, leave an interface or
note, but do not implement it.

---

## 2. Non-Negotiable Safety Rules

### 2.1 Secrets And Credentials

Do not create, edit, print, log, commit, or expose: API keys, tokens,
private keys, passwords, webhook tokens, `.env` files.

This product should NEVER need a credential: TWSE endpoints are keyless and
FinMind's anonymous tier suffices. A FinMind token is allowed only as an
OPTIONAL quota raise, must never be required by code paths or tests, and is
a data token — broker credentials are forbidden permanently.

### 2.2 Trading Scope

Allowed in MVP:

- Taiwan-listed spot equities/ETFs (decision universe: 0050)
- long exposure, flat exposure
- keyless public market data
- backtesting with trial registration
- paper trading through the virtual account ledger (incl. dividends)

Forbidden in MVP (and permanently, by product definition):

- margin, leverage, borrowing, lending, derivatives
- short exposure, synthetic short exposure
- real order submission (permanent — the user is the only executor)
- auto-execution of any kind (permanent)
- broker APIs: Shioaji, Fugle, or any other (permanent)
- credentials / API keys (permanent)
- real account balance access (permanent)
- day trading (odd lots cannot day-trade anyway; the system decides daily)

### 2.3 Signal And Position Rules

- `Signal` may only be `LONG` or `FLAT`. Do not add `SHORT`.
- `Position.quantity` and `TargetPosition.quantity` must never be negative.
- `SELL` can only reduce an existing long position.
- Exposure fraction only in {0, 0.25, 0.5, 0.75, 1}.

### 2.4 Paper Trading Rule

Paper trading is only: virtual order → virtual fill → virtual ledger.
It must never sign broker requests, send real orders, read real balances,
or mutate any real account.

---

## 3. Current MVP Boundary

### 3.1 Build

```text
keyless public data (TWSE RWD/OpenAPI + FinMind anonymous)
  -> trading calendar gate (holiday / make-up-day / typhoon / suspension)
  -> closed DAILY candle gate (Taipei close; data final ~17:30; job >= 18:00)
  -> corporate-action factors -> ADJUSTED series for signals
  -> SMA ensemble features (adjusted closes only)
  -> Daily Trend Ensemble ladder decision
  -> portfolio share targets (odd-lot granularity) x risk budget (0050: 100%)
  -> risk gate (TW triggers)
  -> signal notification (persisted, idempotent, advisory)
  -> paper broker (next trading day open, TW costs on RAW prices)
  -> virtual account ledger (dividends, splits, fees, taxes)
  -> read-only dashboard/API (benchmark: 0050 total-return buy-and-hold)
```

The human executes real trades manually, outside the system.

### 3.2 Do Not Build In Core MVP

```text
real trading bot / broker client / live account manager (excluded permanently)
intraday decision loop
2330 single-stock timing (evidence-excluded)
research lab as a blocker
multi-strategy production allocator
unbounded optimizer, HMM, neural network strategy
```

### 3.3 Initial Virtual Account (the scoreboard)

```text
initial_cash: 100,000 TWD
mode: paper
sizing: odd-lot shares (quantity step = 1)
role: follows every signal in parallel — "what if you followed everything"
real_orders: disabled (permanent);  broker_api: nonexistent (permanent)
```

---

## 4. Plain-Language Design Rule

```text
calendar = knows which days trade
data = fetches, reconciles, adjusts
strategy = decides what the ladder should be
portfolio = decides how many shares
risk = decides whether the action is allowed
paper broker = simulates execution with TW costs
accounting = records what happened (incl. dividends)
```

Strategy must not: submit orders, decide final quantity, bypass risk, touch
the ledger, touch data clients, or read account state.

---

## 5. Architecture Rules

### 5.1 `src/domain/`

Domain is the shared type layer. It must not import business packages,
runtime, backtest, scripts, data adapters, or database clients.

### 5.2 Business Packages

```text
src/data/ (calendar, twse, finmind, adjustments, quality, files)
src/features/  src/strategies/  src/portfolio/  src/risk/
src/execution/  src/accounting/  src/notify/  src/monitoring/
```

A business package may import `src.domain`, itself, and approved third-party
libraries. Composition belongs in `src/backtest/` and `src/runtime/` only.
Boundaries are enforced by import-linter — keep the contracts green.

### 5.3 `scripts/`

Thin CLI wrappers only: parse args, load config, call entry points, print
summaries. No strategy/sizing/risk/execution/accounting/adapter business
logic in scripts.

---

## 6. Data And Timing Rules

### 6.1 Candle And Series Rules

- Decisions use closed DAILY candles (Taipei close 13:30) only.
- TWSE daily data is final ~17:30 Taipei; the daily job runs ≥ 18:00 Taipei.
  TWSE OpenAPI `STOCK_DAY_ALL` is T+1 and must not feed same-day signals.
- DUAL SERIES rule: SMA features and return computations read the
  dividend/split-ADJUSTED series; accounting, fills, and notifications use
  RAW official prices. Feeding raw closes into SMA is a contract violation.
- Adjustment factors come only from official corporate-action rows
  (TWT49U/TWTCAU/TWTAUU), factor = reference price ÷ prior close, persisted
  append-only with their source rows.
- No decision before the warmup floor (200 daily closes per asset).
- If close status is unclear, treat the candle as unusable.

### 6.2 Calendar Rules

- Weekends, scheduled holidays, make-up-workday closures (補班日: market
  CLOSED), typhoon closures, and corporate-action suspensions are NOT data
  gaps and NOT staleness.
- GAP = a missing TRADING day. STALE is measured in TRADING days.
- Unscheduled-closure protocol: no data by ~18:30 Taipei on an expected
  trading day → reconcile with FinMind `TaiwanStockTradingDate`; agreement →
  `UNSCHEDULED_CLOSURE` health event; disagreement →
  `DATA_SOURCE_DISAGREEMENT` and block strategy input for the day.
- The holiday API returns the CURRENT YEAR only — persist per-year schedule
  files and refresh on year boundaries.

### 6.3 Symbol, Money, And Time Rules

- Internal symbols are TWSE codes: `0050`, `2330`, `006208`, `00679B`
  (pattern `^[0-9][0-9A-Z]{3,5}$`). base_asset = code, quote_asset = `TWD`.
- Money is `Decimal` TWD. Volume unit is SHARES (TPEx sources use 張 =
  1,000 shares — convert at the adapter boundary, never downstream).
- All storage/config/report/API timestamps are UTC timezone-aware; candles
  additionally carry `trading_date` (Taipei calendar date). Naive datetimes
  are forbidden.
- ROC dates exist in THREE formats at the adapter boundary (`1150702`,
  `115/06/01` with leading space for 2-digit years, `114年06月16日`);
  they must never leak past `src/data/`.

### 6.4 Execution Timing

- A signal produced from close `t` executes no earlier than the NEXT TRADING
  day (backtest and runtime use the same next-trading-day-open rule).
- No same-bar execution. No still-open-session data in features.

### 6.5 Lookahead Prevention

Stop and report if code: uses future candles, uses future universe
membership, uses a candle before close, forward-fills through suspensions/
holidays for returns, applies an adjustment factor before its ex-date, or
treats the latest incomplete session as closed.

---

## 7. Core MVP Strategy Rules

```text
Daily Trend Ensemble（日線趨勢均線組合，20/65/150/200 日 SMA，曝險五檔階梯）
Contract: docs/contracts/STRATEGY_DAILY_TREND_ENSEMBLE.md
Input: ADJUSTED closes.  Universe: 0050 only (100% budget).
```

Default behavior:

```text
Check once per trading day after the Taipei close.
Ladder up when more trend lines are reclaimed; ladder down toward cash.
No shorting. No dip-buying. No cross-sectional rotation.
Long silences are correct behavior (~2-10 ladder changes/year expected).
```

The four lookbacks {20, 65, 150, 200} are contract-fixed. Changing or tuning
them, adding hysteresis, or adding assets is a new strategy variant: it
requires pre-registration in the trial registry (counts toward N) and a
contract change. Do not hard-code parameters in business logic.

---

## 8. Cost And Execution Rules

Paper execution must include TW costs:

```text
commission = notional x 0.1425% x broker_discount, floor min_fee (NT$20;
             odd-lot NT$1 at some brokers) — both sides
sell tax   = notional x 0.1% (ETF) / 0.3% (stock) — SELL side only
slippage   = configured bps
rounding   = bracketed tick table (ETF <50: 0.01, >=50: 0.05; stocks 6 brackets)
             and share-lot granularity (odd lot = 1 share)
```

(Interim until Goal TW-D: flat `fee_bps` 13.5 approximates the round trip —
see config comments. Replacing it with the full model is TW-D scope.)

Execution rounding rules: quantity respects lot granularity;
cash_after_order never negative; buys reserve estimated costs; reject after
rounding if minimum notional (NT$10,000) is not satisfied. Fills on a day
whose open sits at the ±10% limit carry a `LIMIT_DAY` flag.

Dividends: ex-date books `DIVIDEND_ACCRUED` (counts in equity), pay-date
books `DIVIDEND_PAID` (cash in). Splits book `SPLIT_ADJUST` (quantity
×ratio, average cost ÷ratio, equity invariant).

---

## 9. Runtime Rules

Paper runtime must be restartable, idempotent, auditable, keyless-public-data
only, safe on stale data, duplicate events, partial failure, unscheduled
closures, and multi-day suspensions (0050 was halted 5 sessions in 2025-06).

Runtime must persist: config snapshot, universe snapshot, calendar/health
events, candle events, adjustment factors, feature snapshots, signals,
targets, risk decisions, notifications (persisted BEFORE delivery), virtual
orders/fills, dividend events, account snapshots.

Runtime must not: need credentials, submit real orders, increase exposure on
stale data, duplicate orders or notifications after restart, or treat a
holiday as an incident.

---

## 10. Storage Rules

PostgreSQL-compatible runtime storage (TimescaleDB recommended for local
development). SQLite only for unit tests and fixtures.

Local development credentials in `docker-compose.yml` are explicit dummies:

```text
POSTGRES_USER=tw  POSTGRES_PASSWORD=tw_dev_only  POSTGRES_DB=tw_quant
host port 54321 (54320 belongs to the crypto sibling project)
```

---

## 11. Dashboard/API Rules

MVP API is read-only. Allowed views: current ladder state with sub-signals
and reason codes, notifications history, scoreboard account (vs 0050
total-return benchmark), positions, dividends, virtual orders/fills,
rejected orders, risk status, validation gate status (N, PBO/DSR, holdout
lock, paper-day counter), runtime health, data freshness/reconciliation.

Forbidden: manual buy/sell, real order submit, credential management,
broker account access, changing risk limits from the API.

Stack: FastAPI + static HTML (`src/api/templates/dashboard.html`) + browser
polling JSON. No frontend framework unless a later goal authorizes it.

---

## 12. Research Rules

The VALIDATION GATE tooling is core infrastructure (Goal TW-E): trial
registry, CSCV/PBO, DSR, locked holdout. Full rules:
`docs/contracts/VALIDATION_GATE_CONTRACT.md`.

Non-negotiable from the first backtest onward:

- every backtest run is registered (unregistered results are void);
  TW's N starts at zero in this repository — the crypto sibling's registry
  does not carry over and its artifacts were deleted at fork time
- the final ~12 months are locked as single-use holdout
- iterated out-of-sample is not out-of-sample
- the PRE-REGISTERED PRIMARY CLAIM (MaxDD ≤ 60%×B&H and CAGR ≥ B&H−3pp,
  dividend-adjusted, full costs) may not be modified after trial #1
- every benchmark comparison uses 0050 total-return buy-and-hold;
  price-only TAIEX comparisons are void

Forbidden in Core MVP: GA optimizers, HMM engines, neural strategies,
reinforcement learning, unlimited parameter search, auto-deployment of
research winners.

```text
Research exists to reject fragile parameters, not to find magical parameters.
```

---

## 13. Extension Rules

New strategy / new data source / research lab: each requires its own
contract, tests, declared inputs/outputs, no direct orders, no broker API,
no risk bypass. New ASSETS additionally require a pre-registered experiment
and their own gate pass.

Live trading / auto-execution: PERMANENTLY EXCLUDED by product definition.
If that need ever truly arises, it is a different product in a different
repository.

---

## 14. Testing And Verification

### 14.1 Required Baseline Checks

```bash
ruff check .
ruff format --check .
mypy --strict src/
lint-imports
pytest -m "not network" tests -q
```

Unit tests must not hit TWSE, FinMind, or any external network. Public-data
smokes are manual and marked `pytest.mark.network`.

### 14.2 Required Test Themes

Always test (carried from the crypto edition):

- no SHORT signal; no negative position; fraction only in {0,.25,.5,.75,1}
- no sell greater than holdings; no still-open candle signal
- no decision before the 200-close warmup; no same-bar execution
- no order below minimum notional; no exposure increase on stale data
- no duplicate order/notification after restart; notification persisted
  before delivery with reason codes
- every backtest run appears in the trial registry; holdout lock is
  single-use (second unlock fails)
- virtual account ledger balances after every event

TW additions (each proven by golden tests as the goals land):

- calendar: LNY block, make-up-Saturday closed, cross-year, typhoon protocol
- adjustments: 0050 2025-06 split (÷4, 5-session halt), dividend factors,
  TWT49U pre-2011 schema
- dual series: SMA reads adjusted, ledger books raw
- dividends: accrual/payment lifecycle; split equity invariance
- costs: min-fee erosion; sell-only tax by instrument; tick-bracket rounding
- risk: disaster dual trigger (single-day -9% / 3-session -15%);
  staleness in trading days (LNY is not staleness); LIMIT_DAY flagging
- reconciliation: TWSE/FinMind mismatch blocks strategy input

---

## 15. Git And Change Discipline

### 15.1 Keep Diffs Reviewable

Do not mix unrelated concerns in one commit (strategy vs schema, dashboard
vs execution, research features during Core MVP work).

### 15.2 Commit Message Format

Use decision-record style:

```text
<intent line>

Constraint: <constraint>
Rejected: <alternative> | <reason>
Confidence: <low|medium|high>
Scope-risk: <narrow|moderate|broad>
Directive: <future warning>
Tested: <verification run>
Not-tested: <known gaps>
```

---

## 16. Stop Conditions

Stop and report if a task requires:

- storing or exposing credentials of any kind
- enabling real order submission, any auto-execution, or any broker API
- adding leverage, margin, derivatives, or short exposure
- weakening risk rules to make a bad action pass
- feeding raw (unadjusted) closes into SMA features
- comparing performance against a price-only index
- using still-open sessions for signals
- hiding a failed verification result
- running or citing a backtest outside the trial registry
- touching the locked holdout outside the single-use TW-H procedure
- modifying the pre-registered primary claim after trial #1
- representing unqualified signals as qualified
- adding assets, lookbacks, or variants without pre-registration
- adding research/ML/HMM/GA during Core MVP without authorization

---

## 17. Final Instruction To Agents

```text
Build the TW Core MVP first.
Keep advanced paths possible; do not implement them early.
Make every action testable, explainable, and auditable.
The registered claim is drawdown reduction — let the gate tell the truth.
```
