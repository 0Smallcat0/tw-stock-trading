"""Backfill 00631L (元大台灣50正2) split-adjusted total-return series.

FinMind gives the full raw history since 2014-10-31; the product pays no
cash dividend (futures-based) so the only adjustment is the 2026-03-31 1:22
forward split (official TWTCAU: 443.15 -> 20.14). Writes both raw and
split-adjusted candle files under the storage candle directory.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime

from src.config import load_config
from src.data import (
    CorporateActionEvent,
    CorporateActionKind,
    FinMindPublicClient,
    MarketDataError,
    apply_adjustments,
    write_adjustment_factors,
    write_candles_jsonl,
)
from src.domain import Symbol, taipei_date

_SPLIT = CorporateActionEvent(
    symbol_value="00631L",
    effective_date=date(2026, 3, 31),
    kind=CorporateActionKind.SPLIT,
    prior_close=__import__("decimal").Decimal("443.15"),
    reference_price=__import__("decimal").Decimal("20.14"),
    source="twse_twtcau",
    description="1:22 forward split",
)


def main() -> int:
    config = load_config("configs/runtime/paper_runtime.yaml")
    symbol = Symbol(value="00631L", base_asset="00631L", quote_asset="TWD")
    end = taipei_date(datetime.now(UTC))
    with FinMindPublicClient(
        api_base_url=config.data_source.finmind_api_base_url,
        timeout_seconds=float(config.data_source.timeout_seconds),
    ) as fm:
        candles = fm.fetch_daily_candles(symbol=symbol, start=date(2014, 10, 1), end=end)
    if not candles:
        print("no 00631L data returned", file=sys.stderr)
        return 1
    candles = tuple(sorted(candles, key=lambda c: c.open_time))
    directory = config.storage.candle_files_directory
    raw_path = write_candles_jsonl(candles, f"{directory}/00631L_1d.jsonl")
    adjusted = apply_adjustments(candles, (_SPLIT,))
    adj_path = write_candles_jsonl(adjusted, f"{directory}/00631L_1d_adjusted.jsonl")
    factors_path = write_adjustment_factors((_SPLIT,), f"{directory}/00631L_adjustments.json")
    print(f"00631L: {len(candles)} candles {candles[0].trading_date} -> {candles[-1].trading_date}")
    print(f"  raw      -> {raw_path.name}")
    print(
        f"  adjusted -> {adj_path.name} "
        f"(first {adjusted[0].close_price}, last {adjusted[-1].close_price})"
    )
    print(f"  factors  -> {factors_path.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MarketDataError as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
