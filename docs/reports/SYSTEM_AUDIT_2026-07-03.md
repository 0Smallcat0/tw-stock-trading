# System Audit Report — 2026-07-03

Method: three independent adversarial review agents (backtest money path;
runtime restart semantics; gate statistics + contract conformance), each
instructed to REFUTE the system's claims, plus direct artifact inspection and
numeric probes. Findings triaged, fixed, and re-verified the same day.

## Verdict

The core is sound — holdout boundary strict, no same-bar execution, DSR/PBO
math verified numerically against the published papers, notification content
conforms to GOALS §2.4, API truly read-only, clean-shutdown restarts
round-trip exactly. The audit found **2 confirmed bugs in the backtest
metrics/sizing path, 1 critical crash-window defect in the live runtime, and
a set of gate-process holes** — all fixed below. After fixes, the two
independent engines (backtest vs runtime replay) agree on 7.3 years of real
data **bit-for-bit** (final equity 14221.27753677073000 on both).

## Fixed (same day, commit-referenced)

| # | Finding (severity) | Fix |
|---|--------------------|-----|
| 1 | **Runtime: mid-cycle crash or webhook outage silently loses fills** — restore used only the end-of-cycle snapshot while order-key dedup skipped re-execution (proven by probe: cash 1000 / no positions vs control ~0 cash / 2 positions) | Every fill event now embeds a full state checkpoint; restore takes the newest cycle-or-fill checkpoint; order-without-fill is skipped WITHOUT marking (fresh retry next day); proven by a truncation-crash test that reconverges bit-for-bit with the uninterrupted run |
| 2 | **Runtime: notification delivery inline in the decision loop** — an httpx error aborted the cycle after fills (triggering #1); delivery was at-most-once, unretried, unaudited; HTTP 4xx counted as delivered | Delivery decoupled: persist → complete cycle → flush; delivered-marker events make delivery exactly-once-with-retry; failures logged as health events and never propagate; webhook now `raise_for_status()` |
| 3 | **Backtest: cash-capped buys marked fully executed** — the shortfall became permanent and fell entirely on the alphabetically-last symbol (up to 17pp underweight after drift) | Costs sized INSIDE the ladder claim (÷(1+fee+slip)); genuinely capped fills record the achieved fraction and retry the remainder next cycle (both engines, identical rule) |
| 4 | **Backtest: rejected_count double-counted broker rejections** into registry headline metrics | Broker and gate rejections are disjoint in the metric; invariant asserted in tests |
| 5 | Gate 1 contract required `turnover`; never recorded | `total_traded_notional` + `annualized_turnover` added to metrics, reports, registry |
| 6 | Gate 3/4 inputs not durable (per-period returns only in gitignored bulk reports) | Every registered run writes `docs/reports/research/trial_returns/trial-N.json` (committed); trials 1–3 backfilled |
| 7 | **Gate 5 spend-run metrics blended in-sample with holdout** — the single-use verdict could be masked | Spend runs additionally register isolated `holdout_*` segment metrics (fixed BEFORE any spend occurred) |
| 8 | Holdout anchor trusted unvalidated data (a future-dated candle would disable Gate 5 forever) | Runner refuses `data_end > recorded_at` |
| 9 | DSR units trap: registry stores annualized Sharpe; DSR needs per-period variance (feeding it unconverted inflates SR* ×365) | `non_annualized_sharpe_variance()` adapter; DSR's 1e-12 clamp replaced with a loud raise; skew/kurtosis unified to population moments (restores the γ4 ≥ 1+γ3² bound) |
| 10 | Replay against the live store would inflate the gate's paper-day counter | `--replay-smoke` refuses the live store path; `--once` uses fsync + a single-instance lock (concurrent scheduled+manual runs cannot double-write) |
| 11 | Torn final store line (power loss) bricked runtime and dashboard | Torn tail quarantined to a `.torn` sidecar; any other corruption still fails loudly |
| 12 | Misc.: notification key not canonical (0.50 vs 0.5); missed days invisible; stale halts unpersisted; universe shrink with restored positions → raw KeyError; contract typos (C(16,8)=12,870, warmup reason-code row); cmd cd-failure guard | All fixed; MISSED_DAYS health events; stale-halt rejections persisted; clear universe error; docs corrected |

Re-verification: ruff / ruff format / mypy --strict / lint-imports (13 contracts) /
**264 tests** all green; registered trial 4 (post-fix code) vs fresh runtime
replay: identical to the last decimal digit.

Post-fix registered results (trial 4, 2018-03-04 → 2025-07-01, after costs):
final equity 14,221.28 (+1,322%), Sharpe 1.023, MDD 51.9%, 932 trades,
turnover ≈ 17.7×/yr, benchmark 5,976. The 135 recorded rejections are mostly
dust-remainder retries that the old code absorbed silently — now visible.

## Accepted / deferred (documented, not fixed)

- **Stale-status push notification** (GOALS L wording): stale halts are
  persisted (health + rejection events, dashboard-visible) but not pushed on
  no-change days; NotificationEvent cannot represent status messages. Backlog.
- **Gate 3/4 orchestration CLI**: PBO/DSR are implemented and unit-verified but
  nothing yet assembles the registry-wide matrix; required before Goal O
  sign-off. Backlog (inputs are now durable).
- **Post-hoc trial registration window**: a crash between engine completion and
  registry append loses that row; results are also lost (nothing printed), so
  nothing survives to cherry-pick. Single-operator accepted risk.
- **Delta-trader semantics**: fractions are sized at each change's open equity;
  positions drift with price between changes (no maintenance rebalancing).
  This is the documented design; the strategy contract's "maps ... to target
  weights" wording reads more continuous than the implementation. Clarify at
  the next contract revision.
- **Missed-day pending fills** execute at the latest candle's open (up to the
  gap length late) rather than the decision's true next bar; recorded via
  MISSED_DAYS. Keep the machine on.
- **Registry/store are single-process files** (no cross-process locks beyond
  the --once lock); AGENTS §10's PostgreSQL runtime store remains future work.
- **Benchmark is frictionless** buy-and-hold (slightly flatters the benchmark);
  two MDD definitions (ledger peak incl. open marks vs close-only curve) can
  differ on gap days; registered data_span excludes the 200-day warmup.

## Live-clock status at audit close

- Scheduled task `CryptoQuantDailySignalCycle` registered by the operator,
  next run 08:05 Asia/Taipei; runs the hardened `--once` path.
- Live store: 1 clean cycle (2026-07-02), first real notification emitted
  (ETHUSDT ladder 0 → 0.25 @ 1700.57); legacy-format restore verified.
- Trial registry N=4; holdout locked (2025-07-02), unspent; durable returns
  present for all trials.
