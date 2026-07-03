# Validation Gate Contract

Status: Core MVP Goal K tooling contract + Goal O qualification procedure
Evidence basis: `docs/research/SIGNAL_DESIGN_RESEARCH.md` §3.7 (2026-07-02)

## Purpose

Define the six-gate qualification standard a strategy must pass before its
signals may be represented as "qualified" — i.e., before the user should
consider following them with real money.

Plain language:

```text
回測看起來會賺不算數。純隨機漫步都能刷出樣本內 Sharpe 1.27。
這六道關卡是唯一把「真的有東西」和「自欺」分開的程序。
```

Core doctrine (verified sources):

- A backtest whose trial count N is not controlled and disclosed is
  **worthless for decision-making** (Bailey & López de Prado, JPM 2014).
- **Iterated out-of-sample is not out-of-sample** (Arnott, Harvey & Markowitz 2019).
- A single holdout/walk-forward split cannot prevent overfitting; ~20 uses of a
  95%-confidence holdout make false positives *expected* (JPM 2014; JoCF 2015).
- Passing the gate is a **necessary condition, not a profit guarantee**: in the
  verified 2022 crash case, the least-overfitted strategy still lost 34.96% in
  two months (arXiv 2209.05559).

## Gate 1: Trial Registry

- A **trial** is any backtest execution of any strategy variant, parameter set,
  universe, cost assumption, or date range — including "quick checks".
- Every trial persists, append-only:

```text
trial_id (monotonic), utc_time, code_version, config_hash,
strategy_id + full parameter set, universe, data_span, cost_assumptions,
headline_metrics (Sharpe, MDD, turnover, n_trades), operator_note
```

- The registry maintains the running count `N` and, for DSR, the variance of
  Sharpe ratios across registered trials.
- `effective_N` for DSR should account for correlation between trials
  (near-identical variants do not count as fully independent); the method used
  must be recorded alongside the number.
- **Unregistered results are void**: they must not be cited in any report,
  decision, or conversation as evidence.

## Gate 2: Data Floor

- At least `1,000` daily observations per asset (~4 years).
- The sample must span at least: the 2021 bull market, the 2022 bear market,
  and the 2023-24 recovery. A strategy tested on one regime is untested.
- Data must include the asset's full local history available from the public
  source; survivorship-safe handling is mandatory for any universe beyond
  BTC/ETH (include delisted symbols or do not test breadth claims at all).

## Gate 3: PBO ≤ 0.05 (CSCV)

- Compute the Probability of Backtest Overfitting via Combinatorially Symmetric
  Cross-Validation over the registered trial performance matrix:
  `S = 16` equal disjoint time blocks → `C(16,8) = 12,870` train/test splits.
- PBO = probability that the in-sample-optimal configuration underperforms the
  median of all configurations out-of-sample.
- Requirement: `PBO <= 0.05` (the verified Neyman-Pearson convention; the looser
  α=10% variant was adversarially REFUTED as a practical gate).
- Known limitation (from the source paper §5.1): symmetric splits are less
  suitable under strong autocorrelation with large S. If demonstrated to distort
  results on our data, switch to purged K-fold / CPCV and record the change here.

## Gate 4: DSR ≥ 0.95

- Deflated Sharpe Ratio inputs: return skewness, kurtosis, track length T,
  variance of Sharpe ratios across all registered trials, `effective_N`.
- Requirement: `DSR >= 0.95`.
- Known limitation: DSR relies on a normal approximation; if crypto fat tails
  are shown to break it on our data, supplement with a bootstrap confidence
  interval and record the method.

## Gate 5: Single-Use Final Holdout

- At Goal K first-backtest time, the most recent ~12 months of data are LOCKED:
  never read by any trial.
- Unlocking is a single-use, logged, irreversible event (Goal O step 3):
  run the frozen strategy once, walk-forward, no iteration.
- Failure → the strategy family returns to research: new registered experiment,
  fresh N accounting, and a new holdout must accumulate (or an explicitly
  documented alternative period). Re-testing against the spent holdout after
  ANY modification is void by the iterated-OOS doctrine.

## Gate 6: Paper Trading ≥ 3 Months

- The signal runtime runs for at least 3 calendar months:
  0 real order attempts, 0 private API usage, 0 critical crashes;
  ledger reconciliation passes; every fill has fee/slippage; every reject has a
  reason code; no duplicate notifications/orders across restarts.
- Measure actual costs: current exchange fee schedule, observed spread, and
  notification→execution delay (simulated or journaled).
- If measured round-trip cost exceeds `1.5×` the assumed 25-30bps
  (i.e., > ~45bps), recalibrate the cost model and return to Gate 2's rerun.

## Outcome

- The gate produces a written report in `docs/reports/` with all six verdicts,
  the registry snapshot (N), and the holdout event log — pass or fail.
- A FAIL report is a successful outcome of the process. The gate's job is to
  tell the truth, not to approve.

## Anti-Rules (violations void the gate)

- Running backtests outside the registry.
- Reusing or peeking at the holdout, directly or via derived statistics.
- Changing universe, costs, or rules after seeing results without a new
  registered experiment.
- Selecting the reporting window after the fact.
- Representing unqualified signals as qualified, in any channel.
