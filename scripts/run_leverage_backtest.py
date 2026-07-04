"""Registered leverage-route backtest: validate synthetic, run the f-frontier.

Adjudicates the pre-registered leverage claim (docs/research/
TW2_UNCONSTRAINED_RESEARCH.md) for the balanced product the user chose
(f=50% 0050 正2 + 50% cash, quarterly rebalanced), and reports f in
{50%, 70%, 100%} as a risk/return frontier. Every f is registered as a
trial. The primary L1/L2 adjudication runs on data BEFORE the locked holdout
boundary; full-span and real-only numbers are reported for transparency.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal

from src.backtest.holdout import load_holdout
from src.backtest.registry import append_trial
from src.config import load_config
from src.leverage import (
    LEVERAGE_DAILY_DRAG_BPS,
    LeveragePoint,
    RebalanceParameters,
    build_leveraged_series,
    calibrate_daily_drag_bps,
    load_underlying_total_return,
    simulate_rebalanced_hold,
    splice_leverage_series,
)

_UNDERLYING = "data/candles/0050_1d_adjusted.jsonl"
_REAL_LEVERAGE = "data/candles/00631L_1d_adjusted.jsonl"
_FRACTIONS = (Decimal("0.5"), Decimal("0.7"), Decimal("1.0"))


def _cagr_mdd(series: list[tuple[date, Decimal]]) -> tuple[Decimal, Decimal]:
    years = (series[-1][0] - series[0][0]).days / 365.25
    cagr = Decimal(str(float(series[-1][1] / series[0][1]) ** (1 / years) - 1))
    peak = series[0][1]
    mdd = Decimal("0")
    for _, v in series:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return cagr, mdd


def _code_version() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    config = load_config("configs/runtime/paper_runtime.yaml")
    recorded_at = datetime.now(UTC)

    underlying = load_underlying_total_return(_UNDERLYING)
    real_candles = load_underlying_total_return(_REAL_LEVERAGE)
    real_series = [
        LeveragePoint(trading_date=d, nav=v, is_synthetic=False) for d, v in real_candles
    ]

    # 1) Validate the synthetic against reality: calibrate drag on the overlap.
    calibrated = calibrate_daily_drag_bps(underlying, real_series)
    print(
        f"[calibration] measured daily drag {calibrated:.4f}bps "
        f"(pinned constant {LEVERAGE_DAILY_DRAG_BPS}bps); "
        f"annual ~{float(calibrated) / 10000 * 246:.2%}"
    )

    # 2) Build synthetic 2x pre-2014 and splice with the real series.
    synthetic = build_leveraged_series(
        underlying, factor=Decimal("2"), daily_drag_bps=LEVERAGE_DAILY_DRAG_BPS
    )
    spliced = splice_leverage_series(synthetic, real_series)
    boundary = real_series[0].trading_date
    print(
        f"[splice] synthetic {spliced[0].trading_date} -> {boundary} + real "
        f"{boundary} -> {spliced[-1].trading_date} ({len(spliced)} points)"
    )

    # 3) Holdout boundary: adjudicate BEFORE it; report full-span separately.
    holdout = load_holdout(config.storage.holdout_lock_path)
    holdout_start = (
        holdout.holdout_start.date() if holdout is not None else spliced[-1].trading_date
    )
    pre_holdout = [p for p in spliced if p.trading_date < holdout_start]

    # Benchmark: 0050 TR buy-and-hold over the SAME spans.
    def bench(series_dates: tuple[date, date]) -> tuple[Decimal, Decimal]:
        seg = [(d, v) for d, v in underlying if series_dates[0] <= d <= series_dates[1]]
        return _cagr_mdd(seg)

    adj_span = (pre_holdout[0].trading_date, pre_holdout[-1].trading_date)
    full_span = (spliced[0].trading_date, spliced[-1].trading_date)
    real_span = (real_series[0].trading_date, real_series[-1].trading_date)
    bench_cagr_adj, bench_mdd_adj = bench(adj_span)
    bench_cagr_full, bench_mdd_full = bench(full_span)

    print(
        f"\n[benchmark 0050 TR] adjudication span {adj_span[0]}->{adj_span[1]}: "
        f"CAGR {bench_cagr_adj:.2%} MaxDD {bench_mdd_adj:.2%}"
    )
    print(
        f"[benchmark 0050 TR] full span {full_span[0]}->{full_span[1]}: "
        f"CAGR {bench_cagr_full:.2%} MaxDD {bench_mdd_full:.2%}\n"
    )

    results: dict[str, dict[str, str]] = {}
    for fraction in _FRACTIONS:
        params = RebalanceParameters(
            fraction=fraction,
            initial_capital=config.account.initial_cash,
            commission_bps=config.execution.effective_commission_bps,
            min_fee=config.execution.min_fee_twd,
            sell_tax_bps=config.execution.sell_tax_bps_etf,
            slippage_bps=config.execution.slippage_bps,
        )
        adj = simulate_rebalanced_hold(pre_holdout, params)
        full = simulate_rebalanced_hold(spliced, params)
        real_only = simulate_rebalanced_hold(
            [p for p in spliced if p.trading_date >= boundary], params
        )
        label = "100%" if fraction == Decimal("1.0") else f"{int(fraction * 100)}%+cash"
        l1 = adj.cagr >= bench_cagr_adj
        l2 = adj.max_drawdown_fraction <= bench_mdd_adj
        beat_1pp = adj.cagr >= bench_cagr_adj + Decimal("0.01")
        print(
            f"[f={label:>9}] ADJ CAGR {adj.cagr:>7.2%} MaxDD {adj.max_drawdown_fraction:>6.2%} "
            f"| L1(>=BH){'PASS' if l1 else 'FAIL'} L2(<=BH MaxDD){'PASS' if l2 else 'FAIL'} "
            f"beat+1pp {'Y' if beat_1pp else 'n'} | rebal {adj.rebalance_count} "
            f"cost {adj.total_costs:.0f}"
        )
        print(
            f"            FULL CAGR {full.cagr:>7.2%} MaxDD {full.max_drawdown_fraction:>6.2%} "
            f"| REAL-only({real_span[0]}+) CAGR {real_only.cagr:>7.2%} "
            f"MaxDD {real_only.max_drawdown_fraction:>6.2%}"
        )
        metrics = {
            "adj_cagr": str(adj.cagr),
            "adj_max_drawdown": str(adj.max_drawdown_fraction),
            "adj_sharpe": str(adj.annualized_sharpe),
            "benchmark_cagr": str(bench_cagr_adj),
            "benchmark_max_drawdown": str(bench_mdd_adj),
            "L1_cagr_ge_benchmark": str(l1),
            "L2_maxdd_le_benchmark": str(l2),
            "beat_benchmark_1pp": str(beat_1pp),
            "full_cagr": str(full.cagr),
            "full_max_drawdown": str(full.max_drawdown_fraction),
            "real_only_cagr": str(real_only.cagr),
            "real_only_max_drawdown": str(real_only.max_drawdown_fraction),
            "rebalance_count": str(adj.rebalance_count),
            "total_costs": str(adj.total_costs),
        }
        results[label] = metrics
        append_trial(
            config.storage.trial_registry_path,
            recorded_at=recorded_at,
            config_hash="leverage-" + str(fraction),
            code_version=_code_version(),
            strategy_id="leverage_rebalanced_hold",
            parameters={
                "fraction": str(fraction),
                "rebalance": "calendar_quarter",
                "underlying": "0050_TR",
                "vehicle": "00631L_real+synthetic2x",
                "daily_drag_bps": str(LEVERAGE_DAILY_DRAG_BPS),
            },
            universe=("00631L", "0050"),
            data_start=datetime.combine(adj_span[0], datetime.min.time(), tzinfo=UTC),
            data_end=datetime.combine(adj_span[1], datetime.min.time(), tzinfo=UTC),
            cost_assumptions={
                "commission_bps": str(config.execution.effective_commission_bps),
                "sell_tax_bps_etf": str(config.execution.sell_tax_bps_etf),
                "slippage_bps": str(config.execution.slippage_bps),
                "min_fee": str(config.execution.min_fee_twd),
            },
            metrics=metrics,
            operator_note=(
                f"leverage route f={fraction} quarterly rebalance; pre-holdout adjudication; "
                "synthetic 2x pre-2014 + real 00631L"
            ),
        )

    print(json.dumps({"benchmark_adj_cagr": str(bench_cagr_adj), "frontier": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
