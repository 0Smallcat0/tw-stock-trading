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

## Status

Goal TW-A (bootstrap) done: crypto-specific code removed, TW identity and
contracts in place, baseline green. The TWSE/FinMind data layer, trading
calendar, and corporate-action adjustment pipeline land in Goals TW-B/TW-C.

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
