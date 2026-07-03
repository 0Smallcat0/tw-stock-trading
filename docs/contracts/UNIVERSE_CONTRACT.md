# Universe Contract (TW Edition)

Status: TW Core MVP contract (decision universe pinned by research)

## Purpose

The TW Core MVP universe defines which Taiwan-listed public securities are
eligible to reach the feature and strategy layers.

It is an input-safety contract, not a research or optimizer contract.

## Decision Universe (2026-07-03, evidence-pinned)

Eligibility rules below define which symbols COULD be traded safely. The
DECISION universe — which symbols the active strategy actually decides on —
is pinned by verified research (`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md`):

```text
Core: 0050 元大台灣50 (100% risk budget)
Execution alternative (documentation only, not a second signal asset):
  006208 富邦台50 — same index, lower fee; the signal is identical
Excluded by evidence: 2330 single-stock timing
  (1) TW firm-level evidence: MA profitability is WEAKEST in mature mega-caps
  (2) round-trip cost ~0.47% (0.3% sell tax) + ~20bp tick at NT$2,000+
  (3) limit-day queue risk is worst in the single stock (2025-04-07 precedent)
Excluded: broad stock universes (survivorship bias; the crypto lesson stands)
```

Adding ANY second signal asset (including 2330 or a bond/overseas ETF sleeve)
requires a pre-registered experiment with survivorship-safe data and its own
gate pass. Decision candles are DAILY (Taipei close).

## Source Boundary

Allowed sources:

- TWSE public endpoints (RWD after-trading, OpenAPI) — keyless
- FinMind public API — anonymous tier; optional free token raises the quota
  and is a data token, never an account credential

Forbidden sources:

- broker APIs of any kind (Shioaji, Fugle, etc.) — permanently
- private/authenticated market data requiring an account
- account balances, order history, real order endpoints
- paid data subscriptions

## Eligibility Rules

A symbol is eligible only when all rules are true:

- listed on TWSE (TPEx/OTC is out of scope for Core MVP)
- instrument type is a domestic equity ETF or a common stock
- trading status is normal: not suspended, not full-delivery (全額交割),
  not under disposition measures (處置); attention listing (注意) is
  disclosure-only and does not exclude
- price limit regime is the standard ±10% band
- at least `250` daily closes of local history
- 20-day median daily traded value at least `100,000,000` TWD

## Ranking Rule

Eligible symbols are ranked by 20-day median traded value descending.
Ties break by security code ascending for deterministic output.

## Output

Universe snapshots contain:

- eligible symbols
- UTC creation time
- public data source name

The snapshot must not contain private account data, order data, strategy
scores, target weights, or execution actions.

## Core MVP Boundary

This contract does not authorize:

- research lab candidate search
- ML, HMM, GA, or optimizer logic
- broker API use
- real orders
- margin, leverage, derivatives, or short exposure
