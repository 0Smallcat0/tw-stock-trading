"""Forward-only shadow tracks for the non-crypto sleeves of the combination.

Computes the mid-channel Donchian 10/20/55/110 signal — the same untuned
rule the crypto program runs — on local candle series and appends one row
per newly closed session to an append-only JSONL per sleeve.

Boundaries:
- Reads local candle files only. It never fetches, so a stale file is a
  refusal rather than a silently repeated signal: TWSE's WAF makes daily
  ingestion impractical, so the refresh cadence is weekly and the record
  says so.
- Places no orders, emits no instruction, and touches no live contract.
- Records only from its own first day forward, so every number it ever
  reports is out of sample.
- One sleeve refusing does not stop the others; the exit code is non-zero
  if any refused, so a scheduled run still shows up as failed.

Usage:
    python -m scripts.shadow_signal_tw            # append where data is fresh
    python -m scripts.shadow_signal_tw --summary  # print every track
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.config import load_config
from src.data import adjusted_candle_file_name, candle_file_name, read_candles_jsonl
from src.domain import Candle
from src.strategies import evaluate_donchian_ensemble

WINDOWS = (10, 20, 55, 110)
EXIT_MODE = "mid_channel"
MAX_STALENESS_DAYS = 10  # a weekly refresh plus a long holiday


@dataclass(frozen=True, slots=True)
class Track:
    """One sleeve's forward record."""

    symbol: str
    track_path: Path
    source_trial: int
    adjusted: bool
    refresh_command: str

    def candle_path(self, directory: Path, timeframe: str) -> Path:
        name = (
            adjusted_candle_file_name(self.symbol, timeframe)
            if self.adjusted
            else candle_file_name(self.symbol, timeframe)
        )
        return directory / name


TRACKS = (
    Track(
        symbol="0050",
        track_path=Path("data/runtime/shadow_tw0050.jsonl"),
        source_trial=23,
        adjusted=True,
        refresh_command="scripts.ingest_public_ohlcv",
    ),
    # GLD makes no distributions, so its raw close series IS its total-return
    # series; the ingestion gate proves that on every bar before writing.
    Track(
        symbol="GLD",
        track_path=Path("data/runtime/shadow_gld.jsonl"),
        source_trial=24,
        adjusted=False,
        refresh_command="scripts.ingest_us_etf_ohlcv",
    ),
)


def load_track(track: Track) -> list[dict[str, Any]]:
    if not track.track_path.exists():
        return []
    return [
        json.loads(line)
        for line in track.track_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def decide(candles: tuple[Candle, ...]) -> tuple[Decimal, tuple[str, ...]]:
    """Replay the state machine to the latest close — a pure function of price."""

    closed = tuple(candle for candle in candles if candle.is_closed)
    states: tuple[bool, ...] | None = None
    fraction = Decimal("0")
    codes: tuple[str, ...] = ()
    for index in range(max(WINDOWS), len(closed)):
        fraction, codes, states = evaluate_donchian_ensemble(
            closed, index, windows=WINDOWS, exit_mode=EXIT_MODE, previous_states=states
        )
    return fraction, codes


def summarize(track: Track) -> None:
    rows = load_track(track)
    if not rows:
        print(f"{track.symbol}: track is empty")
        return
    exposures = [float(row["exposure"]) for row in rows]
    print(
        f"{track.symbol}: sessions={len(rows)} "
        f"first/last={rows[0]['date']}/{rows[-1]['date']} "
        f"equity={rows[-1]['equity']} (start 1000) "
        f"mean_exposure={sum(exposures) / len(exposures):.3f} latest={exposures[-1]:.2f}"
    )


def record(track: Track, *, candles_directory: Path, timeframe: str) -> bool:
    """Append one row. Returns False if the sleeve refused to record."""

    candles = read_candles_jsonl(track.candle_path(candles_directory, timeframe))
    closed = tuple(candle for candle in candles if candle.is_closed)
    latest = closed[-1]
    age = datetime.now(UTC) - latest.close_time
    if age > timedelta(days=MAX_STALENESS_DAYS):
        print(
            f"{track.symbol}: candles end {latest.close_time.date().isoformat()} "
            f"({age.days}d old, limit {MAX_STALENESS_DAYS}d); "
            f"run {track.refresh_command} before recording a signal"
        )
        return False

    rows = load_track(track)
    session = latest.open_time.date().isoformat()
    if rows and str(rows[-1]["date"]) >= session:
        print(f"{track.symbol}: already recorded through {rows[-1]['date']}")
        return True

    fraction, codes = decide(candles)
    equity = Decimal(str(rows[-1]["equity"])) if rows else Decimal("1000")
    if rows:
        prior_close = Decimal(str(rows[-1]["close"]))
        held = Decimal(str(rows[-1]["exposure"]))
        if prior_close > 0:
            equity *= Decimal("1") + held * (latest.close_price / prior_close - Decimal("1"))

    row = {
        "date": session,
        "recorded_at": datetime.now(UTC).isoformat(),
        "symbol": track.symbol,
        "strategy": "donchian_breakout_ensemble",
        "config": {
            "windows": list(WINDOWS),
            "exit": EXIT_MODE,
            "source_trial": track.source_trial,
        },
        "exposure": str(fraction),
        "close": str(latest.close_price),
        "reason_codes": list(codes),
        "equity": str(equity),
    }
    track.track_path.parent.mkdir(parents=True, exist_ok=True)
    with track.track_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"{track.symbol}: appended {session} exposure={fraction} close={latest.close_price}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/runtime/paper_runtime.yaml")
    parser.add_argument("--summary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.summary:
        for track in TRACKS:
            summarize(track)
        return

    config = load_config(Path(args.config))
    candles_directory = Path(config.storage.candle_files_directory)
    timeframe = config.data_source.timeframe
    recorded = [
        record(track, candles_directory=candles_directory, timeframe=timeframe) for track in TRACKS
    ]
    if not all(recorded):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
