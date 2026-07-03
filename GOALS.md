# GOALS.md

Version: `v1.0-tw-adaptation`
Status: TW Core MVP work queue
Last updated: `2026-07-03`
Project: `TW Stock Signal MVP v1.0`
Evidence basis: `docs/research/TW_SIGNAL_DESIGN_RESEARCH.md` (2026-07-03)
Plan: `docs/plans/TW_STOCK_VERSION_PLAN.md`
Provenance: forked from the crypto signal MVP at commit `ada007e` (its goal
history A-O lives in this repository's git history and does not repeat here)

---

## 0. Product Target

Build a Taiwan-listed-equity, long-only, public-data, DAILY signal
notification system.

MVP output:

```text
Every trading day after the Taipei close (data final ~17:30, job at 18:00),
the system tells the user what the 0050 exposure ladder should do and WHY.
The user places orders manually on the next trading day.
A 100,000 TWD virtual account follows every signal in parallel as the honest
scoreboard — including dividends, fees, and taxes.
The system never touches a brokerage account and never needs any credential.
```

Plain language:

```text
系統每個交易日收盤後告訴你 0050 的階梯該加還是該減，你自己手動下單。
虛擬帳戶同步照做，誠實記錄「如果完全照做會怎樣」——含股利、手續費、證交稅。
系統永遠不自動下單，永遠不需要任何憑證。
```

### 0.1 What Changed From The Crypto Edition (And Why)

Every change is grounded in verified research
(`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md`):

| Decision | Crypto edition | TW edition | Evidence anchor |
| --- | --- | --- | --- |
| Registered claim | Beat buy-and-hold after costs | **Drawdown reduction**: MaxDD ≤ 60%×B&H and CAGR ≥ B&H−3pp (dividend-adjusted, full costs) | Post-2000 index-timing return claims refuted; drawdown reduction is the replicated result |
| Universe | BTC 50% / ETH 50%; SOL candidate | **0050 at 100%**; 2330 excluded by evidence; 006208 execution-alternative only | TW firm-level MA evidence weakest in mega-caps; ETF sell tax 0.1% vs stock 0.3% |
| Decision time | UTC daily close | Taipei close 13:30; data final ~17:30; job ≥18:00; execution next trading day open | TWSE data timing verified by fetch |
| Data source | Binance Spot public | TWSE RWD + OpenAPI (keyless) + FinMind anonymous backfill; in-house adjustment factors | `docs/contracts/DATA_ADAPTER_TWSE.md` |
| Price series | Single series | **Dual series**: raw (accounting) + adjusted (signals) | Ex-dividend days are false trend breaks otherwise; 0050 split 2025-06 |
| Calendar | Continuous 1d bars | Exchange trading calendar; holidays/make-up-day closures/typhoons/suspensions are not data gaps | ~245 trading days/yr; LNY up to 11 calendar days |
| Disaster event | Single-day ≤ -20% | Single-day ≤ -9% OR 3-session ≤ -15% | ±10% price limit makes -20% impossible; 2025-04 precedent |
| Costs | Flat 10bps + 5bps slippage | Commission ×discount with min fee + sell-only tax by instrument + tick table (interim flat 13.5bps until TW-D) | Verified fee/tax schedule |
| Accounting | Buy/sell fills only | + dividend accrual/payment events, split adjustments | 0050 semi-annual dividends; 2025 split |
| Benchmark | Buy-and-hold reference | **0050 total-return B&H mandatory**; price-only TAIEX comparisons void | 2.3× dividend wedge since 2003 |

Unchanged: layered architecture, module boundaries, six-gate validation
mathematics (registry / CSCV-PBO / DSR / single-use holdout / 3-month paper /
cost recalibration), idempotent notifications, append-only ledger, read-only
dashboard, all hard safety rules.

---

## 1. Goal Structure

### 1.1 TW Core MVP (in order)

```text
TW-A. Bootstrap: fork hygiene, TW identity, contracts, research archive   [DONE]
TW-B. Domain / Config / Trading calendar
TW-C. Data layer: TWSE + FinMind clients, adjustment pipeline, backfill
TW-D. Strategy / Portfolio / Risk / Execution / Accounting retrofit
TW-E. Backtest wiring + holdout lock + registered trial #1 (moment of truth 1)
TW-F. Runtime + notifications + dashboard
TW-G. TW Core MVP complete (real-data end-to-end acceptance)
```

### 1.2 Post-MVP Validation

```text
TW-H. Signal-Live Qualification (six gates + primary claim; moment of truth 2)
```

### 1.3 Future Extensions (by contract only, each pre-registered)

```text
Second asset sleeve (bond/overseas ETF) — own gate pass required
Hysteresis-band variant; Donchian variant
Same-day execution via the 14:00-14:30 fixed-price session
Limit-lock deferred-fill model
Manual fill journal (user-entered real fills; execution-quality tracking)
```

### 1.4 Permanently Excluded

```text
Live trading. Auto-execution. Broker APIs (Shioaji, Fugle, ...).
Any credential or account access. Margin, leverage, short exposure,
derivatives. 2330 single-stock timing (evidence-excluded; reopening it
requires new evidence AND a pre-registered experiment).
```

---

## 2. Global Rules

### 2.1 Allowed In TW Core MVP

- Public keyless market data (TWSE RWD/OpenAPI, FinMind anonymous).
- Daily closed candles for decisions (Taipei close).
- Dual price series: raw for accounting, adjusted for signals.
- Backtesting with trial registration (N starts at zero in this repo).
- Paper trading through the virtual account ledger (the scoreboard),
  including dividend and split events.
- Spot long-only exposure-ladder portfolio logic; odd-lot (1-share) sizing.
- Persisted, idempotent signal notification events (advisory only).
- Read-only dashboard/API.

### 2.2 Forbidden (Core MVP and beyond)

- Real order submission (permanent). Broker APIs (permanent).
- Credentials of any kind; paid data subscriptions.
- Margin, leverage, borrowing, lending, derivatives, short selling.
- Using still-open sessions for signal generation; same-bar execution.
- Feeding RAW (unadjusted) closes into SMA features.
- Comparing against the price-only index in any report.
- Running any backtest without recording it in the trial registry (from
  TW-E on).

### 2.3 Baseline Verification

Before claiming any goal complete:

```bash
ruff check .
ruff format --check .
mypy --strict src/
lint-imports
pytest -m "not network" tests -q
```

Network tests are manual, explicitly marked `pytest.mark.network`.

### 2.4 Signal And Notification Rules

- A notification is advisory output, never an execution instruction.
- Every notification carries: symbol, action (ladder step change), tranche
  size, reason codes, decision price (raw close), decision timestamp, and
  current risk status.
- Notifications are persisted before delivery, deduplicated by idempotency
  key; restarts never re-send a DELIVERED notification.
- Expected cadence: roughly 2-10 ladder changes per year. Long silences are
  correct behavior, not a bug.

### 2.5 Validation Gate (summary)

No strategy output may be represented as "qualified" until all six gates
pass AND the pre-registered primary claim holds (full text:
`docs/contracts/VALIDATION_GATE_CONTRACT.md`):

```text
1. Trial registry: every backtest recorded; unregistered results are void.
2. Data floor: >=1,000 trading-day observations; recommended span 2008+.
3. PBO <= 0.05 via CSCV (S=16).
4. DSR >= 0.95 (effective trial count N).
5. Final holdout: most recent ~12 months, single use, never iterated.
6. >=3 months paper trading; measured costs within 1.5x the 30-35bps assumption.
+  Primary claim: MaxDD <= 60% x B&H and CAGR >= B&H - 3pp (0050 TR benchmark).
```

A FAIL verdict is a successful outcome; the honest conclusion may be
"buy-and-hold, not this signal".

---

## Goal TW-A: Bootstrap (fork hygiene, identity, contracts) — DONE 2026-07-03

### Why

Start the TW product from the proven crypto architecture without inheriting
anything market-specific or gate-contaminating.

### Built

- Forked with full history; crypto gate artifacts DELETED (trial registry,
  holdout lock, backtest reports, completion reports) — TW's N starts at zero.
- Deleted dead crypto code: Binance client/hosts/smoke, 15m feature pipeline,
  Large Liquid Trend 15 strategy, their tests, Binance-dependent scripts.
- TW identity: pyproject `tw-stock-trading`, DB `tw_quant` on port 54321,
  dashboard port 8001, TWD account (100,000 initial), config models rewritten
  (TWSE hosts, TW symbol validation, TW risk defaults, interim flat cost).
- Dashboard HTML extracted to `src/api/templates/dashboard.html` (452-line
  string literal removed from Python; fixes 39 pre-existing lint violations).
- Docs: README, GOALS, architecture, research archive
  (`TW_SIGNAL_DESIGN_RESEARCH.md`), contracts (strategy TW edition, universe
  TW edition, validation gate TW edition, risk gate TW amendments, new
  `DATA_ADAPTER_TWSE.md`), AGENTS.

### Done When (verified)

- baseline verification passes (ruff, format, mypy --strict, lint-imports,
  254 unit tests)
- no references to deleted crypto modules remain in code
- TW contracts and research archive exist
- crypto trial-registry/holdout artifacts are absent

### Known Interim State (by design, closed by later goals)

- `stale_data_max_age_seconds` is wall-clock (TW-D switches to trading days).
- `fee_bps` is flat 13.5 (TW-D introduces the full commission/tax/min-fee model).
- Remaining test fixtures still use crypto symbols where the module under
  test is market-agnostic; they convert per-module during TW-B/TW-D.
- No data client exists yet (TW-C); ingest/runtime CLIs return in TW-C/TW-F.

---

## Goal TW-B: Domain / Config / Trading Calendar

### Why

Every later layer needs trading-day arithmetic and the TW candle identity
(`trading_date`). The calendar is the single most load-bearing new module:
gap detection, staleness, missed-day semantics, scheduling, and backtest
alignment all sit on it.

### Build

- `src/data/calendar.py`: TWSE holiday-schedule ingestion (ROC dates,
  settlement-only rows filtered, informational rows filtered), trading-day
  arithmetic (`is_trading_day`, `next/previous_trading_day`,
  `trading_days_between`), year-boundary handling (API returns current year
  only — persisted schedule files per year), unscheduled-closure
  reconciliation protocol (typhoon days) per `DATA_ADAPTER_TWSE.md`.
- Domain: `Candle.trading_date` (Taipei date) alongside UTC timestamps;
  ledger event type enums for dividends/splits (types only; accounting logic
  lands in TW-D).
- Config: calendar file path + schedule-refresh settings.
- Fixtures: 2026 official schedule (verified 2026-02-20 make-up-day closure,
  LNY block 2/12-2/20), synthetic typhoon-day cases.

### Done When

- calendar arithmetic proven by tests: LNY gap, make-up Saturday closed,
  cross-year queries, typhoon reconciliation states
- `trading_date` present on candles and file round-trips
- baseline verification passes

### Not Now

- no live holiday-API fetch in unit tests (fixtures only; fetch is a
  network-marked smoke)

---

## Goal TW-C: Data Layer (clients, adjustments, backfill)

### Why

The biggest new engineering in the port. Everything the strategy sees flows
through this layer, and TW correctness lives here: parsing traps, the dual
price series, and suspension tolerance.

### Build

- `src/data/twse.py`: RWD STOCK_DAY monthly client (throttled, sequential),
  OpenAPI holiday/ex-dividend-schedule clients; all parsing traps from
  `DATA_ADAPTER_TWSE.md` (ROC dates x3 formats, comma numbers, `X` change,
  `**` note, embedded HTML).
- `src/data/finmind.py`: anonymous TaiwanStockPrice/TradingDate/
  DividendResult/SplitPrice clients for backfill and reconciliation.
- `src/data/adjustments.py`: corporate-action event table (TWT49U/TWTCAU/
  TWTAUU), chained factor computation, adjusted-series builder; append-only
  persisted factor table.
- `src/data/quality.py`: calendar-aware rewrite (GAP = missing trading day;
  STALE in trading days; SUSPENSION tolerance).
- Backfill script: FinMind 2003→today + TWSE 2010→today month-by-month
  reconciliation, splice, persist to `data/candles/`; dual-series output.
- Golden tests: 0050 2025-06 split month, one 0050 dividend, one 2330
  dividend, TWT49U pre-2011 schema, reconciliation-mismatch blocking.

### Done When

- 0050 full history backfilled locally; TWSE/FinMind overlap reconciles
  exactly (2010+)
- adjusted series passes golden tests (split factor ≈ 4, dividend factors)
- network smokes pass (`pytest.mark.network`, manual)
- baseline verification passes

---

## Goal TW-D: Decision + Accounting Retrofit

### Why

Point the proven decision pipeline at TW semantics: adjusted inputs, TW
costs, TW risk triggers, dividend-aware accounting.

### Build

- Features/strategy: adjusted-close input wiring (math unchanged;
  contract-fixed lookbacks).
- Portfolio: weight→shares with odd-lot granularity (`quantity_mode`).
- Risk: disaster dual trigger (single-day -9% / 3-session -15%), staleness
  in trading days, LIMIT_DAY flagging, TW instrument rules (tick table,
  lot granularity) replacing exchange-filter payloads.
- Execution: full TW cost model — commission ceiling × discount with min
  fee, sell-only tax by instrument type (ETF 10bps / stock 30bps), bracketed
  tick rounding, slippage.
- Accounting: DIVIDEND_ACCRUED (ex-date, counts in equity) /
  DIVIDEND_PAID (pay date) / SPLIT_ADJUST (quantity ×ratio, avg cost ÷ratio,
  equity invariant); optional NHI 2.11% withholding flag (default off).
- Tests: limit-down week disaster trigger, dividend three-event lifecycle,
  min-fee erosion, split equity invariance, LNY staleness non-event.

### Done When

- deterministic decisions on adjusted series; fills/fees/taxes itemized and
  auditable per event
- all TW risk triggers proven by tests
- baseline verification passes

---

## Goal TW-E: Backtest + Holdout Lock + Registered Trial #1

### Why

Moment of truth 1. The registry/PBO/DSR/holdout tooling carries over; this
goal wires TW data and costs in, LOCKS the holdout, and runs the first
registered trial that adjudicates the primary claim in-sample.

### Build

- Backtest engine wiring: calendar-aware replay, TW cost model, dividend
  events in equity, benchmark leg (0050 TR buy-and-hold on the same span),
  optional idle-cash yield (default 0%, sensitivity 1.5%).
- Holdout lock: most recent ~12 months locked BEFORE trial #1.
- Trial #1 (registered): ensemble on 0050, 2008-01 → holdout boundary,
  full costs; cost-stress rerun at 2×.
- Whipsaw census report: round-trip count and cost drag for 2010-2016 and
  2023-2026 segments; 2025-04 crash reconstruction (exit/re-entry dates and
  net effect).

### Done When

- registry has trial #1 (N=1) with benchmark metrics recorded
- holdout lock proven single-use by test
- report adjudicates the primary claim in-sample honestly
- **if the claim fails in-sample: skip TW-F, write the FAIL report
  (TW-H procedure), and stop — do not build runtime for a dead strategy**
- baseline verification passes

---

## Goal TW-F: Runtime + Notifications + Dashboard

### Why

The product loop: watch the Taipei close, decide, notify, keep the
scoreboard honest — through holidays, typhoons, and suspensions.

### Build

- Daily cycle at ≥18:00 Taipei: calendar check → fetch → reconcile →
  quality gate → decide → notify → scoreboard fill next trading day open.
- Health events: UNSCHEDULED_CLOSURE, SUSPENSION, DATA_SOURCE_DISAGREEMENT,
  MISSED_DAYS (trading days only), LIMIT_DAY.
- Dividend runtime hooks: ex-date accrual, pay-date cash-in on the calendar.
- Notification content in TWD with security name (0050 元大台灣50);
  idempotent restart proven.
- Dashboard TW display: ladder state, scoreboard vs 0050 TR benchmark curve,
  dividend calendar/events, gate status, data freshness, risk states.
- `scripts/run_paper_runtime.py` + `run_daily_cycle.cmd` return (weekday
  18:00 Taipei schedule; runtime itself re-checks the calendar).

### Done When

- recorded-replay end-to-end + one real public-data cycle smoke
- restart duplicates nothing (notifications, orders, dividends)
- dashboard renders all TW states; API stays read-only
- baseline verification passes

---

## Goal TW-G: TW Core MVP Complete

### Done When (all on real 0050 data)

1. baseline verification passes
2. daily public data ingested, reconciled, and replayed
3. dual price series proven (signals on adjusted, accounting on raw)
4. deterministic ladder decisions on closed daily candles
5. portfolio/risk approve/reject with reason codes; TW triggers live
6. paper broker + scoreboard correct through fees, taxes, dividends, splits
7. backtest end-to-end with registration, PBO/DSR, locked holdout
8. runtime replay smoke passes; restart duplicates nothing
9. notifications persisted, idempotent, dashboard-visible
10. no broker-API path exists; no credential needed anywhere
11. calendar edge cases proven (LNY, make-up day, typhoon protocol,
    suspension)

---

## Goal TW-H: Signal-Live Qualification

### Why

Moment of truth 2. Spend the single-use holdout, run ≥3 calendar months of
paper, adjudicate the six gates AND the primary claim, publish the verdict.

### Procedure

1. Freeze code + config; freeze the registered trial set (N frozen).
2. Compute PBO (CSCV S=16) and DSR → require PBO ≤ 0.05, DSR ≥ 0.95.
3. Unlock and spend the holdout (single use, logged): walk-forward the
   frozen strategy; adjudicate the primary claim on the holdout.
4. Run ≥3 calendar months of signal runtime paper trading: 0 real order
   attempts, 0 broker-API usage, 0 critical crashes; ledger reconciliation
   including dividends; measured round-trip cost ≤ 1.5× the 30-35bps
   assumption (else recalibrate and return to step 2).
5. Publish the gate report (PASS or FAIL) to `docs/reports/`.

### Done When

All verdicts recorded and the report exists — whatever the verdict.

```text
就算結論是「不合格，請直接買 0050 抱著」，這個目標也算完成。
閘門的工作是說真話，不是放行。
```

---

## Final Rule

```text
TW Core MVP should be small enough to finish.
The gate should be strict enough to trust.
The registered claim is drawdown reduction — never quietly upgrade it.
Signals earn belief by surviving verification, not by looking good in-sample.
Advanced features must be added by contract, not by quietly expanding scope.
```
