"""Render the README result charts from committed adjudication artifacts.

Sources, in order of preference:
- strategy daily returns: ``docs/reports/research/trial_returns/trial-000004.json`` (committed)
- benchmark daily returns: ``docs/reports/research/benchmark_returns/trial-000004_benchmark.json``
  (committed; derived once from the local full trial report and cross-checked
  against the verdict headline numbers before writing)
- target exposure ladder: ``docs/reports/backtests/trial-000004/report.json`` (local only;
  the exposure chart is skipped when absent)

Outputs light/dark PNG pairs under ``docs/assets/``.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import NullFormatter

ROOT = Path(__file__).resolve().parents[1]
TRIAL_RETURNS = ROOT / "docs" / "reports" / "research" / "trial_returns" / "trial-000004.json"
BENCHMARK_RETURNS = (
    ROOT / "docs" / "reports" / "research" / "benchmark_returns" / "trial-000004_benchmark.json"
)
LOCAL_REPORT = ROOT / "docs" / "reports" / "backtests" / "trial-000004" / "report.json"
ASSETS = ROOT / "docs" / "assets"

INITIAL_EQUITY = 100_000.0

# Verdict headline numbers (docs/reports/TW_QUALIFICATION_VERDICT.md) used as
# cross-checks: the charts must reproduce the adjudicated record, not approximate it.
EXPECTED = {
    "strategy_final": 276_230.0,
    "benchmark_final": 787_034.0,
    "strategy_maxdd": -0.2632,
    "benchmark_maxdd": -0.5575,
}

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "strategy": "#2a78d6",
        "benchmark": "#898781",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "strategy": "#3987e5",
        "benchmark": "#898781",
    },
}


def load_strategy_returns() -> list[float]:
    payload = json.loads(TRIAL_RETURNS.read_text(encoding="utf-8"))
    return [float(r) for r in payload["daily_returns"]]


def derive_benchmark_artifact() -> dict[str, Any]:
    """Build the committed benchmark artifact from the local full trial report."""
    report = json.loads(LOCAL_REPORT.read_text(encoding="utf-8"))
    curve = report["report"]["equity_curve"]
    dates: list[str] = []
    returns: list[float] = []
    previous = INITIAL_EQUITY
    for row in curve:
        equity = float(row["benchmark_equity"])
        returns.append(equity / previous - 1.0)
        previous = equity
        dates.append(datetime.fromisoformat(row["close_time"]).date().isoformat())
    return {
        "symbol": "0050",
        "description": (
            "Buy-and-hold benchmark daily returns on the dividend/split-adjusted "
            "(total-return) 0050 series over the trial #4 adjudication span; derived "
            "from the registered trial report equity curve."
        ),
        "source": "docs/reports/backtests/trial-000004/report.json (benchmark_equity)",
        "first_return_date": dates[0],
        "last_return_date": dates[-1],
        "dates": dates,
        "daily_returns": returns,
    }


def load_benchmark() -> tuple[list[date], list[float]]:
    if not BENCHMARK_RETURNS.exists():
        if not LOCAL_REPORT.exists():
            raise SystemExit(
                "benchmark artifact missing and local trial report unavailable; "
                "cannot derive benchmark returns"
            )
        artifact = derive_benchmark_artifact()
        BENCHMARK_RETURNS.parent.mkdir(parents=True, exist_ok=True)
        BENCHMARK_RETURNS.write_text(
            json.dumps(artifact, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        print(f"wrote {BENCHMARK_RETURNS.relative_to(ROOT)}")
    payload = json.loads(BENCHMARK_RETURNS.read_text(encoding="utf-8"))
    dates = [date.fromisoformat(d) for d in payload["dates"]]
    returns = [float(r) for r in payload["daily_returns"]]
    return dates, returns


def equity_curve(returns: list[float]) -> list[float]:
    equity = INITIAL_EQUITY
    curve = []
    for r in returns:
        equity *= 1.0 + r
        curve.append(equity)
    return curve


def drawdown_curve(curve: list[float]) -> list[float]:
    peak = INITIAL_EQUITY
    drawdowns = []
    for value in curve:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1.0)
    return drawdowns


def cross_check(strategy: list[float], benchmark: list[float]) -> None:
    checks = {
        "strategy_final": strategy[-1],
        "benchmark_final": benchmark[-1],
        "strategy_maxdd": min(drawdown_curve(strategy)),
        "benchmark_maxdd": min(drawdown_curve(benchmark)),
    }
    for key, actual in checks.items():
        expected = EXPECTED[key]
        if not math.isclose(actual, expected, rel_tol=5e-3):
            raise SystemExit(f"cross-check failed: {key} = {actual:.4f}, expected ~{expected}")
        print(f"cross-check ok: {key} = {actual:,.2f}")


def style_axes(ax: Any, theme: dict[str, str]) -> None:
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=theme["muted"], labelsize=8.5, length=3, width=0.8)
    ax.grid(axis="y", color=theme["grid"], linewidth=0.6)
    ax.set_axisbelow(True)


def make_equity_chart(
    dates: list[date],
    strategy: list[float],
    benchmark: list[float],
    theme_name: str,
    theme: dict[str, str],
) -> None:
    fig, (ax_eq, ax_dd) = plt.subplots(
        2,
        1,
        figsize=(9.6, 6.4),
        dpi=160,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.14},
    )
    fig.patch.set_facecolor(theme["surface"])

    ax_eq.plot(dates, benchmark, color=theme["benchmark"], linewidth=1.5, label="0050 buy-and-hold")
    ax_eq.plot(dates, strategy, color=theme["strategy"], linewidth=1.8, label="Strategy (ladder)")
    ax_eq.set_yscale("log")
    ax_eq.set_yticks([100_000, 200_000, 400_000, 800_000])
    ax_eq.set_yticklabels(["100k", "200k", "400k", "800k"])
    ax_eq.yaxis.set_minor_formatter(NullFormatter())
    ax_eq.annotate(
        f"{benchmark[-1] / 1000:,.0f}k",
        (dates[-1], benchmark[-1]),
        xytext=(6, 0),
        textcoords="offset points",
        color=theme["ink2"],
        fontsize=8.5,
        va="center",
    )
    ax_eq.annotate(
        f"{strategy[-1] / 1000:,.0f}k",
        (dates[-1], strategy[-1]),
        xytext=(6, 0),
        textcoords="offset points",
        color=theme["ink2"],
        fontsize=8.5,
        va="center",
    )
    ax_eq.legend(
        loc="upper left",
        frameon=False,
        fontsize=9,
        labelcolor=theme["ink2"],
        handlelength=1.6,
    )
    ax_eq.set_title(
        "Pre-registered adjudication (trial #4) — equity, log scale, net of all costs\n"
        "0050 exposure-ladder strategy vs buy-and-hold · 100,000 TWD start · 2004–2025",
        loc="left",
        fontsize=10.5,
        color=theme["ink"],
        pad=10,
    )

    strat_dd = drawdown_curve(strategy)
    bench_dd = drawdown_curve(benchmark)
    ax_dd.plot(dates, [d * 100 for d in bench_dd], color=theme["benchmark"], linewidth=1.2)
    ax_dd.plot(dates, [d * 100 for d in strat_dd], color=theme["strategy"], linewidth=1.4)
    ax_dd.fill_between(
        dates, [d * 100 for d in bench_dd], 0, color=theme["benchmark"], alpha=0.14, linewidth=0
    )
    ax_dd.fill_between(
        dates, [d * 100 for d in strat_dd], 0, color=theme["strategy"], alpha=0.18, linewidth=0
    )
    ax_dd.set_title("Drawdown", loc="left", fontsize=10, color=theme["ink"], pad=6)
    ax_dd.set_ylim(-62, 2)

    bench_trough = min(range(len(bench_dd)), key=lambda i: bench_dd[i])
    strat_trough = min(range(len(strat_dd)), key=lambda i: strat_dd[i])
    ax_dd.annotate(
        f"{bench_dd[bench_trough] * 100:.1f}%",
        (dates[bench_trough], bench_dd[bench_trough] * 100),
        xytext=(8, -2),
        textcoords="offset points",
        color=theme["ink2"],
        fontsize=8.5,
    )
    ax_dd.annotate(
        f"{strat_dd[strat_trough] * 100:.1f}%",
        (dates[strat_trough], strat_dd[strat_trough] * 100),
        xytext=(8, -10),
        textcoords="offset points",
        color=theme["ink2"],
        fontsize=8.5,
    )

    for ax in (ax_eq, ax_dd):
        style_axes(ax, theme)
        ax.xaxis.set_major_locator(mdates.YearLocator(3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax_eq.margins(x=0.01)

    out = ASSETS / f"trial4_equity_drawdown_{theme_name}.png"
    fig.savefig(out, facecolor=theme["surface"], bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


def make_exposure_chart(theme_name: str, theme: dict[str, str]) -> None:
    report = json.loads(LOCAL_REPORT.read_text(encoding="utf-8"))
    targets = report["report"]["targets"]
    dates = [datetime.fromisoformat(t["as_of"]).date() for t in targets]
    weights = [float(t["target_weights"].get("0050", "0")) * 100 for t in targets]
    changes = sum(1 for a, b in zip(weights, weights[1:]) if a != b)
    years = (dates[-1] - dates[0]).days / 365.25

    fig, ax = plt.subplots(figsize=(9.6, 2.9), dpi=160)
    fig.patch.set_facecolor(theme["surface"])
    ax.step(dates, weights, where="post", color=theme["strategy"], linewidth=0.7)
    ax.set_ylim(-4, 104)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_title(
        f"Target equity exposure ladder — {changes:,} changes in {years:.1f} years "
        f"({changes / years:.0f}/yr): the whipsaw that decided the verdict",
        loc="left",
        fontsize=10.5,
        color=theme["ink"],
        pad=10,
    )
    style_axes(ax, theme)
    ax.xaxis.set_major_locator(mdates.YearLocator(3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.margins(x=0.01)

    out = ASSETS / f"trial4_exposure_{theme_name}.png"
    fig.savefig(out, facecolor=theme["surface"], bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    strategy_returns = load_strategy_returns()
    bench_dates, bench_returns = load_benchmark()
    if len(strategy_returns) != len(bench_returns):
        raise SystemExit(
            f"series length mismatch: strategy {len(strategy_returns)} "
            f"vs benchmark {len(bench_returns)}"
        )
    strategy = equity_curve(strategy_returns)
    benchmark = equity_curve(bench_returns)
    cross_check(strategy, benchmark)
    for theme_name, theme in THEMES.items():
        make_equity_chart(bench_dates, strategy, benchmark, theme_name, theme)
        if LOCAL_REPORT.exists():
            make_exposure_chart(theme_name, theme)
        else:
            print("local trial report absent; exposure chart skipped")


if __name__ == "__main__":
    main()
