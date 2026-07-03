# Strategy Contract: Daily Trend Ensemble

Status: Core MVP Goal J contract (supersedes `STRATEGY_LARGE_LIQUID_TREND_15.md` as the active strategy)
Evidence basis: `docs/research/SIGNAL_DESIGN_RESEARCH.md` §3.2-3.3 (2026-07-02)

## Purpose

`Daily Trend Ensemble` converts one asset's closed DAILY candles into one auditable
target-exposure decision per day. It is a long-only, time-series trend rule:
trend up → hold, trend down → cash.

It does not submit orders, choose final position size in quote terms, inspect
account state, or bypass risk.

Plain language:

```text
每天收盤看一次：價格站在幾條長短均線上方，就持有幾分之幾。
四條都站上 → 滿額。全部跌破 → 空手。中間就是 25% 一階的階梯。
```

## Why An Ensemble (design rationale, from verified evidence)

- Daily MA trend rules beat buy-and-hold after 10-50bps costs in verified
  2014-2023 samples — but WHICH single lookback is best was chosen in-sample by
  the papers, and the claim "shorter lookbacks dominate" failed adversarial
  verification (1-2).
- The ensemble spreads single-parameter selection risk across four verified-family
  lookbacks and keeps the registered trial count N minimal (one strategy, zero
  tunable lookbacks).

## Inputs

- Per-asset daily closed candles (UTC close), from the public data layer.
- Warmup: no decision until at least `200` daily closes are available.
- All values `Decimal`; all timestamps UTC-aware.

Required derived features (from the feature pipeline, closed candles only):

- `sma_20`, `sma_65`, `sma_150`, `sma_200`: arithmetic means of the last
  N daily closes, computed at close `t` using closes `<= t`.

## Signal Construction (contract-fixed)

Sub-signals at daily close `t`:

```text
s_n = 1 if close_t > sma_n(t) else 0,  for n in {20, 65, 150, 200}
```

Target exposure fraction:

```text
exposure_fraction = (s_20 + s_65 + s_150 + s_200) / 4
                  ∈ {0, 0.25, 0.50, 0.75, 1.00}
```

Boundary rule: `close_t == sma_n(t)` counts as NOT above (`s_n = 0`).
Conservative by construction.

The four lookbacks `{20, 65, 150, 200}` are FIXED BY THIS CONTRACT and uniform
across all assets. Changing any lookback, adding a lookback, or per-asset tuning
is a new strategy variant: it requires pre-registration in the trial registry and
a new or amended contract. This is an anti-overfitting rule, not a style choice.

## Output

Each decision contains only:

- `symbol`
- `exposure_fraction`: `Decimal` in `{0, 0.25, 0.5, 0.75, 1}`
- `sub_signals`: the four `s_n` states
- `score`: equals `exposure_fraction` (kept for pipeline compatibility)
- `reason_codes`
- `generated_at_bar_close`: the daily close timestamp (UTC)
- `executable_from_next_bar`: strictly after `generated_at_bar_close`

Reason codes:

| Condition | Code |
| --- | --- |
| `close > sma_20` | `ABOVE_SMA_20` else `BELOW_SMA_20` |
| `close > sma_65` | `ABOVE_SMA_65` else `BELOW_SMA_65` |
| `close > sma_150` | `ABOVE_SMA_150` else `BELOW_SMA_150` |
| `close > sma_200` | `ABOVE_SMA_200` else `BELOW_SMA_200` |
| fraction increased vs previous decision | `LADDER_UP` |
| fraction decreased vs previous decision | `LADDER_DOWN` |
| fraction unchanged | `LADDER_HOLD` |

With fewer than 200 daily closes NO decision is produced (see Inputs): the
feature layer emits no snapshot, and the runtime records a
`WARMUP_INSUFFICIENT_HISTORY` health event instead of a strategy decision.

## Ladder-Change Semantics (what triggers a notification)

- A notification event exists only when `exposure_fraction` changes between
  consecutive decisions for the same symbol.
- The event magnitude is the delta (usually 0.25; multi-cross days may produce
  0.50 or more in one step).
- No change → no notification. Long silences are correct behavior.

## Relationship To Other Layers

- The strategy emits a FRACTION of the asset's risk budget, never a quote amount.
- Portfolio maps `exposure_fraction × asset_risk_budget` to target weights
  (Core MVP budgets: BTC 50%, ETH 50%; SOL 0% until it passes the validation gate).
- Risk gate approval is still required for every resulting action; the strategy
  cannot bypass it.
- The paper broker executes approved actions on the scoreboard account; the
  human executes them (or not) at the real exchange, manually.

## Safety Rules

- Long-only: `exposure_fraction` is never negative; SHORT is unrepresentable.
- Decisions use only closed daily candles; still-open candles are blocked upstream.
- A decision generated at close `t` is executable no earlier than the next bar.
- Deterministic: identical candle history → identical decision.
- Public data only; no account, order, or broker state may be read.

## Explicitly Out Of Scope (requires pre-registered research, Goal P)

- Regime filter (e.g., hold ETH/SOL only when BTC > 200d SMA)
- Stop overlays (trailing / volatility-scaled / time stops)
- Donchian-channel ensemble variant
- Any additional asset, lookback, or weighting scheme
