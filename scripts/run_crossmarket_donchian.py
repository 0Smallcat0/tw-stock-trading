"""Cross-market validation run (docs/research/CROSSMARKET_DONCHIAN_PREREGISTRATION.md).

Replays the crypto program's trial-118 configuration on 0050, unchanged.
One configuration, no grid, no tuning: the pre-registration forbids
adjusting anything here on Taiwan data.

Also computes buy-and-hold 0050 over the identical decision window, since
the pre-declared criteria are relative to it.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.backtest import BacktestParameters, config_hash_for, run_registered_backtest
from src.config import config_snapshot, load_config
from src.data import adjusted_candle_file_name, read_candles_jsonl

SYMBOL = "0050"
# Copied verbatim from crypto trial 118. Not tunable here.
DC_WINDOWS = (10, 20, 55, 110)
DC_EXIT = "atr_channel"
DC_ATR_WINDOW = 14
DC_ATR_MULTIPLE = Decimal("2")
_DAYS_PER_YEAR = 365


def _code_version() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return f"{head}-dirty" if dirty else head
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _buy_and_hold(closes: list[Decimal]) -> dict[str, str]:
    returns = [
        float(closes[index + 1] / closes[index]) - 1.0
        for index in range(len(closes) - 1)
        if closes[index] > 0
    ]
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.fmean(returns) / stdev * math.sqrt(_DAYS_PER_YEAR) if stdev > 0 else 0.0
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = max(worst, 1.0 - equity / peak)
    return {
        "annualized_sharpe": f"{sharpe:.6f}",
        "max_drawdown_fraction": f"{worst:.6f}",
        "total_return_multiple": f"{equity:.4f}",
        "days": str(len(returns)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/runtime/paper_runtime.yaml")
    parser.add_argument("--candles-dir", default=None)
    parser.add_argument(
        "--exit",
        default=DC_EXIT,
        choices=("atr_channel", "mid_channel"),
        help=(
            "atr_channel is the pre-registered primary test (crypto trial 118). "
            "mid_channel reproduces crypto trial 88 as declared CONTEXT only; "
            "its result may never be substituted for the verdict."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    candles_dir = Path(args.candles_dir or config.storage.candle_files_directory)
    timeframe = config.data_source.timeframe
    candles = read_candles_jsonl(candles_dir / adjusted_candle_file_name(SYMBOL, timeframe))
    candles_by_symbol = {SYMBOL: candles}

    parameters = BacktestParameters(
        risk_budgets={SYMBOL: Decimal("1")},
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
        enforce_price_tick=False,  # adjusted series is intentionally off-tick
        strategy_name="donchian_breakout_ensemble",
        dc_windows=DC_WINDOWS,
        dc_exit=args.exit,
        dc_atr_window=DC_ATR_WINDOW,
        dc_atr_multiple=DC_ATR_MULTIPLE,
    )

    role = "PRIMARY (crypto trial 118)" if args.exit == DC_EXIT else "CONTEXT (crypto trial 88)"
    result = run_registered_backtest(
        candles_by_symbol,
        parameters=parameters,
        config_hash=config_hash_for(config_snapshot(config)),
        code_version=_code_version(),
        registry_path=config.storage.trial_registry_path,
        holdout_path=config.storage.holdout_lock_path,
        reports_directory=config.storage.backtest_reports_directory,
        recorded_at=datetime.now(UTC),
        operator_note=(
            f"Cross-market validation {role}: donchian "
            f"{'+'.join(str(w) for w in DC_WINDOWS)} exit={args.exit} "
            f"atr={DC_ATR_WINDOW}/{DC_ATR_MULTIPLE} on {SYMBOL} adjusted "
            "(CROSSMARKET_DONCHIAN_PREREGISTRATION.md) [series=adjusted]"
        ),
    )

    metrics = result.report.metrics
    window_closes = [
        candle.close_price
        for candle in candles
        if result.report.data_start <= candle.close_time <= result.report.data_end
    ]
    print(
        json.dumps(
            {
                "role": role,
                "trial_id": result.trial.trial_id,
                "data_start": result.report.data_start.isoformat(),
                "data_end": result.report.data_end.isoformat(),
                "strategy": {
                    "annualized_sharpe": str(metrics.annualized_sharpe),
                    "max_drawdown_fraction": str(metrics.max_drawdown_fraction),
                    "annualized_turnover": str(metrics.annualized_turnover),
                    "final_equity": str(metrics.final_equity),
                    "trade_count": str(metrics.trade_count),
                },
                "buy_and_hold_0050": _buy_and_hold(window_closes),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
