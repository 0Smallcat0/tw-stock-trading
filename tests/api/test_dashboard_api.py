from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.api import create_dashboard_app
from src.runtime import JsonlEventStore

_RECORDED_AT = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)


def _seed_store(store_path: Path) -> None:
    store = JsonlEventStore(store_path)
    store.append(
        kind="signal",
        key="signal:BTCUSDT:2026-07-01",
        recorded_at=_RECORDED_AT,
        payload={
            "symbol": "BTCUSDT",
            "as_of": "2026-07-01T23:59:59.999000+00:00",
            "exposure_fraction": "0.5",
            "reason_codes": ["ABOVE_SMA_20", "LADDER_UP"],
        },
    )
    store.append(
        kind="signal",
        key="signal:BTCUSDT:2026-07-02",
        recorded_at=_RECORDED_AT,
        payload={
            "symbol": "BTCUSDT",
            "as_of": "2026-07-02T23:59:59.999000+00:00",
            "exposure_fraction": "0.75",
            "reason_codes": ["ABOVE_SMA_20", "LADDER_UP"],
        },
    )
    store.append(
        kind="notification",
        key="notify:paper-runtime:BTCUSDT:2026-07-02:0.5->0.75",
        recorded_at=_RECORDED_AT,
        payload={
            "notification_id": "notify:paper-runtime:BTCUSDT:2026-07-02:0.5->0.75",
            "symbol": "BTCUSDT",
            "action": "INCREASE_EXPOSURE",
            "target_fraction": "0.75",
            "delta_fraction": "0.25",
            "decision_price": "50000",
            "decision_time": "2026-07-02T23:59:59.999000+00:00",
            "risk_status": "OK",
        },
    )
    store.append(
        kind="cycle",
        key="cycle:2026-07-02",
        recorded_at=_RECORDED_AT,
        payload={
            "close_time": "2026-07-02T23:59:59.999000+00:00",
            "start_of_day_equity": "1000",
            "executed_fractions": {"BTCUSDT": "0.5", "ETHUSDT": "0"},
            "decision_fractions": {"BTCUSDT": "0.75", "ETHUSDT": "0"},
            "account": {
                "account_id": "paper-main",
                "cash": "750",
                "realized_pnl": "0",
                "unrealized_pnl": "5",
                "equity": "1005",
                "peak_equity": "1005",
                "drawdown": "0",
                "updated_at": "2026-07-02T23:59:59.999000+00:00",
                "positions": [
                    {
                        "symbol": "BTCUSDT",
                        "base_asset": "BTC",
                        "quote_asset": "USDT",
                        "quantity": "0.005",
                        "average_entry_price": "50000",
                        "cost_basis": "250",
                    }
                ],
            },
        },
    )
    store.append(
        kind="rejection",
        key="rejection:ETHUSDT:2026-07-02",
        recorded_at=_RECORDED_AT,
        payload={"symbol": "ETHUSDT", "reason_codes": ["STALE_DATA_HALT"]},
    )
    store.append(
        kind="health",
        key="health:STALE_DATA_HALT:2026-07-02",
        recorded_at=_RECORDED_AT,
        payload={"code": "STALE_DATA_HALT"},
    )


def _client(tmp_path: Path) -> TestClient:
    store_path = tmp_path / "events.jsonl"
    _seed_store(store_path)
    registry_path = tmp_path / "registry.jsonl"
    registry_path.write_text('{"trial_id": 1}\n{"trial_id": 2}\n', encoding="utf-8")
    holdout_path = tmp_path / "holdout.json"
    holdout_path.write_text(
        json.dumps(
            {
                "holdout_start": "2025-07-02T00:00:00+00:00",
                "locked_at": "2026-07-02T00:00:00+00:00",
                "spent": False,
                "spent_at": None,
            }
        ),
        encoding="utf-8",
    )
    app = create_dashboard_app(
        store_path=store_path,
        trial_registry_path=registry_path,
        holdout_lock_path=holdout_path,
        risk_budgets={"BTCUSDT": "0.5", "ETHUSDT": "0.5"},
    )
    return TestClient(app)


def test_dashboard_page_serves_static_html(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "read-only" in response.text
    assert "/api/signals/current" in response.text


def test_current_signals_return_latest_per_symbol(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/signals/current")

    assert response.status_code == 200
    body = response.json()
    assert body["risk_budgets"] == {"BTCUSDT": "0.5", "ETHUSDT": "0.5"}
    (signal,) = body["signals"]
    assert signal["symbol"] == "BTCUSDT"
    assert signal["exposure_fraction"] == "0.75"


def test_account_view_exposes_the_scoreboard(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/account")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["account"]["equity"] == "1005"
    assert body["account"]["positions"][0]["symbol"] == "BTCUSDT"
    assert body["decision_fractions"]["BTCUSDT"] == "0.75"


def test_notifications_rejections_and_gate_views(tmp_path: Path) -> None:
    client = _client(tmp_path)

    notifications = client.get("/api/notifications").json()
    assert notifications["count"] == 1
    assert notifications["notifications"][0]["action"] == "INCREASE_EXPOSURE"

    rejections = client.get("/api/rejections").json()
    assert rejections["rejections"][0]["reason_codes"] == ["STALE_DATA_HALT"]

    risk = client.get("/api/risk").json()
    assert risk["health"][0]["code"] == "STALE_DATA_HALT"

    gate = client.get("/api/gate").json()
    assert gate["registered_trials_n"] == 2
    assert gate["holdout"]["spent"] is False
    assert gate["thresholds"]["pbo_max"] == "0.05"
    assert gate["paper_trading"]["cycles"] == 1
    assert gate["paper_trading"]["started"] is not None
    assert gate["paper_trading"]["days"] >= 0


def test_empty_store_yields_graceful_defaults(tmp_path: Path) -> None:
    app = create_dashboard_app(
        store_path=tmp_path / "missing.jsonl",
        trial_registry_path=tmp_path / "missing-registry.jsonl",
        holdout_lock_path=tmp_path / "missing-holdout.json",
        risk_budgets={"BTCUSDT": "0.5"},
    )
    client = TestClient(app)

    assert client.get("/api/account").json()["status"] == "NO_CYCLES_YET"
    assert client.get("/api/gate").json()["registered_trials_n"] == 0
    assert client.get("/api/health").json()["read_only"] is True


def test_api_has_no_mutating_routes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    for route in client.app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute):
            assert route.methods <= {"GET", "HEAD"}, route.path

    assert client.post("/api/orders").status_code == 405
    assert client.put("/api/account", json={}).status_code == 405
    assert client.delete("/api/notifications").status_code == 405
