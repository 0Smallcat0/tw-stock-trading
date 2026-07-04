"""Resumable, throttled backfill of the survivorship-mitigated common-stock universe.

Per TW4 pre-registration (docs/research/TW4_LOWVOL_PREREGISTRATION.md):
  master list = TaiwanStockInfo(twse|tpex, 4-digit common) UNION TaiwanStockDelisting
  per stock  = full-life TaiwanStockPrice (date, open, close, Trading_money)

Keyless anonymous FinMind (300 req/hr). We throttle to stay well under the cap
and back off hard on any error. Each stock is written to its own file; a stock
that already has a file (even empty) is skipped, so the job is fully resumable
and can be killed/restarted at will.

Usage:  python -m scripts.backfill_universe            # run to completion
        python -m scripts.backfill_universe --status   # print progress and exit
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "https://api.finmindtrade.com/api/v4/data"
OUT_DIR = Path("data/universe")
MASTER_FILE = OUT_DIR / "_master_list.json"
LOG_FILE = OUT_DIR / "_progress.log"

START_DATE = "2004-01-01"  # 252d lookback before the 2005-01 backtest start
END_DATE = "2026-07-04"

SLEEP_OK = 13.5  # seconds between successful calls  -> ~266/hr, under the 300 cap
BACKOFF = 300.0  # seconds to wait after an error (soft-ban penalty box)
MAX_RETRY = 4


def _log(message: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _get(params: dict[str, str]) -> dict:
    resp = httpx.get(BASE, params=params, timeout=90.0)
    resp.raise_for_status()
    return resp.json()


def build_master_list(client_refresh: bool = False) -> list[str]:
    if MASTER_FILE.exists() and not client_refresh:
        return json.loads(MASTER_FILE.read_text(encoding="utf-8"))
    info = _get({"dataset": "TaiwanStockInfo"})["data"]
    time.sleep(SLEEP_OK)
    delist = _get({"dataset": "TaiwanStockDelisting"})["data"]

    def is_common(code: str) -> bool:
        return len(code) == 4 and code[0] in "123456789" and code.isdigit()

    ids: set[str] = set()
    for row in info:
        code = str(row.get("stock_id", ""))
        if is_common(code) and str(row.get("type")) in ("twse", "tpex"):
            ids.add(code)
    for row in delist:
        code = str(row.get("stock_id", ""))
        if is_common(code):
            ids.add(code)
    ordered = sorted(ids)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MASTER_FILE.write_text(json.dumps(ordered), encoding="utf-8")
    _log(f"master list built: {len(ordered)} common stock ids")
    return ordered


def _stock_file(code: str) -> Path:
    return OUT_DIR / f"{code}.jsonl"


def fetch_stock(code: str) -> int:
    """Fetch one stock's full life; write minimal rows. Returns row count."""

    payload = _get(
        {
            "dataset": "TaiwanStockPrice",
            "data_id": code,
            "start_date": START_DATE,
            "end_date": END_DATE,
        }
    )
    if payload.get("status") != 200:
        msg = f"{code}: status={payload.get('status')} msg={payload.get('msg')}"
        raise RuntimeError(msg)
    rows = payload.get("data", [])
    out = _stock_file(code)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "d": str(row.get("date")),
                        "o": row.get("open"),
                        "c": row.get("close"),
                        "m": row.get("Trading_money"),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    return len(rows)


def status() -> None:
    ids = build_master_list()
    done = sum(1 for code in ids if _stock_file(code).exists())
    print(f"universe: {len(ids)} ids | fetched: {done} | remaining: {len(ids) - done}")


def main() -> None:
    if "--status" in sys.argv:
        status()
        return
    ids = build_master_list()
    total = len(ids)
    todo = [code for code in ids if not _stock_file(code).exists()]
    _log(f"backfill start: {len(todo)}/{total} remaining")
    fetched = 0
    for index, code in enumerate(todo, start=1):
        for attempt in range(1, MAX_RETRY + 1):
            try:
                count = fetch_stock(code)
                fetched += 1
                if index % 25 == 0 or count == 0:
                    _log(f"{index}/{len(todo)} {code}: {count} rows (session fetched={fetched})")
                time.sleep(SLEEP_OK)
                break
            except (httpx.HTTPError, RuntimeError) as exc:
                if attempt == MAX_RETRY:
                    _log(f"{code}: GIVE UP after {MAX_RETRY} tries ({exc}); leaving unfetched")
                else:
                    wait = BACKOFF * attempt
                    _log(f"{code}: error ({exc}); backoff {wait:.0f}s (try {attempt}/{MAX_RETRY})")
                    time.sleep(wait)
    _log(f"backfill DONE this session: fetched {fetched}; run --status to see totals")


if __name__ == "__main__":
    main()
