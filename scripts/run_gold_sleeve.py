"""Sleeve 3 run (docs/research/SLEEVE3_GOLD_PREREGISTRATION.md).

Replays the crypto program's trial-88 configuration on GLD, unchanged. One
configuration, no grid, no arms: the pre-registration forbids adjusting any
signal parameter on gold data.

Costs and risk brakes are the ones declared in that document, not this
repository's Taiwan runtime values — a Taiwan securities transaction tax and
a plus-or-minus 10% price-limit brake would both be fiction on a US ETF.
They are hardcoded here so a later run cannot quietly use different ones.

Also prints buy-and-hold GLD over the identical window, and the worst
single-day equity move, which the pre-registration requires as proof that no
risk brake influenced the result.
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

from src.backtest import (
    BacktestParameters,
    EquityPoint,
    config_hash_for,
    run_registered_backtest,
)
from src.config import config_snapshot, load_config
from src.data import candle_file_name, read_candles_jsonl

SYMBOL = "GLD"
# Copied verbatim from crypto trial 88. Not tunable here.
DC_WINDOWS = (10, 20, 55, 110)
DC_EXIT = "mid_channel"
DC_ATR_WINDOW = 14
DC_ATR_MULTIPLE = Decimal("3")

# Declared in the pre-registration; deliberately harsher than US retail.
COMMISSION_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("5")
INITIAL_CASH = Decimal("100000")

# Set wider than anything in GLD's history so they cannot bind. The run
# verifies that below; if one binds, the result is void.
MAX_DRAWDOWN_FRACTION = Decimal("0.60")
DAILY_LOSS_PAUSE_FRACTION = Decimal("0.20")
DISASTER_SINGLE_DAY_DROP = Decimal("0.20")
DISASTER_MULTI_SESSION_COUNT = 3
DISASTER_MULTI_SESSION_DROP = Decimal("0.30")

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
        "sessions": str(len(returns)),
    }


def _worst_single_day(equity_curve: tuple[EquityPoint, ...]) -> dict[str, str]:
    worst = 0.0
    worst_at = ""
    previous: Decimal | None = None
    for point in equity_curve:
        if previous is not None and previous > 0:
            move = 1.0 - float(point.equity / previous)
            if move > worst:
                worst, worst_at = move, point.close_time.date().isoformat()
        previous = point.equity
    return {"worst_single_day_drop": f"{worst:.6f}", "on": worst_at}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/runtime/paper_runtime.yaml")
    parser.add_argument("--candles-dir", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config))
    candles_dir = Path(args.candles_dir or config.storage.candle_files_directory)
    # GLD makes no distributions, so the raw close series IS the total-return
    # series; the ingestion gate proves that on every bar before writing.
    candles = read_candles_jsonl(candles_dir / candle_file_name(SYMBOL, "1d"))

    parameters = BacktestParameters(
        risk_budgets={SYMBOL: Decimal("1")},
        initial_cash=INITIAL_CASH,
        account_id=config.account.account_id,
        commission_bps=COMMISSION_BPS,
        min_fee=Decimal("0"),
        sell_tax_bps_etf=Decimal("0"),
        sell_tax_bps_stock=Decimal("0"),
        slippage_bps=SLIPPAGE_BPS,
        quantity_step=Decimal("1"),
        # The pre-registration declares "no lot minimum to clear"; the paper
        # broker refuses a non-positive floor, so this is the smallest value
        # that expresses the same thing. One share of GLD has never been
        # worth less than $40, so it cannot reject a trade.
        min_notional_twd=Decimal("0.01"),
        max_drawdown_fraction=MAX_DRAWDOWN_FRACTION,
        daily_loss_pause_fraction=DAILY_LOSS_PAUSE_FRACTION,
        disaster_single_day_drop_fraction=DISASTER_SINGLE_DAY_DROP,
        disaster_multi_session_count=DISASTER_MULTI_SESSION_COUNT,
        disaster_multi_session_drop_fraction=DISASTER_MULTI_SESSION_DROP,
        stale_data_max_age_seconds=config.risk.stale_data_max_age_seconds,
        enforce_price_tick=False,
        strategy_name="donchian_breakout_ensemble",
        dc_windows=DC_WINDOWS,
        dc_exit=DC_EXIT,
        dc_atr_window=DC_ATR_WINDOW,
        dc_atr_multiple=DC_ATR_MULTIPLE,
    )

    result = run_registered_backtest(
        {SYMBOL: candles},
        parameters=parameters,
        config_hash=config_hash_for(config_snapshot(config)),
        code_version=_code_version(),
        registry_path=config.storage.trial_registry_path,
        holdout_path=config.storage.holdout_lock_path,
        reports_directory=config.storage.backtest_reports_directory,
        recorded_at=datetime.now(UTC),
        operator_note=(
            f"Sleeve 3: donchian {'+'.join(str(w) for w in DC_WINDOWS)} exit={DC_EXIT} "
            f"on {SYMBOL} raw=total-return, US costs "
            f"{COMMISSION_BPS}bps+{SLIPPAGE_BPS}bps no tax "
            "(SLEEVE3_GOLD_PREREGISTRATION.md) [series=raw_is_total_return]"
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
                "brake_check": {
                    **_worst_single_day(result.report.equity_curve),
                    # The pre-registration requires proof that no brake shaped
                    # the result. An empty risk_events tuple is that proof.
                    "risk_events": str(len(result.report.risk_events)),
                },
                "buy_and_hold_gld": _buy_and_hold(window_closes),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
