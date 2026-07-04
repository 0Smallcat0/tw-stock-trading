"""TW4 low-volatility factor backtest — adjudicate the pre-registered claim.

Pipeline (composition layer; may import across packages, unlike src.factor):
  1. load the survivorship-mitigated raw universe (data/universe/*.jsonl)
  2. build the realized market calendar (union of all trading days)
  3. select the schedule for every pre-declared config (raw prices only)
  4. fetch TaiwanStockDividendResult for the union of held names (cached, throttled)
     and build each held name's dividend/split-adjusted total-return series
  5. simulate every config; compute 0050 total-return benchmark over the same span
  6. adjudicate F1/F2, CSCV/PBO over the config grid, DSR; register trials
  7. write docs/reports/TW4_LOWVOL_VERDICT.md

Run `--fetch-only` first (does 1-4, the slow API step, resumable) then the full
run for the deterministic adjudication. Registry is append-only; holdout stays
sealed (adjudication span ends 2025-07-03).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, cast

import httpx

from src.backtest.registry import append_trial, config_hash_for, trial_count
from src.backtest.validation import (
    deflated_sharpe_ratio,
    non_annualized_sharpe_variance,
    probability_of_backtest_overfitting,
)
from src.factor import FactorParameters, RawSeries, select_schedule, simulate
from src.factor.lowvol import FactorResult

UNIVERSE_DIR = Path("data/universe")
DIV_DIR = Path("data/universe_div")
BENCH_FILE = Path("data/candles/0050_1d_adjusted.jsonl")
REGISTRY = Path("docs/reports/research/trial_registry.jsonl")
REPORT = Path("docs/reports/TW4_LOWVOL_VERDICT.md")

FINMIND = "https://api.finmindtrade.com/api/v4/data"
START = date(2005, 1, 1)
HOLDOUT = date(2025, 7, 3)  # adjudicate strictly before this; holdout stays sealed
DIV_START = "2003-01-01"
DIV_END = "2026-07-04"
SLEEP_OK = 13.5
BACKOFF = 300.0


# ---------------------------------------------------------------- data loading


def load_universe(min_rows: int = 260, limit: int | None = None) -> dict[str, RawSeries]:
    out: dict[str, RawSeries] = {}
    files = sorted(p for p in UNIVERSE_DIR.glob("*.jsonl") if not p.name.startswith("_"))
    for path in files:
        dates: list[date] = []
        close: list[float] = []
        turnover: list[float] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            c = row.get("c")
            if c is None or float(c) <= 0.0:
                continue
            dates.append(date.fromisoformat(str(row["d"])))
            close.append(float(c))
            turnover.append(float(row.get("m") or 0.0))
        if len(dates) < min_rows:
            continue
        out[path.stem] = RawSeries(path.stem, tuple(dates), tuple(close), tuple(turnover))
        if limit is not None and len(out) >= limit:
            break
    return out


def market_calendar(raw_map: dict[str, RawSeries]) -> list[date]:
    days: set[date] = set()
    for series in raw_map.values():
        days.update(series.dates)
    return sorted(days)


# ------------------------------------------------------ dividend / adjustment


def _get(params: dict[str, str]) -> dict[str, Any]:
    resp = httpx.get(FINMIND, params=params, timeout=90.0)
    resp.raise_for_status()
    return cast("dict[str, Any]", resp.json())


def fetch_dividend_events(stock_id: str) -> list[tuple[date, float]]:
    """Cached ex-dividend adjustment factors (after_price / before_price)."""

    DIV_DIR.mkdir(parents=True, exist_ok=True)
    cache = DIV_DIR / f"{stock_id}.json"
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
    else:
        for attempt in range(1, 5):
            try:
                payload = _get(
                    {
                        "dataset": "TaiwanStockDividendResult",
                        "data_id": stock_id,
                        "start_date": DIV_START,
                        "end_date": DIV_END,
                    }
                )
                rows = payload.get("data", []) if payload.get("status") == 200 else []
                cache.write_text(json.dumps(rows), encoding="utf-8")
                time.sleep(SLEEP_OK)
                break
            except httpx.HTTPError:
                if attempt == 4:
                    return []
                time.sleep(BACKOFF)
        else:
            return []
    events: list[tuple[date, float]] = []
    for row in rows:
        before = row.get("before_price")
        after = row.get("after_price")
        if before is None or after is None:
            continue
        before_f = float(before)
        after_f = float(after)
        if before_f > 0.0 and after_f > 0.0:
            events.append((date.fromisoformat(str(row["date"])), after_f / before_f))
    events.sort()
    return events


def build_adjusted_lookup(series: RawSeries, events: list[tuple[date, float]]) -> dict[date, float]:
    """Backward dividend/split-adjusted close: raw x product(factors after the day)."""

    if not events:
        return dict(zip(series.dates, series.close, strict=True))
    ex_dates = [d for d, _ in events]
    factors = [f for _, f in events]
    suffix: list[float] = [1.0] * (len(events) + 1)
    for i in range(len(events) - 1, -1, -1):
        suffix[i] = suffix[i + 1] * factors[i]
    lookup: dict[date, float] = {}
    for day, raw in zip(series.dates, series.close, strict=True):
        lo, hi = 0, len(ex_dates)
        while lo < hi:  # first event strictly after `day`
            mid = (lo + hi) // 2
            if ex_dates[mid] > day:
                hi = mid
            else:
                lo = mid + 1
        lookup[day] = raw * suffix[lo]
    return lookup


# --------------------------------------------------------------- configs/grid


@dataclass(frozen=True, slots=True)
class Config:
    label: str
    params: FactorParameters
    is_primary: bool = False


def build_configs() -> list[Config]:
    configs: list[Config] = []
    for n in (20, 50, 100):
        for lookback in (126, 252):
            for weight in ("equal", "inverse_vol"):
                primary = n == 50 and lookback == 252 and weight == "equal"
                configs.append(
                    Config(
                        label=f"n{n}_lb{lookback}_{weight}",
                        params=FactorParameters(n=n, lookback_days=lookback, weight=weight),
                        is_primary=primary,
                    )
                )
    return configs


# ------------------------------------------------------------------- metrics


@dataclass(frozen=True, slots=True)
class Metrics:
    cagr: float
    max_drawdown: float
    sharpe: float


def benchmark_metrics(start: date, end: date) -> tuple[Metrics, list[float]]:
    dates: list[date] = []
    close: list[float] = []
    for line in BENCH_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        day = date.fromisoformat(str(row["trading_date"]))
        if start <= day <= end:
            dates.append(day)
            close.append(float(row["close"]))
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = (close[-1] / close[0]) ** (1.0 / years) - 1.0
    peak = close[0]
    max_dd = 0.0
    rets: list[float] = []
    for i, value in enumerate(close):
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak)
        if i > 0 and close[i - 1] > 0.0:
            rets.append(value / close[i - 1] - 1.0)
    ppy = len(close) / years
    sharpe = fmean(rets) / stdev(rets) * (ppy**0.5) if len(rets) >= 2 and stdev(rets) > 0 else 0.0
    return Metrics(cagr, max_dd, sharpe), rets


def monthly_returns(result: FactorResult) -> dict[tuple[int, int], float]:
    """Compress a daily equity curve to per-calendar-month returns."""

    by_month: dict[tuple[int, int], tuple[float, float]] = {}
    for day, value in result.equity_curve:
        key = (day.year, day.month)
        first, _ = by_month.get(key, (value, value))
        by_month[key] = (first, value)
    prev_close: float | None = None
    out: dict[tuple[int, int], float] = {}
    for key in sorted(by_month):
        first, last = by_month[key]
        base = prev_close if prev_close is not None else first
        out[key] = last / base - 1.0 if base > 0.0 else 0.0
        prev_close = last
    return out


def code_version() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def dirty_tree() -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return bool(out)
    except (subprocess.SubprocessError, OSError):
        return True


# ----------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="cap universe size (smoke test)")
    parser.add_argument("--fetch-only", action="store_true", help="only cache dividends, then exit")
    parser.add_argument("--no-register", action="store_true", help="skip trial registration")
    args = parser.parse_args()

    print("loading universe ...", flush=True)
    raw_map = load_universe(limit=args.limit)
    calendar = market_calendar(raw_map)
    end = max(d for d in calendar if d < HOLDOUT)
    print(
        f"universe: {len(raw_map)} stocks | "
        f"calendar {calendar[0]}..{calendar[-1]} | adj end {end}"
    )

    configs = build_configs()
    print("selecting schedules for all configs ...", flush=True)
    schedules = {c.label: select_schedule(raw_map, calendar, START, end, c.params) for c in configs}
    held: set[str] = {sid for plans in schedules.values() for p in plans for sid in p.weights}
    print(f"held union across configs: {len(held)} names -> fetching dividends")

    price_lookup: dict[str, dict[date, float]] = {}
    for i, sid in enumerate(sorted(held), start=1):
        events = fetch_dividend_events(sid)
        price_lookup[sid] = build_adjusted_lookup(raw_map[sid], events)
        if i % 25 == 0:
            print(f"  dividends {i}/{len(held)}", flush=True)
    if args.fetch_only:
        print("fetch-only done.")
        return

    bench, _ = benchmark_metrics(START, end)
    print(
        f"\n0050 benchmark {START}..{end}: CAGR {bench.cagr:.4%} "
        f"MaxDD {bench.max_drawdown:.2%} Sharpe {bench.sharpe:.3f}"
    )

    results: dict[str, FactorResult] = {}
    for c in configs:
        results[c.label] = simulate(schedules[c.label], price_lookup, calendar, c.params)
        r = results[c.label]
        star = " *PRIMARY" if c.is_primary else ""
        print(
            f"  {c.label:26s} CAGR {r.cagr:7.4%} MaxDD {r.max_drawdown:6.2%} "
            f"Sharpe {r.annualized_sharpe:6.3f} names {r.avg_names:4.1f} "
            f"turn {r.annual_turnover:4.2f}{star}"
        )

    primary = next(c for c in configs if c.is_primary)
    pr = results[primary.label]
    f1 = pr.cagr >= bench.cagr
    f2 = pr.max_drawdown <= bench.max_drawdown

    # CSCV/PBO over the config grid on aligned monthly returns
    per_config_month = {label: monthly_returns(res) for label, res in results.items()}
    common = sorted(set.intersection(*(set(m) for m in per_config_month.values())))
    labels = [c.label for c in configs]
    matrix = [[per_config_month[label][month] for label in labels] for month in common]
    pbo = probability_of_backtest_overfitting(matrix, block_count=16)

    # DSR on the primary, honest N = registry after appending the 12 grid trials
    n_after = trial_count(REGISTRY) + (0 if args.no_register else len(configs))
    grid_sharpes = [results[c.label].annualized_sharpe for c in configs]
    ppy_primary = pr.periods_per_year
    var_nonann = non_annualized_sharpe_variance(grid_sharpes, periods_per_year=round(ppy_primary))
    try:
        dsr = deflated_sharpe_ratio(
            list(pr.daily_returns),
            trial_sharpe_variance=var_nonann,
            effective_trials=max(1, n_after),
        )
        dsr_value = dsr.deflated_sharpe_ratio
    except Exception as exc:  # noqa: BLE001 - report instead of crash
        dsr_value = float("nan")
        print(f"DSR could not be computed: {exc}")

    verdict = f1 and f2 and pbo.pbo <= 0.05 and dsr_value >= 0.95
    print("\n=== ADJUDICATION ===")
    print(f"F1 CAGR  {pr.cagr:.4%} >= {bench.cagr:.4%} : {'PASS' if f1 else 'FAIL'}")
    print(
        f"F2 MaxDD {pr.max_drawdown:.2%} <= {bench.max_drawdown:.2%} : "
        f"{'PASS' if f2 else 'FAIL'}"
    )
    print(f"PBO {pbo.pbo:.3f} <= 0.05 : {'PASS' if pbo.pbo <= 0.05 else 'FAIL'}")
    print(f"DSR {dsr_value:.3f} >= 0.95 : {'PASS' if dsr_value >= 0.95 else 'FAIL'}")
    print(f"OVERALL: {'PASS' if verdict else 'FAIL'}")

    if not args.no_register:
        register_trials(configs, results, bench, pbo.pbo, dsr_value, len(raw_map), calendar, end)
    write_report(configs, results, bench, pr, f1, f2, pbo.pbo, dsr_value, verdict,
                 len(raw_map), len(held), calendar, end)
    print(f"\nreport -> {REPORT}")


def register_trials(
    configs: list[Config],
    results: dict[str, FactorResult],
    bench: Metrics,
    pbo: float,
    dsr: float,
    universe_size: int,
    calendar: list[date],
    end: date,
) -> None:
    now = datetime.now(UTC)
    start_dt = datetime(START.year, START.month, START.day, tzinfo=UTC)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=UTC)
    dirty = dirty_tree()
    for c in configs:
        r = results[c.label]
        snapshot: dict[str, object] = {
            "strategy": "lowvol_factor",
            "label": c.label,
            "params": asdict(c.params),
        }
        append_trial(
            REGISTRY,
            recorded_at=now,
            config_hash=config_hash_for(snapshot),
            code_version=code_version() + ("-dirty" if dirty else ""),
            strategy_id="lowvol_factor",
            parameters={k: str(v) for k, v in asdict(c.params).items()},
            universe=(f"survivorship_mitigated_{universe_size}",),
            data_start=start_dt,
            data_end=end_dt,
            cost_assumptions={
                "commission_bps": "8.55",
                "min_fee": "20",
                "sell_tax_bps": "30",
                "slippage_bps": "15",
            },
            metrics={
                "cagr": f"{r.cagr:.6f}",
                "max_drawdown": f"{r.max_drawdown:.6f}",
                "sharpe": f"{r.annualized_sharpe:.6f}",
                "bench_cagr": f"{bench.cagr:.6f}",
                "bench_maxdd": f"{bench.max_drawdown:.6f}",
                "pbo": f"{pbo:.6f}",
                "dsr": f"{dsr:.6f}",
                "primary": str(c.is_primary),
            },
            operator_note="TW4 low-vol factor grid; holdout 2025-07-03 sealed",
        )


def write_report(
    configs: list[Config],
    results: dict[str, FactorResult],
    bench: Metrics,
    primary: FactorResult,
    f1: bool,
    f2: bool,
    pbo: float,
    dsr: float,
    verdict: bool,
    universe_size: int,
    held: int,
    calendar: list[date],
    end: date,
) -> None:
    lines: list[str] = []
    lines.append("# TW4 低波動因子路線 — 裁決報告\n")
    lines.append(f"日期：`{date.today().isoformat()}`  ")
    lines.append("預先登記：docs/research/TW4_LOWVOL_PREREGISTRATION.md（跑前寫死）  ")
    lines.append(f"資料：自建倖存者-緩解 FinMind 宇宙 {universe_size} 檔（held {held} 檔已還原）  ")
    lines.append(f"裁決跨度：{START} → {end}（holdout {HOLDOUT}+ 保持鎖定）\n")
    lines.append(f"## 裁決：**{'PASS' if verdict else 'FAIL'}**\n")
    v1 = "PASS" if f1 else "FAIL"
    v2 = "PASS" if f2 else "FAIL"
    vp = "PASS" if pbo <= 0.05 else "FAIL"
    vd = "PASS" if dsr >= 0.95 else "FAIL"
    lines.append("```text")
    lines.append(f"F1 報酬  CAGR  {primary.cagr:.4%} >= 0050 {bench.cagr:.4%}  -> {v1}")
    lines.append(
        f"F2 回撤  MaxDD {primary.max_drawdown:.2%} <= 0050 {bench.max_drawdown:.2%}  -> {v2}"
    )
    lines.append(f"過擬合  PBO {pbo:.3f} <= 0.05  -> {vp}")
    lines.append(f"去膨脹  DSR {dsr:.3f} >= 0.95  -> {vd}")
    lines.append("```\n")
    lines.append("## 完整 config 網格（裁決跨度，扣全成本）\n")
    lines.append("| config | CAGR | MaxDD | Sharpe | avg names | 年換手 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    bench_cells = f"{bench.cagr:.2%} | {bench.max_drawdown:.2%} | {bench.sharpe:.3f}"
    lines.append(f"| **0050 含息（基準）** | {bench_cells} | — | — |")
    for c in configs:
        r = results[c.label]
        star = " **(primary)**" if c.is_primary else ""
        lines.append(
            f"| {c.label}{star} | {r.cagr:.2%} | {r.max_drawdown:.2%} | "
            f"{r.annualized_sharpe:.3f} | {r.avg_names:.1f} | {r.annual_turnover:.2f} |"
        )
    lines.append("\n## 倖存者-緩解揭露\n")
    lines.append(
        "宇宙為 Info∪Delisting 的倖存者-**緩解**版，非完全 free（FinMind 免費下市清單"
        "已證漏掉 3662 樂陞級名）。倖存者偏誤只會**美化**報酬：若上表 FAIL，真正 free 版"
        "只會更差，故 FAIL 為 a fortiori 穩健；若 PASS 則需加此但書。減資調整未由"
        "DividendResult 涵蓋（低波動大型股罕見），列為殘餘。"
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
