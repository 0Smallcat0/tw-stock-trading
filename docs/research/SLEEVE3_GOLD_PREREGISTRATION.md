# Sleeve 3 — the same untuned rule on gold — pre-registration

Written 2026-07-26, **before the backtest was run**, and frozen on commit.
Nothing below may be edited afterwards; the result goes in a separate file.

## Why this test exists

`docs/research/CROSSMARKET_COMBINATION_RESULT.md` (crypto repo) measured the
only positive result this program has produced that does not rest on a
parameter choice: the *same* untuned mid-channel Donchian rule, run in two
independent markets and combined at fixed 50/50 weights, scored a higher
Sharpe (1.3437) at a lower drawdown (19.73%) than either sleeve alone, with
daily correlation −0.0041 that went *more* negative (−0.1818) in the crypto
sleeve's worst 5% of days.

That result also named its own weakness: **two sleeves is not
diversification, it is the minimum viable version of it.** This test adds a
third.

It also named the mechanism, and that naming is binding here:

> The Taiwan sleeve is **not a hedge against crypto**. It is a trend system
> that is frequently in cash — and *cash is uncorrelated with everything*.
> […] Any future sleeve added on this reasoning must be a system that also
> goes to cash.

## Why gold, chosen before any number was seen

The sleeve must be (a) a market where a long-only daily trend system can be
in cash, (b) as structurally unlinked as possible from *both* existing
sleeves, and (c) available as a spot, long-only, unlevered instrument, which
is permanent product law here.

**GLD (SPDR Gold Shares)** is the choice. Gold is the asset class with the
least structural overlap with either crypto or Taiwan equities. Rejected in
advance, with reasons, so this cannot be reconstructed after the fact:

| Candidate | Rejected because |
|---|---|
| SPY / US equities | Correlates with 0050 through global equity beta; 0050 is ~half semiconductor, whose demand cycle is a US-listed one. It would add a fourth correlated leg, not an independent one. |
| TLT / long bonds | The available history is dominated by one 40-year rate regime that ended in 2022. A trend system's result there is a statement about that regime, not about the rule. |
| A second crypto asset | Not independent by construction. |

### Market-shopping guard (binding)

If GLD fails the criteria below, **that is the recorded result.** Trying
SLV, USO, DBC, or any other market afterwards and reporting the winner is
exactly the selection procedure that PBO 0.7411 already indicted in this
program. Any further market is a separate numbered pre-registration, run
after this one is recorded, and **every one of them is reported** — not
only the survivor.

## The configuration — nothing here is chosen

Copied verbatim from crypto trial 88 and this repository's trial 23. No
grid, no arms, no per-market fitting. One run.

| Field | Value | Source |
|---|---|---|
| strategy | `donchian_breakout_ensemble` | crypto trial 88 |
| `dc_windows` | 10, 20, 55, 110 | crypto trial 88 |
| `dc_exit` | `mid_channel` | crypto trial 88 |
| symbol | `GLD` | this pre-registration |
| risk budget | 1.0 (single symbol) | matches both existing sleeves |

## Cost model, declared in advance

US ETF costs are structurally lower than Taiwan's, so the numbers are set
**deliberately harsher than reality** rather than flattering:

| Field | Value | Why |
|---|---|---|
| commission | 5 bps/side | US retail ETF commission has been $0 since 2019; 5 bps is a penalty, not an estimate |
| slippage | 5 bps/side | GLD's quoted spread is ~1 bp on multi-billion daily notional |
| sell tax (ETF/stock) | 0 bps | there is no US securities transaction tax; the SEC fee is ~0.008 bps |
| min fee | 0 | no per-ticket minimum |
| quantity step | 1 share | US ETFs trade in single shares |
| min notional | 0 | no lot minimum to clear |
| initial cash | 100,000 USD | same scoreboard scale as both existing sleeves |
| price tick enforcement | off | same as the 0050 cross-market run |

Round-trip cost burden is therefore 20 bps, against a real-world figure
closer to 2 bps.

## Risk brakes are set so they cannot bind, and this is checked

The disaster brakes in this engine are calibrated to Taiwan's ±10% daily
price limit, which does not exist in US markets. Rather than re-tune them
(a tuning surface this test refuses to open), they are set wider than any
move in GLD's history, so the measured result is the raw rule:

| Field | Value |
|---|---|
| `max_drawdown_fraction` | 0.60 |
| `daily_loss_pause_fraction` | 0.20 |
| `disaster_single_day_drop_fraction` | 0.20 |
| `disaster_multi_session_count` / `_drop_fraction` | 3 / 0.30 |
| `stale_data_max_age_seconds` | 345600 (as configured) |

**Verification, required before the result may be reported:** the reported
`max_drawdown_fraction` must be below 0.60 and no single day in the equity
curve may fall more than 20%. If either binds, the brake influenced the
result, **the run is VOID**, and it must be re-declared with brakes derived
from published US market structure — not re-declared with whatever number
makes the result look better.

## Pre-declared criteria

The sleeve's own window is 2004-11-18 → 2026-07-23 (5,452 sessions). The
combination is evaluated on the window **all three sleeves cover**, which
the crypto sleeve's start truncates to 2018-03-06 onward.

1. **The gold sleeve is individually positive.** Annualized Sharpe > 0 over
   its own full window, and > 0 over the three-way common window.
2. **Independence.** |daily return correlation| < 0.30 against the crypto
   sleeve and < 0.30 against the Taiwan sleeve, on the common window.
3. **The three-sleeve combination beats the two-sleeve one.** Equal weights
   (1/3 each), monthly rebalanced, evaluated on the common window: combined
   Sharpe strictly greater than the 50/50 crypto+Taiwan combination's Sharpe
   **recomputed on that same window**, not against the 1.3437 headline from
   a different window.
4. **Drawdown does not get worse.** Three-sleeve combined max drawdown
   strictly lower than the two-sleeve combination's on the same window.

**PASS** requires all four. Anything less is a registered negative and gets
written up as one, in full, with the numbers that failed.

## Costs and limits, stated before the result

- **This registers a trial.** The run appends to this repository's
  `docs/reports/research/trial_registry.jsonl`, taking N from 23 to 24. It
  does not touch the crypto program's N=133.
- **The repository is a host, not a claim.** GLD is not a Taiwan stock. It
  lives here because the ported daily-bar engine does, and pricing sleeve 3
  with a *different* engine than sleeve 2 would quietly weaken the "same
  rule" claim this whole result depends on.
- **FX is still not modeled**, now across three currencies (USDT, TWD, USD).
  These remain local-currency combinations, not an achievable portfolio
  return. Sleeve 3 makes this worse, not better, and it is not fixed here.
- **No DSR, no gate verdict for the combination.** As with the two-sleeve
  result, the portfolio-level analysis registers nothing and has no
  deflation statistic of its own.
- **Never nominatable.** Neither this trial nor the three-sleeve combination
  may be nominated for the crypto program's single-use October holdout;
  `docs/contracts/PRE_HOLDOUT_PROTOCOL.md` nominations are fixed and this
  does not reopen them.
- **A longer common window would be better and does not exist.** The
  three-way overlap is bounded by the crypto sleeve's 2018 start. The gold
  sleeve's own 21-year history is reported for context, not for the
  combination claim.

## Data provenance

`data/candles/GLD_1d.jsonl`, written 2026-07-26 by
`scripts/ingest_us_etf_ohlcv.py` only after every gate passed:

| Gate | Result |
|---|---|
| bars | 5,452 (floor 1,000) |
| window | 2004-11-18 → 2026-07-23 |
| second provider confirmed | 5,452 of 5,452 sessions (ratio 1.000000) |
| worst close disagreement | 0.00000006 (2006-04-28) |
| sessions over 0.5% disagreement | 0 |
| gaps > 10 days | 0 |
| distribution bars (adjusted ≠ raw) | 0, on both providers |

Series of record is Yahoo's consolidated chart feed; FinMind's
`USStockPrice` is the independent confirmer. Yahoo returned a holed row for
2026-07-24 (null close) and that session was dropped rather than patched —
which is the entire reason two providers are fetched.

Because GLD makes no distributions, its raw close series *is* its
total-return series; both providers confirm adjusted close equals raw close
on all 5,452 bars, and the ingestion fails loudly if that ever stops being
true.
