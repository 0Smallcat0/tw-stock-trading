# TW Stock Signal MVP

A Taiwan-listed-equity, long-only, public-data, DAILY signal notification system.

Every trading day after the Taipei close (data final ~17:30, job at 18:00), the
system tells the user what the 0050 exposure ladder should do and why; the user
executes manually on the next trading day. A `100,000 TWD` virtual account
follows every signal in parallel as the honest scoreboard, recording virtual
decisions, orders, fills, positions, cash, PnL, dividends, rejected orders, and
risk events. The system never submits real orders, never touches a brokerage
account or broker API, and never requires any credential — permanently, by
product definition.

This project is the Taiwan sibling of the crypto signal MVP it was forked from
(same architecture, same six-gate validation discipline). The value proposition
is different and honest: the registered claim is **drawdown reduction, not
return enhancement** — evidence says index-level trend timing should not be
expected to beat 0050 buy-and-hold on CAGR
(`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md`).

Design decisions are grounded in verified research:
`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md` and the adaptation plan
`docs/plans/TW_STOCK_VERSION_PLAN.md`. Work queue: `GOALS.md`.
Qualification standard: `docs/contracts/VALIDATION_GATE_CONTRACT.md`.

## Status — VERDICT: FAIL (2026-07-04); the honest answer is buy-and-hold

TW-A..TW-E complete. The registered in-sample adjudication on 21.2 years of
real 0050 total-return data REJECTED the pre-registered claim: drawdown
protection is real (26.3% vs 55.8%) but the CAGR cost is 5.3pp/yr — beyond
the 3pp tolerance (49 ladder changes/yr of whipsaw). Per the pre-registered
stop rule the runtime was never built. Full numbers:
`docs/reports/TW_QUALIFICATION_VERDICT.md`. The data pipeline, adjustment
factors, validation-gate tooling, and TW cost model remain reusable for any
future pre-registered experiment; the holdout (2025-07+) is still locked and
unspent.

## Local Setup

Use Python 3.12 explicitly. On this machine, `python` may point at a newer interpreter.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements\constraints-dev.txt
```

## Verification

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy --strict src/
.\.venv\Scripts\lint-imports
.\.venv\Scripts\python.exe -m pytest -m "not network" tests -q
docker compose config
```

## Local Database

The local database service uses dummy development credentials only:

- Host port: `54321` (54320 belongs to the crypto sibling project)
- Database: `tw_quant`
- User: `tw`
- Password: `tw_dev_only`

These are local Docker credentials, not production secrets.
