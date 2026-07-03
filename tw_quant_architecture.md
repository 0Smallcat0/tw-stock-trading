# TW Quant Signal Architecture

Version: `v1.0-tw-adaptation`
Status: system foundation contract
Last updated: `2026-07-03`
Project: `TW Stock Signal MVP v1.0`
Provenance: forked from the crypto signal MVP architecture (`v0.8/v0.9`);
the layered design carries over, the market semantics are Taiwan's.

---

## 0. Product In One Sentence

Build a Taiwan-listed-equity, long-only, DAILY signal notification system
where a `100,000 TWD` virtual account follows every system decision as the
honest scoreboard, and the human is the only executor.

Plain language:

```text
系統每個交易日收盤後看一次公開資料。
它只決定 0050 的階梯部位該加還是該減，並告訴你為什麼。
它只在虛擬帳戶裡交易；股利、手續費、證交稅都誠實入帳。
它永遠不碰券商 API、永遠不需要任何憑證、永遠不下真單。
```

---

## 1. How To Read This Document

This document is the system map, not a technical encyclopedia.

It defines what the system is, what it must never do, how the main modules
connect, and where future features can be added. Detailed rules live in
contracts under `docs/contracts/`; evidence lives in
`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md`.

---

## 2. Product Boundary

### 2.1 TW Core MVP Includes

- keyless public market data (TWSE + FinMind anonymous)
- exchange trading calendar (holidays, make-up-day closures, typhoon
  protocol, suspensions)
- DUAL price series: raw (accounting/display) + dividend/split-adjusted
  (signals/returns)
- closed DAILY candle processing (Taipei close)
- one active strategy (Daily Trend Ensemble, exposure ladder)
- virtual account starting with `100,000 TWD`, including dividend accrual/
  payment and split adjustments
- TW cost model: commission × discount with minimum fee, sell-only
  transaction tax by instrument, bracketed ticks, slippage
- portfolio targets, pre-trade risk gate (TW triggers), paper broker,
  backtest engine with trial registry + locked holdout, signal runtime,
  read-only dashboard/API, full audit trail

### 2.2 TW Core MVP Does Not Include (many permanently)

- real order submission (permanent) — the user is the only executor
- broker APIs: Shioaji, Fugle, or any other (permanent)
- credentials, API keys, paid data (permanent)
- margin, leverage, borrowing, lending, derivatives, short exposure
- intraday decisions; canary/production trading
- 2330 single-stock timing (evidence-excluded; see universe contract)

### 2.3 Future But Not Core MVP (each by pre-registered contract)

- second asset sleeve; hysteresis/Donchian variants
- same-day execution via the 14:00-14:30 fixed-price session
- limit-lock deferred-fill model; manual fill journal
- TPEx/OTC symbols (mind the 張/仟元 unit trap)

---

## 3. Hard Safety Rules

| Rule | Meaning |
|---|---|
| Spot only | Cash equities/ETFs only. |
| Long/flat only | Hold shares or hold TWD. Shorting unrepresentable. |
| No negative quantity | Positions and targets never below zero. |
| Sell only existing holdings | A sell only reduces an existing long. |
| Paper only | Orders are virtual ledger events, never broker orders. |
| Keyless public data only | No credential of any kind, ever. |
| Closed candle only | Still-open sessions never reach the strategy. |
| Later execution | A signal from close `t` executes no earlier than the next TRADING day. |
| Adjusted signals, raw accounting | SMA features read the adjusted series; the ledger books raw prices. |
| Explicit costs | Fills carry commission, tax, slippage; dividends carry their events. |
| Audit trail | Every decision and account change is recorded and explainable. |

---

## 4. System Responsibility Split

```text
Calendar      = knows which days trade and which do not
Data          = fetches, validates, reconciles, adjusts public market data
Features      = turns ADJUSTED closed candles into SMA ensemble numbers
Strategy      = emits the ladder fraction and reason codes
Portfolio     = maps fraction x budget to share targets (odd-lot granularity)
Risk          = approves/rejects with TW triggers (limits, staleness, disaster)
Paper broker  = simulates execution with TW costs on RAW prices
Accounting    = books cash, positions, dividends, splits, PnL, equity
Runtime       = runs the daily loop through holidays and typhoons
Backtest      = replays history with the same core logic + trial registry
Dashboard     = shows what happened, read-only, vs the 0050 TR benchmark
```

Strategy must not: submit orders, size final quantities, bypass risk, touch
the ledger, touch data clients, or read account state.

---

## 5. Main Pipeline

```text
TWSE/FinMind public data
  -> trading-calendar check (holiday? typhoon? suspension?)
  -> candle validation + cross-source reconciliation
  -> corporate-action factors -> adjusted series
  -> closed daily candle gate (Taipei close, data final ~17:30)
  -> universe (0050 pinned)
  -> SMA ensemble features (adjusted closes)
  -> ladder decision + reason codes
  -> portfolio share targets
  -> risk gate (TW triggers)
  -> notification (persisted, idempotent, advisory)
  -> paper broker (next trading day open, TW costs)
  -> virtual ledger (incl. dividends/splits)
  -> dashboard/API
```

| Mode | Data source | Clock | Fill rule |
|---|---|---|---|
| Backtest | local backfilled history | replay clock over trading days | next-trading-day open + TW costs |
| Paper runtime | live public data (job ≥18:00 Taipei) | real clock + exchange calendar | next-trading-day open + TW costs |

---

## 6. MVP Default Choices

| Area | MVP default | Future extension |
|---|---|---|
| Data | TWSE RWD (primary) + FinMind anonymous (backfill/reconcile) | TPEx adapter, other markets by contract |
| Universe | 0050 at 100% budget | second sleeve by gate pass |
| Strategy | Daily Trend Ensemble (contract-fixed) | variants by pre-registered contract |
| Sizing | odd-lot shares (step = 1) | board-lot mode via config |
| Costs | commission 0.1425%×0.6 min NT$20 + ETF sell tax 0.1% + 5bps slippage (interim flat 13.5bps until TW-D) | measured-cost recalibration via Gate 6 |
| Storage | PostgreSQL-compatible (TimescaleDB), port 54321, db `tw_quant` | alternative store by contract |
| Dashboard | read-only FastAPI + static page (`src/api/templates/`) | richer UI post-MVP |
| Benchmark | 0050 total-return buy-and-hold | — (mandatory, not optional) |

---

## 7. Data Source Contract (summary — full text `docs/contracts/DATA_ADAPTER_TWSE.md`)

- Primary daily OHLCV: TWSE RWD `STOCK_DAY` (monthly JSON, keyless,
  same-day final ~17:30, history floor 2010-01-04).
- Backfill + reconciliation: FinMind anonymous (`TaiwanStockPrice` reaches
  2003 for 0050; `TaiwanStockTradingDate` adjudicates typhoon closures).
- Corporate actions: TWSE `TWT49U`/`TWTCAU`/`TWTAUU` (+ OpenAPI `TWT48U_ALL`
  schedule); factors = reference price ÷ prior close, chained, persisted,
  auditable.
- Calendar: OpenAPI `holidaySchedule` (current year only) + runtime
  unscheduled-closure detection.
- Forbidden: broker APIs, credentials, paid data, Yahoo as a raw-price or
  dividend source (returns cross-check only).
- Candle carries: symbol, timeframe, `trading_date` (Taipei), UTC open/close
  times, OHLCV (raw), close status, source, received_at. Volume unit is
  SHARES.

---

## 8. Universe Contract (summary — full text `docs/contracts/UNIVERSE_CONTRACT.md`)

Decision universe pinned by evidence: `0050` at 100% risk budget; 006208 is
an execution alternative only; 2330 excluded. Eligibility floors (listing,
normal status, liquidity, history) apply to any future candidate.

---

## 9. Storage Contract

Runtime must persist: config snapshot, universe snapshot, calendar events,
candle events (raw + adjustment factors), feature snapshots, signals,
targets, risk decisions, notifications, virtual orders/fills, dividend
events, account snapshots, health events.

MVP default: PostgreSQL-compatible store (TimescaleDB recommended), JSONL
event store for the runtime loop. Ledger-like tables are append-only; the
runtime must not silently lose events. SQLite only for unit tests.

---

## 10. Core Modules

```text
src/tw_public_hosts.py  public base URLs (TWSE RWD/OpenAPI, FinMind)
src/domain/             shared types; TWD money as Decimal; trading_date
src/config/             typed configs; rejects real-trading/broker flags
src/data/               calendar.py, twse.py, finmind.py, adjustments.py,
                        quality.py (calendar-aware), files.py
src/features/           daily SMA ensemble on ADJUSTED closes
src/strategies/         Daily Trend Ensemble (contract-fixed)
src/portfolio/          ladder fraction x budget -> share targets
src/risk/               TW triggers: disaster dual rule, trading-day
                        staleness, LIMIT_DAY, tick/lot rules
src/execution/          paper broker with TW cost model
src/accounting/         ledger incl. DIVIDEND_ACCRUED/PAID, SPLIT_ADJUST
src/backtest/           replay + trial registry + CSCV/PBO + DSR + holdout
src/runtime/            daily cycle, restart-safe, calendar-aware
src/api/                read-only dashboard (templates/dashboard.html)
src/monitoring/         health/freshness views
```

Module boundaries are enforced by import-linter; composition happens only in
`src/backtest/` and `src/runtime/`.

---

## 11. Repository Skeleton

```text
pyproject.toml            tw-stock-trading
requirements/constraints-dev.txt
docker-compose.yml        tw_quant @ 54321
README.md  AGENTS.md  GOALS.md  tw_quant_architecture.md
configs/runtime/paper_runtime.yaml
docs/contracts/           STRATEGY_DAILY_TREND_ENSEMBLE, UNIVERSE_CONTRACT,
                          VALIDATION_GATE_CONTRACT, RISK_GATE_CONTRACT,
                          DATA_ADAPTER_TWSE
docs/research/            TW_SIGNAL_DESIGN_RESEARCH.md
docs/plans/               TW_STOCK_VERSION_PLAN.md (fork-time plan)
docs/reports/             gate reports, config snapshots (TW N starts at 0)
scripts/                  run_backtest.py, run_dashboard.py, make_report.py
                          (+ ingest/runtime CLIs return in TW-C/TW-F)
src/  tests/  data/(gitignored)
```

---

## 12. Extension Points

| Future feature | Add through |
|---|---|
| Second asset sleeve | pre-registered experiment + own gate pass + universe amendment |
| Strategy variant | new strategy contract + registry entry |
| TPEx/OTC data | `docs/contracts/DATA_ADAPTER_TPEX.md` |
| Execution-timing change | pre-registered experiment (fixed-price session) |
| Manual fill journal | user-entered data contract; still no broker API |

```text
Future extension is allowed.
Unplanned scope expansion during Core MVP is not allowed.
Live trading is not an extension point. It is excluded by product definition.
```

---

## 13. Design Principle

```text
Keep the architecture expandable.  Keep the main documents readable.
Keep TW Core MVP narrow.           Move details into contracts.
Never weaken safety rules.         Never compare against a price-only index.
```
