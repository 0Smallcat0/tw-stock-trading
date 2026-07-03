# Data Adapter Contract: TWSE Public + FinMind Backfill

Status: TW Core MVP contract for Goals TW-B/TW-C (calendar, clients, adjustments)
Evidence basis: endpoint behavior verified by direct fetch on 2026-07-03
(`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md` §資料源; adaptation plan appendix A)

## Purpose

Define how the system obtains, validates, adjusts, and serves Taiwan market
daily data — keyless, public, reproducible, and survivorship-aware.

## Source Boundary

Allowed (all keyless):

- TWSE RWD after-trading endpoints (`https://www.twse.com.tw/rwd/...`)
- TWSE OpenAPI (`https://openapi.twse.com.tw/v1/...`)
- FinMind public API (`https://api.finmindtrade.com/api/v4/data`) —
  anonymous tier (300 req/hr); an optional FREE registered token (600 req/hr)
  is a data token, not an account credential, and must remain optional
- Yahoo/yfinance — cross-check of adjusted RETURNS only (see Anti-Rules)

Forbidden, permanently: broker APIs (Shioaji, Fugle, ...), any authenticated
or paid market data, any account/order endpoint.

## Primary Daily OHLCV: TWSE RWD `STOCK_DAY`

```text
GET {rwd}/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMMDD&stockNo=0050&response=json
```

- Returns the FULL CALENDAR MONTH containing `date`; row format:
  `[ROC date, shares, value TWD, open, high, low, close, change, trades, note]`.
- History floor: `2010-01-04` (earlier dates return a `stat` error message —
  pre-2010 history comes from FinMind backfill).
- Same-day final data ~17:30 Taipei (close price itself final from ~14:00);
  the daily job runs no earlier than 18:00 Taipei.
- Parsing traps (all MUST be handled and tested):
  - ROC dates: `"115/06/01"` and 2-digit years with a LEADING SPACE (` 99/01/04`);
    OpenAPI uses `"1150702"`; TWT49U uses `"114年06月16日"`. Year = ROC + 1911.
  - Comma-grouped number strings; change column may be `"X"` (no comparison).
  - `note == "**"` marks a resumption day after split/par-value change.
  - Volume unit is SHARES on TWSE JSON (TPEx uses 張 = 1,000 shares — out of
    MVP scope but documented to prevent a future unit bug).
- Bulk alternative `MI_INDEX?type=ALLBUT0999` embeds HTML fragments inside
  values; strip before parsing.
- Rate limits are undocumented; community-reported IP bans are real. Rules:
  sequential requests only, >= `min_request_interval_seconds` (config,
  default 3s) between requests, honest User-Agent. A 2010→2026 monthly
  backfill is ~200 requests per symbol (~15 minutes).

## Backfill + Cross-Check: FinMind

```text
GET /api/v4/data?dataset=TaiwanStockPrice&data_id=0050&start_date=...&end_date=...
```

- One call returns 0050's entire life (2003-06-30 → today, ~5,661 rows);
  2330 reaches back to 1994. Suspension days are simply absent. Updates
  ~17:30-17:45 Taipei on trading days. Sunday 00:00-07:00 Taipei: API down.
- Delisted symbols retained → the future survivorship-safe universe source.
- `TaiwanStockPriceAdj` (pre-adjusted prices) is PAYWALLED — irrelevant: this
  system builds its own auditable adjustment factors (below).
- Daily reconciliation rule: for overlapping dates (2010+), FinMind raw close
  must equal TWSE raw close exactly; any mismatch is a data health event and
  blocks strategy input for that symbol-day.

## Corporate Actions → Adjustment Factors (in-house, auditable)

Official sources (all keyless):

```text
TWT49U  {rwd}/rwd/zh/exRight/TWT49U?startDate=..&endDate=..&response=json
        ex-dividend/rights RESULTS since 2003-05-05
        (pre-~2011 rows use a 17-column schema with 權值/息值 split — parse by name)
TWTCAU  {rwd}/rwd/zh/split/TWTCAU?...      ETF split/reverse-split reference prices
        (verified 0050 row: halted 2025-06-11~17, resumed 06-18, 188.65 → 47.16)
TWTAUU  {rwd}/rwd/zh/reducation/TWTAUU?... capital-reduction reference prices
OpenAPI /v1/exchangeReport/TWT48U_ALL      upcoming ex-dividend schedule
        (ETF cash amount can be empty until ~1 week before the ex-date)
FinMind TaiwanStockDividendResult / TaiwanStockSplitPrice /
        TaiwanStockCapitalReductionReferencePrice as JSON mirrors for cross-check
```

Factor rule, per event on its effective date:

```text
factor = 官方參考價 (reference price) / 前收盤價 (prior close)
adjusted series = raw series with factors chain-multiplied backward
```

- The factor table is persisted with event source, date, raw inputs, and the
  computed factor — auditable, append-only, recomputable.
- New events trigger recomputation of the ADJUSTED series only; RAW series
  are immutable facts.
- Golden test cases (mandatory): 0050 2025-06-18 split (÷4 + 5-session halt),
  one 0050 semi-annual cash dividend, one 2330 quarterly dividend, one
  capital-reduction case.

## Dual Price Series (hard rule)

```text
RAW      — official numbers. Used by: accounting, fills, notifications, display.
ADJUSTED — raw x chained factors. Used by: SMA features, strategy decisions,
           backtest returns, benchmark comparisons.
```

Feeding raw closes into the SMA stack is a contract violation: every
ex-dividend day would read as a false trend break.

## Trading Calendar

- Planned schedule: OpenAPI `/v1/holidaySchedule/holidaySchedule` — CURRENT
  YEAR ONLY, ROC dates; entries include settlement-only no-trading days and
  informational rows (e.g. first trading day of the year) that must be
  filtered by their description, not assumed to be closures.
- Make-up workdays (補行上班日): the market is CLOSED (verified 2026-02-20).
- Unscheduled closures (typhoon days) are NOT in the schedule. Detection
  rule: if no data exists for an expected trading day by ~18:30 Taipei,
  reconcile against FinMind `TaiwanStockTradingDate` (updates ~18:00);
  agreement that the market was closed → `UNSCHEDULED_CLOSURE` health event
  (not staleness); disagreement → `DATA_SOURCE_DISAGREEMENT` health event and
  strategy input blocked for the day.
- Suspension tolerance: a symbol absent for multiple consecutive trading
  days while a corporate action is pending (e.g. the 0050 split halt) is a
  `SUSPENSION` state, not a data gap.

## Quality Rules (calendar-aware)

- GAP = a missing TRADING day per the calendar. Weekends, holidays, and
  suspensions are not gaps.
- STALE = the latest close is older than `stale_trading_days` TRADING days
  (wall-clock seconds are forbidden as the staleness unit once the calendar
  module exists — Lunar New Year is 11 calendar days without trading).
- DUPLICATE and OPEN-CANDLE rules carry over unchanged.

## Same-Day Timing Contract

```text
13:30 Taipei  market close
~14:00        close price final (preliminary volume)
~17:30        TWSE RWD volume final (odd-lot, block trades included)
18:00         daily job earliest start (config daily_job_time_taipei)
~18:30        unscheduled-closure / staleness adjudication deadline
next trading day 09:00  earliest execution of yesterday's decision
```

TWSE OpenAPI `STOCK_DAY_ALL` is T+1 (refreshes ~05:20 next morning) and must
NOT be used for same-day signals.

## Anti-Rules

- Never use Yahoo/yfinance as a raw-price, volume, or dividend-amount source:
  for 0050 it back-rescales pre-split history (no split event, halt days
  padded as fake zero-volume bars, pre-split dividends shown ÷4) and its
  volumes differ 3-8% from TWSE. Adjusted-RETURN cross-checks only.
- Never forward-fill through suspensions or holidays for return computation.
- Never ingest through a broker API "because it is easier".
- Never write an adjustment factor without its official source row persisted.
