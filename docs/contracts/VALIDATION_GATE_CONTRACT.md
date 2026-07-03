# Validation Gate Contract (TW Edition)

Status: TW Core MVP tooling contract + qualification procedure (Goal TW-E tooling, Goal TW-H verdict)
Evidence basis: `docs/research/TW_SIGNAL_DESIGN_RESEARCH.md` (2026-07-03); gate mathematics carried unchanged from the crypto edition

## Purpose

Define the qualification standard the TW strategy must pass before its signals
may be represented as "qualified" — i.e., before the user should consider
following them with real money.

Plain language:

```text
回測看起來會賺不算數。純隨機漫步都能刷出樣本內 Sharpe 1.27。
這六道關卡加上預先登記的主張，是唯一把「真的有東西」和「自欺」分開的程序。
```

Core doctrine (verified sources, unchanged):

- A backtest whose trial count N is not controlled and disclosed is
  **worthless for decision-making** (Bailey & López de Prado, JPM 2014).
- **Iterated out-of-sample is not out-of-sample** (Arnott, Harvey & Markowitz 2019).
- Passing the gate is a **necessary condition, not a profit guarantee**.

## Pre-Registered Primary Claim (TW addition — fixed BEFORE trial #1)

The TW edition adds a primary claim that the gate must adjudicate. It is
written here before the first registered backtest and may not be changed
afterward without voiding the gate:

```text
On the full registered sample AND on the single-use holdout:
  (1) MaxDD(strategy) <= 60% x MaxDD(0050 total-return buy-and-hold)
  (2) CAGR(strategy)  >= CAGR(0050 total-return buy-and-hold) - 3pp
Both after full costs, on dividend/split-adjusted series,
idle-cash yield 0% (sensitivity rerun at 1.5%/yr documented alongside).
```

- The mandatory benchmark is 0050 buy-and-hold with dividends reinvested;
  the TWSE total-return index (發行量加權股價報酬指數) is the secondary
  reference. Price-only TAIEX comparisons are void.
- Return enhancement is explicitly NOT claimed. If both conditions hold but
  CAGR trails by more than expected, the report documents it; if either
  condition fails, the verdict is FAIL and the product conclusion is
  "buy-and-hold, not this signal".

## Gate 1: Trial Registry

- A **trial** is any backtest execution of any strategy variant, parameter
  set, universe, cost assumption, or date range — including "quick checks".
- Every trial persists, append-only:

```text
trial_id (monotonic), utc_time, code_version, config_hash,
strategy_id + full parameter set, universe, data_span, cost_assumptions,
headline_metrics (Sharpe, CAGR, MDD, turnover, n_trades, round_trips),
benchmark_metrics (0050 TR buy-and-hold on the same span), operator_note
```

- The registry maintains the running count `N` and, for DSR, the variance of
  Sharpe ratios across registered trials. `effective_N` accounting for trial
  correlation must be recorded alongside the number.
- **Unregistered results are void**: they must not be cited in any report,
  decision, or conversation as evidence.
- The crypto sibling's registry does NOT carry over: TW N starts at zero in
  this repository, and the crypto artifacts were deleted at fork time.

## Gate 2: Data Floor

- At least `1,000` daily observations (TW: ~4.1 calendar years at ~245
  trading days/yr) per asset.
- The sample must span at least: one bear-market entry (2022: TAIEX -32%),
  one crash-with-V-recovery (2024-08 or 2025-04), one melt-up (2023-2026),
  and one multi-year sideways stretch (2010-2016 class).
- RECOMMENDED registered span: 2008-01 → holdout boundary (~4,400+ obs),
  which adds the 2008 GFC (-58%) — the drawdown the primary claim is really
  about. 0050 public history begins 2003-06-30.
- All series dividend/split-adjusted; the 0050 2025-06 1:4 split and its
  5-session trading halt must be handled, not dropped.

## Gate 3: PBO ≤ 0.05 (CSCV)

- Compute the Probability of Backtest Overfitting via Combinatorially
  Symmetric Cross-Validation over the registered trial performance matrix:
  `S = 16` equal disjoint time blocks → `C(16,8) = 12,870` train/test splits.
- Requirement: `PBO <= 0.05`.
- Known limitation (source paper §5.1): symmetric splits are less suitable
  under strong autocorrelation with large S. If demonstrated to distort
  results on TW data, switch to purged K-fold / CPCV and record the change.

## Gate 4: DSR ≥ 0.95

- Deflated Sharpe Ratio inputs: return skewness, kurtosis, track length T,
  variance of Sharpe ratios across all registered trials, `effective_N`.
- Requirement: `DSR >= 0.95`.
- Known limitation: DSR relies on a normal approximation; price-limit
  truncation (±10%) clips the daily return distribution — if this is shown
  to break the approximation, supplement with a bootstrap confidence
  interval and record the method.

## Gate 5: Single-Use Final Holdout

- At Goal TW-E first-backtest time, the most recent ~12 months of data are
  LOCKED: never read by any trial.
- Unlocking is a single-use, logged, irreversible event (Goal TW-H):
  run the frozen strategy once, walk-forward, no iteration.
- Failure → the strategy family returns to research: new registered
  experiment, fresh N accounting, and a new holdout must accumulate.
  Re-testing against the spent holdout after ANY modification is void.

## Gate 6: Paper Trading ≥ 3 Months

- The signal runtime runs for at least 3 calendar months:
  0 real order attempts, 0 broker API usage, 0 critical crashes;
  ledger reconciliation passes (including dividend events); every fill has
  fee/tax/slippage; every reject has a reason code; no duplicate
  notifications/orders across restarts; unscheduled market closures
  (typhoon days) handled without false staleness halts.
- Measure actual costs: commission after real broker discount, observed
  odd-lot spread, and notification→execution delay.
- Cost assumption to beat: ~30-35 bps round trip for 0050 (commission
  0.1425% x ~0.6 both sides + 0.1% ETF sell tax + slippage). If measured
  round-trip cost exceeds `1.5×` the assumption (> ~50 bps), recalibrate
  the cost model and return to Gate 2's rerun.

## Outcome

- The gate produces a written report in `docs/reports/` with all six verdicts
  PLUS the primary-claim verdict, the registry snapshot (N), and the holdout
  event log — pass or fail.
- A FAIL report is a successful outcome of the process. The gate's job is to
  tell the truth, not to approve.

Plain language:

```text
就算結論是「台股請直接買 0050 抱著」，這個系統也算完成了它的工作。
```

## Anti-Rules (violations void the gate)

- Running backtests outside the registry.
- Reusing or peeking at the holdout, directly or via derived statistics.
- Changing universe, costs, benchmark, or the primary claim after seeing
  results without a new registered experiment.
- Comparing against the price-only index (hides the ~3.5-4%/yr dividend wedge).
- Selecting the reporting window after the fact.
- Representing unqualified signals as qualified, in any channel.
