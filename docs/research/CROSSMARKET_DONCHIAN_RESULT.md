# Cross-market validation — result: it did not transfer

Executed: 2026-07-26 · Trials **22** (primary) and **23** (declared
context) · Pre-registration:
`docs/research/CROSSMARKET_DONCHIAN_PREREGISTRATION.md` (unmodified)
Window: 2004-04-15 → 2025-07-02 (5,217 sessions, ~21 years), 0050
adjusted series, TW cost model unchanged, holdout untouched.

## Verdict

| | Sharpe | Max drawdown | 100,000 TWD becomes |
|---|---:|---:|---:|
| **Primary — crypto trial 118 config** | **−0.3055** | 40.48% | **66,330** |
| Buy-and-hold 0050 | 0.7159 | 55.75% | 787,034 |
| Context — crypto trial 88 config | 0.4262 | 30.85% | 215,032 |

Pre-declared criteria, both required against buy-and-hold:

1. **Sharpe strictly higher: FAIL** — −0.3055 against 0.7159.
2. MDD strictly lower: PASS — 40.48% against 55.75%.

**Recorded as: DID NOT TRANSFER.** The configuration that is the crypto
program's best risk-compliant candidate lost 34% of capital over
twenty-one years in a market that rose 687%.

No re-tuning was performed and none is permitted under the
pre-registration. The result is what it is.

## It is not a broken port

The signal traded normally throughout: mean exposure 0.384 across 5,218
decision days, all five ladder rungs used (flat on 2,351 days, quarter
649, half 600, three-quarter 309, full 1,309), 165 completed trades. The
strategy was invested more than a third of the time and still lost money
while the index compounded. That is whipsaw, not a dead signal.

The 376-test TW suite passes with the port in place, the existing
`daily_trend_ensemble` path is untouched, and every prior trial in this
registry remains reproducible.

## The mechanism, and why it matters more than the number

The primary and context arms differ in **one parameter**: the exit rule.

- In crypto, swapping mid-channel for a 2×ATR floor moved Sharpe
  **1.1821 → 1.2411** (that swap is exactly what made trial 118 the
  program's best candidate).
- In Taiwan, the same swap moves Sharpe **+0.4262 → −0.3055**.

The crypto-optimal exit is the Taiwan-worst exit. A 2×ATR floor is a
tight stop; in crypto's high-volatility trending regime it re-entered
quickly enough to keep the trend, while on a low-volatility
mean-reverting index it converts every failed breakout into a realized
loss. The parameter did not encode a general truth about trend
following. It encoded a property of crypto.

## What this does and does not establish

**Establishes**: the crypto program's selection of trial 118 over trial
88 — the choice that produced its only gate-4 pass — does not generalize
to a different market. This is direct measured evidence for the same
conclusion its 2026-07-26 PBO diagnostic reached statistically
(distinct-family PBO 0.7411): **selection inside that program does not
transfer.** A cross-market test is stronger evidence than any resampling
of one market's data, and it points the same way.

**Does not establish** that the crypto result is fake inside crypto.
Trial 118's crypto numbers, robustness battery, and gate-4 pass stand as
recorded. What this refutes is any claim that the configuration captures
a market-independent effect.

**Also does not establish** that trend following fails in Taiwan. The
context arm cut maximum drawdown from 55.75% to 30.85% while still
compounding 2.15×, which is a real risk-shape change; it simply does not
beat buy-and-hold on Sharpe, so it fails the same bar. Note this is
consistent with this repository's own earlier FAIL adjudication for its
`daily_trend_ensemble` program: 0050 buy-and-hold is a hard benchmark.

## Honest limits of this test

- One symbol, one market, one configuration. A single ETF is not "the
  Taiwan market".
- The design deliberately forbade adaptation. One could argue any system
  needs its stop scaled to the local volatility regime — and that
  argument is fair. But it cuts both ways: if the parameter must be
  refitted per market, then the crypto fit is a fit, and the crypto
  program's deflation accounting (which only ever paid for search within
  crypto) does not cover it.
- A market-adaptive variant (for example, scaling the ATR multiple by the
  market's own long-run volatility) is a DIFFERENT hypothesis. It would
  need its own pre-registration, and it could not be called a validation
  of anything, because fitting it here is tuning here.

## Provenance

Both trials ran on clean tree `892982c` / `e8c98df` (commit-first rule).
The primary run's helper raised after the trial had already registered;
the trial stands as registered and was not re-run, since re-running would
duplicate the row without adding information. The bug was in the
buy-and-hold reporting helper only and never touched the backtest.
