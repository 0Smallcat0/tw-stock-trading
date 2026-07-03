# Core MVP Completion Report (Goal N)

Date: 2026-07-03
Verified by: baseline toolchain + registered real-data runs
Scope: GOALS.md v0.9 Goal N checklist

## Checklist Evidence

| # | Requirement | Evidence |
|---|-------------|----------|
| 1 | Baseline verification passes | `ruff check` clean, `ruff format --check` clean, `mypy --strict src/` clean (53 files), `lint-imports` 13 contracts kept / 0 broken, `pytest -m "not network" tests -q` 252 passed |
| 2 | Daily public data ingested and replayed | Real Binance Spot daily history fetched via paginated public REST: BTCUSDT 3,242 candles, ETHUSDT 3,242, SOLUSDT 2,152 (2017-08-17 → 2026-07-02), zero quality issues; written to `data/candles/*.jsonl` |
| 3 | Ensemble strategy produces deterministic ladder decisions | Unit tests (determinism, ladder set, equality-is-not-above) + 2,676 decision days replayed identically by two independent engines |
| 4 | Portfolio targets respect budgets; risk gate reason codes | Unit tests + trial 2 replay: 3 rejections, every rejection carries reason codes |
| 5 | Paper broker + scoreboard correct | Ledger invariant tests + backtest/runtime cross-check (see below) |
| 6 | Backtest e2e with registry, PBO/DSR tooling, locked holdout | Trials 1-3 registered (`docs/reports/research/trial_registry.jsonl`, N=3); holdout locked at 2025-07-02, unspent; CSCV/PBO + DSR implemented and unit-tested |
| 7 | Runtime replay smoke; restart duplicates nothing | Pass 1: 2,677 cycles, 926 fills, 930 notifications; Pass 2 (restart, same store): 0 new events, 2,876 cycles skipped; store counts unchanged |
| 8 | Notifications persisted, idempotent, dashboard-visible | 930 notification events persisted before delivery; `/api/notifications` serves them |
| 9 | Dashboard shows signals, scoreboard, rejections, gate | TestClient smoke on the real store: current ladder (BTC 1.0 / ETH 0.25 as of 2025-07-01), equity 14,260.09, gate N=3, holdout unspent; no mutating routes (POST/PUT/DELETE all 405) |
| 10 | No private API path, no real order path | Config validators reject the flags; broker rejects real-order parameters; grep-clean; permanent product property |
| 11 | 15m legacy path present, passing, inactive | Large Liquid Trend 15 code + tests intact; not wired into runtime |

## Registered Real-Data Results (pre-holdout: 2018-03-04 → 2025-07-01)

BTC+ETH 50/50 risk budgets, 1000 USDT start, fees 10bps + slippage 5bps per side,
fills at next daily open. The final 365 days (2025-07-02 → 2026-07-02) are locked
as the single-use holdout and were touched by no trial.

| Trial | Config | Final equity | Ann. Sharpe | Max DD | Trades | vs B&H (5,976) |
|-------|--------|--------------|-------------|--------|--------|-----------------|
| 1 | 15m-era risk pauses (DD pause 20%) | 813.70 (-18.6%) | -0.375 | 20.5% | 43 (3,822 rejections) | strategy locked out after first bear |
| 2 | Research-aligned pauses (DD 65%, daily 10%) | **14,260.20 (+1,326%)** | **1.02** | **52.5%** | 926 | **2.39x benchmark** |
| 3 | Trial 2 at 2x costs (20bps+10bps) | 11,728.38 | 0.96 | 54.7% | 926 | 1.96x benchmark; edge degrades gracefully, does not flip |

Cross-validation: the runtime engine (independent implementation, same rules)
replayed the identical period to final equity 14,260.09 vs the backtest's
14,260.20 — a 0.001% difference from end-of-series bookkeeping.

Trial 1 is retained deliberately: it documents that the v0.8 drawdown-pause
default structurally conflicts with a strategy whose verified research expects
50-60% drawdowns, and that the risk-threshold change in trial 2 was made as a
NEW registered trial, not a silent tweak.

## Honest Caveats

1. **These numbers are in-sample in spirit.** The ensemble lookbacks come from
   published research whose samples overlap this period. The only clean answer
   comes from the locked holdout (Goal O, single use) and forward paper months.
2. **Signal frequency is higher than the research inference.** ~63 ladder
   changes per symbol per year (~1.2/week), not 4-15/year: 25% steps churn when
   price hovers near an SMA. Human-followable but chattier than hoped; any
   hysteresis fix is a pre-registered Goal P experiment (counts toward N).
3. **Costs are assumptions.** 10bps+5bps fits the verified research band, but
   current Binance fee schedules were NOT verified by the research run; the
   Goal O paper months must measure real costs (gate 6).
4. **Benchmark is frictionless** buy-and-hold 50/50 from the first execution
   day; it flatters the benchmark slightly.
5. **Exchange-maintenance candles.** Binance 2018-02-08 daily candles close
   early with millisecond skew across symbols; the runtime aligns by trading
   day. If such a candle ever lands post-warmup, the backtest engine fails
   loudly by design rather than guessing.
6. **DSR/PBO inputs.** With N=3 registered trials the DSR deflation is mild;
   honest accounting requires registering every future variant (the registry
   enforces the record; the discipline stays human).

## What Goal N Does NOT Claim

- No gate PASS: PBO/DSR sign-off, holdout spend, and >=3 paper months belong to
  Goal O and cannot be compressed into a code milestone.
- No profit promise: the gate can only reject bad strategies; the market prices
  the rest.
