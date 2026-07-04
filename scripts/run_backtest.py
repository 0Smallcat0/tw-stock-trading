"""Thin CLI for registered daily backtests (Goal K)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.backtest import (
    BacktestError,
    BacktestParameters,
    HoldoutViolationError,
    TrialRegistryError,
    config_hash_for,
    run_registered_backtest,
    trial_count,
)
from src.config import config_snapshot, load_config
from src.data import (
    MarketDataValidationError,
    adjusted_candle_file_name,
    candle_file_name,
    read_candles_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/runtime/paper_runtime.yaml",
        help="Path to the Core MVP runtime config.",
    )
    parser.add_argument(
        "--candles-dir",
        default=None,
        help="Directory with <SYMBOL>_1d.jsonl candle files (default: storage config).",
    )
    parser.add_argument("--note", default="", help="Operator note recorded in the trial registry.")
    parser.add_argument(
        "--series",
        choices=("adjusted", "raw"),
        default="adjusted",
        help=(
            "Price series to replay. 'adjusted' (default) is the dividend/split-"
            "adjusted series — total-return-correct signals AND benchmark; "
            "'raw' exists only for diagnostics and is NOT gate-valid."
        ),
    )
    parser.add_argument(
        "--cash-yield-bps",
        default="0",
        help="Annualized idle-cash yield in bps (sensitivity assumption; default 0).",
    )
    parser.add_argument(
        "--cost-stress",
        action="store_true",
        help="Run with doubled commission and slippage assumptions (gate stress rerun).",
    )
    parser.add_argument(
        "--spend-holdout",
        action="store_true",
        help="SINGLE-USE qualification run across the locked holdout (Goal O step 3).",
    )
    parser.add_argument(
        "--i-understand-single-use",
        action="store_true",
        help="Required confirmation flag for --spend-holdout.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.spend_holdout and not args.i_understand_single_use:
        print(
            "refusing: --spend-holdout requires --i-understand-single-use "
            "(the holdout can be used exactly once, ever)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    config = load_config(Path(args.config))
    candles_dir = Path(args.candles_dir or config.storage.candle_files_directory)
    timeframe_value = config.data_source.timeframe

    candles_by_symbol = {}
    for symbol_value in sorted(config.portfolio.risk_budgets):
        name = (
            adjusted_candle_file_name(symbol_value, timeframe_value)
            if args.series == "adjusted"
            else candle_file_name(symbol_value, timeframe_value)
        )
        candles_by_symbol[symbol_value] = read_candles_jsonl(candles_dir / name)

    parameters = BacktestParameters(
        risk_budgets=config.portfolio.risk_budgets,
        initial_cash=config.account.initial_cash,
        account_id=config.account.account_id,
        commission_bps=config.execution.effective_commission_bps,
        min_fee=config.execution.min_fee_twd,
        sell_tax_bps_etf=config.execution.sell_tax_bps_etf,
        sell_tax_bps_stock=config.execution.sell_tax_bps_stock,
        slippage_bps=config.execution.slippage_bps,
        quantity_step=config.execution.quantity_step,
        min_notional_twd=config.risk.min_notional_twd,
        max_drawdown_fraction=config.risk.max_drawdown_fraction,
        daily_loss_pause_fraction=config.risk.daily_loss_pause_fraction,
        disaster_single_day_drop_fraction=config.risk.disaster_single_day_drop_fraction,
        disaster_multi_session_count=config.risk.disaster_multi_session_count,
        disaster_multi_session_drop_fraction=config.risk.disaster_multi_session_drop_fraction,
        stale_data_max_age_seconds=config.risk.stale_data_max_age_seconds,
        cost_multiplier=Decimal("2") if args.cost_stress else Decimal("1"),
        cash_yield_annual_bps=Decimal(args.cash_yield_bps),
        enforce_price_tick=args.series == "raw",
    )

    note = args.note or ""
    series_note = f"series={args.series}"
    operator_note = f"{note} [{series_note}]".strip() if note else series_note
    result = run_registered_backtest(
        candles_by_symbol,
        parameters=parameters,
        config_hash=config_hash_for(config_snapshot(config)),
        code_version=_code_version(),
        registry_path=config.storage.trial_registry_path,
        holdout_path=config.storage.holdout_lock_path,
        reports_directory=config.storage.backtest_reports_directory,
        recorded_at=datetime.now(UTC),
        operator_note=operator_note,
        spend_holdout_single_use=args.spend_holdout,
    )

    print(
        json.dumps(
            {
                "trial_id": result.trial.trial_id,
                "registered_trials_n": trial_count(config.storage.trial_registry_path),
                "data_start": result.report.data_start.isoformat(),
                "data_end": result.report.data_end.isoformat(),
                "holdout_start": result.holdout.holdout_start.isoformat(),
                "holdout_spent": result.holdout.spent,
                "metrics": result.report.to_json_dict()["metrics"],
                "cost_assumptions": dict(result.report.cost_assumptions),
                "report_path": str(result.report_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _code_version() -> str:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return output.stdout.strip() or "unknown"


if __name__ == "__main__":
    try:
        main()
    except (
        BacktestError,
        HoldoutViolationError,
        TrialRegistryError,
        MarketDataValidationError,
    ) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
