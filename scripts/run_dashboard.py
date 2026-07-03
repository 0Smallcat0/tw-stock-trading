"""Thin CLI for the read-only dashboard (Goal M)."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from src.api import create_dashboard_app
from src.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/runtime/paper_runtime.yaml",
        help="Path to the Core MVP runtime config.",
    )
    parser.add_argument(
        "--store",
        default=None,
        help="Runtime event store path (default: storage config).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Listen port (default: api_dashboard config).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    app = create_dashboard_app(
        store_path=Path(args.store or config.storage.runtime_events_path),
        trial_registry_path=Path(config.storage.trial_registry_path),
        holdout_lock_path=Path(config.storage.holdout_lock_path),
        risk_budgets={
            symbol: str(budget) for symbol, budget in config.portfolio.risk_budgets.items()
        },
        initial_cash=str(config.account.initial_cash),
    )
    uvicorn.run(app, host=config.api_dashboard.host, port=args.port or config.api_dashboard.port)


if __name__ == "__main__":
    main()
