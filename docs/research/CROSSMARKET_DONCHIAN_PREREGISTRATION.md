# Cross-market validation — Donchian+ATR from the crypto program, unchanged

Status: **FROZEN on commit**. Written 2026-07-26, before any engine work
in this repository and before any run.

## What this is

The sibling crypto program (`D:\Crypto-Trading`, public repo
`crypto-quant-signal`) searched 133 registered trials across ten
pre-registered families. Its best risk-compliant configuration is
**trial 118**: a Donchian breakout ensemble with an ATR-scaled channel
exit. On crypto pre-holdout data it scored Sharpe 1.2411, max drawdown
33.24%, and cleared that program's deflation gate (DSR 0.9505 at N=125,
0.9501 at N=133).

That program also measured, on 2026-07-26, that in-sample ranking among
its own strategies does not generalize (distinct-family PBO 0.7411).
Every candidate there shares one market's beta. **The single strongest
test available to either program is therefore whether the configuration
survives in a market it has never seen.**

This is that test. It is a validation, not a search.

## Binding design rules

1. **One configuration. Zero free parameters.** Every number is copied
   from crypto trial 118 and may not be adjusted for Taiwan: channel
   windows 10/20/55/110, exit `atr_channel`, ATR window 14, ATR
   multiple 2, equal weighting, next-bar-open execution.
2. **No grid, no selection, no re-tuning.** If the result is poor, the
   recorded conclusion is "it did not transfer" — not "try 3 ATR".
   Any tuning on Taiwan data voids this test permanently; a tuned
   variant would need its own pre-registration and would no longer be a
   cross-market validation of anything.
3. **Universe: 0050 (adjusted series) only.** 00631L is a 2x leveraged
   ETF and leverage is excluded by product law in both programs.
   Adjusted closes are used because the strategy is a total-return
   strategy and unadjusted prices would fabricate gaps at every
   distribution.
4. **Pre-holdout data only.** This repository's holdout
   (`docs/reports/research/holdout_lock.json`, start 2025-07-03,
   unspent) stays sealed. The test window is everything before it —
   roughly 2003-06-30 → 2025-07-02, about 22 years, which includes the
   2008 crisis and the 2022 drawdown that the crypto window cannot test.

## Success criteria (BOTH required)

Against buy-and-hold 0050 over the identical pre-holdout window:

1. **Annualized Sharpe strictly higher than buy-and-hold's.**
2. **Maximum drawdown strictly lower than buy-and-hold's.**

The product claim being tested is precisely "trend-following with a
volatility-scaled exit gives a better risk-adjusted path than owning the
index". If it cannot do that in a new market, the crypto result is
market-specific and must be reported that way.

Failing either → recorded as **did not transfer**. There is no partial
credit and no substitute metric.

## Read-outs (non-gating, declared now)

- Sharpe, MDD, turnover, terminal equity for the strategy and for
  buy-and-hold.
- The same figures for crypto trial 88's configuration (mid-channel
  exit) as context only. **Its result may never be substituted for the
  verdict**, which rests on trial 118's configuration alone.
- Per-decade behaviour (2003-2009, 2010-2019, 2020-2025), because a
  22-year window can hide a strategy that only worked in one era.
- DSR at this repository's registry N. Recorded as information, not as a
  criterion: there is no selection being performed here, and the
  deflation that this configuration owes was already paid in the crypto
  registry, where the search actually happened.

## Honesty clauses

- Taiwan market microstructure differs (0.1425% commission, 0.1% ETF
  sell tax, price ticks, no trading on typhoon days). The existing TW
  cost model and calendar are used unchanged; no cost assumption is
  softened to help the result.
- A pass here does not qualify anything in either program. It is
  evidence that a strategy specified entirely by one market's data also
  works in another — which is the kind of evidence neither program can
  manufacture by running more backtests in its own market.
- A failure here is equally informative and will be published in both
  repositories.
