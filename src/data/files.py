"""Local JSONL candle files for backtest and replay inputs."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.data.types import MarketDataValidationError
from src.domain import Candle, DomainValidationError, Symbol, Timeframe

_FIELDS = (
    "symbol",
    "base_asset",
    "quote_asset",
    "timeframe",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "is_closed",
)


def candle_file_name(symbol_value: str, timeframe_value: str) -> str:
    """Deterministic file name for one symbol/timeframe candle series."""

    return f"{symbol_value}_{timeframe_value}.jsonl"


def write_candles_jsonl(candles: tuple[Candle, ...], path: str | Path) -> Path:
    """Write one symbol/timeframe candle series as append-safe JSONL."""

    if not candles:
        msg = "candles must not be empty"
        raise MarketDataValidationError(msg)
    first = candles[0]
    for candle in candles:
        if candle.symbol != first.symbol or candle.timeframe != first.timeframe:
            msg = "candle files must contain one symbol and one timeframe"
            raise MarketDataValidationError(msg)

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for candle in sorted(candles, key=lambda item: item.open_time):
        row: dict[str, object] = {
            "symbol": candle.symbol.value,
            "base_asset": candle.symbol.base_asset,
            "quote_asset": candle.symbol.quote_asset,
            "timeframe": candle.timeframe.value,
            "open_time": candle.open_time.isoformat(),
            "close_time": candle.close_time.isoformat(),
            "open": str(candle.open_price),
            "high": str(candle.high_price),
            "low": str(candle.low_price),
            "close": str(candle.close_price),
            "volume": str(candle.volume),
            "is_closed": candle.is_closed,
        }
        if candle.trading_date is not None:
            row["trading_date"] = candle.trading_date.isoformat()
        lines.append(json.dumps(row, sort_keys=True))
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


def read_candles_jsonl(path: str | Path) -> tuple[Candle, ...]:
    """Read one symbol/timeframe candle series written by write_candles_jsonl."""

    file_path = Path(path)
    if not file_path.exists():
        msg = f"candle file does not exist: {file_path}"
        raise MarketDataValidationError(msg)

    candles: list[Candle] = []
    for line_number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            msg = f"{file_path}:{line_number} is not valid JSON"
            raise MarketDataValidationError(msg) from exc
        if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
            msg = f"{file_path}:{line_number} is missing required candle fields"
            raise MarketDataValidationError(msg)
        try:
            raw_trading_date = row.get("trading_date")
            candle = Candle(
                symbol=Symbol(
                    value=str(row["symbol"]),
                    base_asset=str(row["base_asset"]),
                    quote_asset=str(row["quote_asset"]),
                ),
                timeframe=Timeframe(str(row["timeframe"])),
                open_time=_utc_datetime(str(row["open_time"])),
                close_time=_utc_datetime(str(row["close_time"])),
                open_price=Decimal(str(row["open"])),
                high_price=Decimal(str(row["high"])),
                low_price=Decimal(str(row["low"])),
                close_price=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                is_closed=bool(row["is_closed"]),
                trading_date=(
                    date.fromisoformat(str(raw_trading_date))
                    if raw_trading_date is not None
                    else None
                ),
            )
        except (DomainValidationError, InvalidOperation, ValueError) as exc:
            msg = f"{file_path}:{line_number} is not a valid candle row: {exc}"
            raise MarketDataValidationError(msg) from exc
        candles.append(candle)
    if not candles:
        msg = f"candle file contains no candles: {file_path}"
        raise MarketDataValidationError(msg)
    return tuple(sorted(candles, key=lambda item: item.open_time))


def _utc_datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        msg = f"candle timestamps must be timezone-aware: {raw}"
        raise MarketDataValidationError(msg)
    return value.astimezone(UTC)
