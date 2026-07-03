"""Read-only dashboard API over the runtime event store and gate artifacts.

Every route is GET. There is no order endpoint, no risk-limit mutation, and no
private data anywhere in this process — permanently, by product definition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.api.page import DASHBOARD_HTML

# Import the store submodule directly: pulling in the src.runtime package
# facade would drag the whole engine (and its business-package imports) into
# the API's dependency graph, breaking the read-only presentation contract.
from src.runtime.store import JsonlEventStore


def create_dashboard_app(
    *,
    store_path: str | Path,
    trial_registry_path: str | Path,
    holdout_lock_path: str | Path,
    risk_budgets: dict[str, str],
) -> FastAPI:
    """Build the read-only dashboard app bound to artifact paths."""

    app = FastAPI(
        title="Crypto Quant Signal MVP - read-only dashboard",
        description="Advisory signal state and paper scoreboard. No trading endpoints exist.",
    )

    def _store() -> JsonlEventStore:
        return JsonlEventStore(store_path)

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page() -> str:
        return DASHBOARD_HTML

    @app.get("/api/signals/current")
    def current_signals() -> dict[str, Any]:
        store = _store()
        latest: dict[str, dict[str, Any]] = {}
        for event in store.events_of_kind("signal"):
            payload = dict(event.payload)
            symbol = str(payload.get("symbol", ""))
            latest[symbol] = payload
        return {
            "risk_budgets": risk_budgets,
            "signals": [latest[symbol] for symbol in sorted(latest)],
        }

    @app.get("/api/notifications")
    def notifications(limit: int = 50) -> dict[str, Any]:
        store = _store()
        events = store.events_of_kind("notification")
        return {
            "count": len(events),
            "notifications": [dict(event.payload) for event in events[-limit:]][::-1],
        }

    @app.get("/api/account")
    def account() -> dict[str, Any]:
        store = _store()
        cycle = store.latest_of_kind("cycle")
        if cycle is None:
            return {"status": "NO_CYCLES_YET"}
        payload = dict(cycle.payload)
        return {
            "status": "OK",
            "close_time": payload.get("close_time"),
            "account": payload.get("account"),
            "executed_fractions": payload.get("executed_fractions"),
            "decision_fractions": payload.get("decision_fractions"),
        }

    @app.get("/api/equity")
    def equity(limit: int = 400) -> dict[str, Any]:
        store = _store()
        points = []
        for event in store.events_of_kind("cycle")[-limit:]:
            payload = dict(event.payload)
            account_payload = payload.get("account")
            if not isinstance(account_payload, dict):
                continue
            points.append(
                {
                    "close_time": payload.get("close_time"),
                    "equity": account_payload.get("equity"),
                    "drawdown": account_payload.get("drawdown"),
                }
            )
        return {"points": points}

    @app.get("/api/orders")
    def orders(limit: int = 100) -> dict[str, Any]:
        store = _store()
        events = store.events_of_kind("order")
        return {
            "count": len(events),
            "orders": [{"key": event.key, **dict(event.payload)} for event in events[-limit:]][
                ::-1
            ],
        }

    @app.get("/api/fills")
    def fills(limit: int = 100) -> dict[str, Any]:
        store = _store()
        events = store.events_of_kind("fill")
        return {
            "count": len(events),
            "fills": [{"key": event.key, **dict(event.payload)} for event in events[-limit:]][::-1],
        }

    @app.get("/api/rejections")
    def rejections(limit: int = 100) -> dict[str, Any]:
        store = _store()
        events = store.events_of_kind("rejection")
        return {
            "count": len(events),
            "rejections": [dict(event.payload) for event in events[-limit:]][::-1],
        }

    @app.get("/api/risk")
    def risk() -> dict[str, Any]:
        store = _store()
        events = store.events_of_kind("risk_event")
        health = store.events_of_kind("health")
        return {
            "risk_events": [dict(event.payload) for event in events[-50:]][::-1],
            "health": [
                {"recorded_at": event.recorded_at.isoformat(), **dict(event.payload)}
                for event in health[-50:]
            ][::-1],
        }

    @app.get("/api/gate")
    def gate() -> dict[str, Any]:
        return {
            "registered_trials_n": _count_jsonl_lines(trial_registry_path),
            "holdout": _read_json_or_none(holdout_lock_path),
            "thresholds": {
                "pbo_max": "0.05",
                "dsr_min": "0.95",
                "min_daily_observations": 1000,
                "paper_trading_months_min": 3,
            },
        }

    @app.get("/api/health")
    def health_check() -> dict[str, Any]:
        store = _store()
        cycle = store.latest_of_kind("cycle")
        return {
            "status": "OK",
            "last_cycle_close": (str(dict(cycle.payload).get("close_time")) if cycle else None),
            "store_path": str(store.path),
            "read_only": True,
        }

    return app


def _count_jsonl_lines(path: str | Path) -> int:
    file_path = Path(path)
    if not file_path.exists():
        return 0
    return sum(1 for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_json_or_none(path: str | Path) -> dict[str, Any] | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        loaded = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None
