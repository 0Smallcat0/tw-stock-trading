"""Thin CLI for the one-time/periodic public daily OHLCV backfill (Goal TW-C).

Flow per symbol: FinMind full history + TWSE official months (2010+) →
close-price reconciliation → splice → corporate-action factors → raw +
adjusted series + factor table on disk. The historical trading calendar
comes from FinMind's realized trading-day list and is persisted per year.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.config import load_config
from src.data import (
    TWSE_STOCK_DAY_FLOOR,
    CorporateActionEvent,
    FinMindPublicClient,
    MarketDataError,
    TradingCalendar,
    TwsePublicClient,
    read_adjustment_factors,
    reconcile_and_splice,
    write_adjustment_factors,
    write_backfill_artifacts,
    write_calendar_json,
)
from src.domain import Candle, Symbol, taipei_date

_DEFAULT_START = date(2003, 1, 1)
_TWT49U_FLOOR = date(2003, 5, 5)
# TWTCAU/TWTAUU reject queries before ROC year 100 (verified live:
# "查詢開始日期小於100年1月1日"). Pre-2011 capital reductions/splits are
# invisible to this pipeline — irrelevant for 0050 (no such events), but a
# documented limitation for any future stock symbol.
_RESUMPTION_REPORTS_FLOOR = date(2011, 1, 1)


# Market-wide corporate actions occur most trading days, so a cache whose
# newest event is older than this cannot be trusted to describe the recent
# window — and an unexempted halt there blocks the entire write.
_CORPORATE_ACTIONS_MAX_STALENESS_DAYS = 7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="configs/runtime/paper_runtime.yaml",
        help="Path to the runtime config.",
    )
    parser.add_argument(
        "--start",
        default=_DEFAULT_START.isoformat(),
        help="Backfill start date (ISO). Default 2003-01-01 (0050 lists 2003-06-30).",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Backfill end date (ISO). Default: today's Taipei date.",
    )
    parser.add_argument(
        "--refresh-corporate-actions",
        action="store_true",
        help=(
            "Re-fetch the corporate-action cache even when it looks fresh. "
            "The cache is refreshed automatically once it lags the requested "
            f"end date by more than {_CORPORATE_ACTIONS_MAX_STALENESS_DAYS} days."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else taipei_date(datetime.now(UTC))

    data_source = config.data_source
    with (
        TwsePublicClient(
            rwd_base_url=data_source.twse_rwd_base_url,
            openapi_base_url=data_source.twse_openapi_base_url,
            timeout_seconds=float(data_source.timeout_seconds),
            min_request_interval_seconds=float(data_source.min_request_interval_seconds),
        ) as twse,
        FinMindPublicClient(
            api_base_url=data_source.finmind_api_base_url,
            timeout_seconds=float(data_source.timeout_seconds),
        ) as finmind,
    ):
        print(f"[calendar] fetching realized trading days {start} -> {end}")
        trading_dates = finmind.fetch_trading_dates(start=start, end=end)
        calendar = _build_calendar(twse, trading_dates=trading_dates, start=start, end=end)
        calendar_paths = write_calendar_json(calendar, config.storage.calendar_schedule_directory)
        print(f"[calendar] wrote {len(calendar_paths)} year file(s)")

        corporate_actions_cache = (
            Path(config.storage.candle_files_directory) / "corporate_actions_all.json"
        )
        cached_events = (
            read_adjustment_factors(corporate_actions_cache)
            if corporate_actions_cache.exists()
            else ()
        )
        newest_cached = max((event.effective_date for event in cached_events), default=None)
        newest_text = newest_cached.isoformat() if newest_cached is not None else "empty"
        cache_is_fresh = (
            newest_cached is not None
            and (end - newest_cached).days <= _CORPORATE_ACTIONS_MAX_STALENESS_DAYS
        )
        if cached_events and cache_is_fresh and not args.refresh_corporate_actions:
            events = cached_events
            print(f"[corporate-actions] loaded {len(events)} cached event(s) through {newest_text}")
        else:
            if cached_events:
                # A stale cache silently hides every halt and distribution
                # after its last event, which makes the quality check fail
                # with a GAP it can never exempt — and then NOTHING is
                # written, freezing the whole series at the cache's date.
                reason = (
                    "forced refresh"
                    if args.refresh_corporate_actions
                    else f"cache ends {newest_text}, {end.isoformat()} requested"
                )
                print(f"[corporate-actions] refreshing ({reason})")
            print(f"[corporate-actions] fetching {_TWT49U_FLOOR} -> {end}")
            events = _fetch_corporate_actions(twse, end=end)
            write_adjustment_factors(events, corporate_actions_cache)
            print(f"[corporate-actions] fetched and cached {len(events)} event(s)")
            # Cool down before the month sweep: TWSE's WAF reacts to sustained
            # request bursts; a pause between phases keeps the sweep clean.
            print("[corporate-actions] cooling down 60s before the TWSE month sweep")
            time.sleep(60)

        for symbol_value in data_source.symbols:
            symbol = Symbol(value=symbol_value, base_asset=symbol_value, quote_asset="TWD")
            print(f"[{symbol_value}] FinMind full history…")
            finmind_candles = finmind.fetch_daily_candles(symbol=symbol, start=start, end=end)
            if not finmind_candles:
                print(f"[{symbol_value}] no FinMind data — skipping")
                continue
            twse_start = max(TWSE_STOCK_DAY_FLOOR, start)
            months = _verification_months(twse_start, end)
            print(
                f"[{symbol_value}] TWSE verification sweep: {len(months)} month(s) "
                "(exhaustive recent 13 + annual historical samples; STOCK_DAY "
                "rate limits forbid full sweeps — see DATA_ADAPTER_TWSE.md)"
            )
            twse_candle_list: list = []
            for month_start in months:
                window_start = max(month_start, twse_start)
                window_end = min(_month_end(month_start), end)
                month_candles = twse.fetch_daily_candles_range(
                    symbol=symbol, start=window_start, end=window_end
                )
                twse_candle_list.extend(month_candles)
                print(
                    f"[{symbol_value}]   {month_start.year}-{month_start.month:02d}: "
                    f"{len(month_candles)} day(s)"
                )
            twse_candles = tuple(twse_candle_list)
            spliced = reconcile_and_splice(
                finmind_candles=finmind_candles, twse_candles=twse_candles
            )
            verified_suspensions = _dually_silent_dates(
                trading_dates=trading_dates,
                finmind_candles=finmind_candles,
                twse_candles=twse_candles,
            )
            if verified_suspensions:
                print(
                    f"[{symbol_value}] verified suspension(s) "
                    f"{', '.join(day.isoformat() for day in verified_suspensions)}: "
                    "both sources silent while the market was open"
                )
            coverage = spliced.overlap_days / max(1, len(spliced.candles))
            print(
                f"[{symbol_value}] verified {spliced.overlap_days} day(s) exactly against "
                f"TWSE ({coverage:.1%} of {len(spliced.candles)}); "
                f"{spliced.finmind_only_days} day(s) from FinMind alone"
            )
            artifacts = write_backfill_artifacts(
                symbol=symbol,
                candles=spliced.candles,
                events=events,
                calendar=calendar,
                observed_on=end,
                stale_trading_days=1,
                candles_directory=config.storage.candle_files_directory,
                adjustments_directory=config.storage.candle_files_directory,
                overlap_days=spliced.overlap_days,
                verified_suspensions=verified_suspensions,
            )
            print(
                f"[{symbol_value}] wrote {artifacts.candle_count} candles -> "
                f"{artifacts.raw_path.name}, {artifacts.adjusted_path.name}, "
                f"{artifacts.factors_path.name}"
            )
    return 0


def _dually_silent_dates(
    *,
    trading_dates: tuple[date, ...],
    finmind_candles: tuple[Candle, ...],
    twse_candles: tuple[Candle, ...],
) -> tuple[date, ...]:
    """Open-market days where BOTH sources have no bar for this symbol.

    A single source going quiet is a fetch problem. Two independent
    sources going quiet on the same day, while both are alive on days
    either side of it, is the symbol not trading — a suspension that the
    corporate-action feed does not always carry (0050 on 2026-07-10 is
    the case that exposed this).

    The inside-the-covered-range requirement is the guard: it makes a
    wholesale fetch failure impossible to mistake for a halt, because a
    failed fetch has no bars after the gap to bracket it.
    """

    finmind_days = {candle.trading_date for candle in finmind_candles if candle.trading_date}
    twse_days = {candle.trading_date for candle in twse_candles if candle.trading_date}
    if not finmind_days or not twse_days:
        return ()
    lower = max(min(finmind_days), min(twse_days))
    upper = min(max(finmind_days), max(twse_days))
    return tuple(
        day
        for day in sorted(trading_dates)
        if lower < day < upper and day not in finmind_days and day not in twse_days
    )


def _build_calendar(
    twse: TwsePublicClient,
    *,
    trading_dates: tuple[date, ...],
    start: date,
    end: date,
) -> TradingCalendar:
    """Historical years from realized trading days; current year from the
    planned holiday schedule plus any realized unscheduled closures."""

    current_year = end.year
    historical_years = tuple(range(start.year, current_year))
    calendars: list[TradingCalendar] = []
    if historical_years:
        calendars.append(
            TradingCalendar.from_trading_dates(
                tuple(day for day in trading_dates if day.year < current_year),
                years=historical_years,
            )
        )
    entries = twse.fetch_current_year_holiday_schedule()
    realized_current = {day for day in trading_dates if day.year == current_year}
    unscheduled: list[date] = []
    cursor = date(current_year, 1, 1)
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in realized_current:
            unscheduled.append(cursor)
        cursor += timedelta(days=1)
    calendars.append(
        TradingCalendar.from_holiday_entries(
            entries, year=current_year, extra_closures=tuple(unscheduled)
        )
    )
    calendar = calendars[0]
    for extra in calendars[1:]:
        calendar = calendar.merge(extra)
    return calendar


def _verification_months(floor: date, end: date) -> tuple[date, ...]:
    """Months to verify exactly against TWSE: recent 13 + one per older year.

    The STOCK_DAY endpoint family soft-bans after ~12-14 sequential requests
    (multi-minute penalty), so exhaustive month sweeps are hostile territory;
    the penalty-box retry in the client absorbs the occasional trip.
    """

    recent: list[date] = []
    cursor = date(end.year, end.month, 1)
    for _ in range(13):
        if cursor < date(floor.year, floor.month, 1):
            break
        recent.append(cursor)
        cursor = _previous_month(cursor)
    oldest_recent = min(recent) if recent else date(end.year, end.month, 1)
    samples = [
        date(year, 6, 1)
        for year in range(floor.year, oldest_recent.year + 1)
        if date(year, 6, 1) < oldest_recent and date(year, 6, 1) >= date(floor.year, floor.month, 1)
    ]
    return tuple(sorted(set(samples) | set(recent)))


def _previous_month(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def _month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _fetch_corporate_actions(
    twse: TwsePublicClient, *, end: date
) -> tuple[CorporateActionEvent, ...]:
    """Year-chunked fetches respecting each report's history floor.

    Long spans are untested on TWSE report endpoints, and rare-event reports
    legitimately return no-data windows.
    """

    events: list[CorporateActionEvent] = []
    for year in range(_TWT49U_FLOOR.year, end.year + 1):
        window_start = max(_TWT49U_FLOOR, date(year, 1, 1))
        window_end = min(end, date(year, 12, 31))
        events.extend(twse.fetch_ex_right_results(start=window_start, end=window_end))
    for year in range(_RESUMPTION_REPORTS_FLOOR.year, end.year + 1):
        window_start = max(_RESUMPTION_REPORTS_FLOOR, date(year, 1, 1))
        window_end = min(end, date(year, 12, 31))
        events.extend(twse.fetch_etf_splits(start=window_start, end=window_end))
        events.extend(twse.fetch_capital_reductions(start=window_start, end=window_end))
    return tuple(events)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MarketDataError as exc:
        print(f"backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
