"""Trading calendar tests pinned to the verified 2026 TWSE schedule.

Fixture facts (docs/research/TW_SIGNAL_DESIGN_RESEARCH.md §Q8):
last trading day before Lunar New Year is 2026-02-11; 02-12/13 are
settlement-only days; holidays run through 02-20 (a make-up workday on
which the market stays CLOSED); trading resumes 2026-02-23.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.data import (
    CalendarCoverageError,
    HolidayEntryKind,
    MarketDataValidationError,
    TradingCalendar,
    parse_holiday_schedule,
    read_calendar_json,
    write_calendar_json,
)

_SCHEDULE_2026: tuple[dict[str, str], ...] = (
    {"Date": "1150101", "Name": "中華民國開國紀念日", "Description": "依規定放假1日。"},
    {"Date": "1150102", "Name": "中華民國115年開始交易日", "Description": "開始交易。"},
    {"Date": "1150212", "Name": "農曆春節前", "Description": "市場無交易，僅辦理結算交割作業。"},
    {"Date": "1150213", "Name": "農曆春節前", "Description": "市場無交易，僅辦理結算交割作業。"},
    {"Date": "1150216", "Name": "農曆除夕", "Description": "依規定放假1日。"},
    {"Date": "1150217", "Name": "春節", "Description": "依規定放假1日。"},
    {"Date": "1150218", "Name": "春節", "Description": "依規定放假1日。"},
    {"Date": "1150219", "Name": "春節", "Description": "依規定放假1日。"},
    {"Date": "1150220", "Name": "補行上班日", "Description": "市場無交易。"},
    {"Date": "1150227", "Name": "和平紀念日", "Description": "彈性放假。"},
    {"Date": "1150403", "Name": "兒童節", "Description": "彈性放假。"},
    {"Date": "1150406", "Name": "民族掃墓節", "Description": "補假1日。"},
    {"Date": "1150501", "Name": "勞動節", "Description": "依規定放假1日。"},
    {"Date": "1150619", "Name": "端午節", "Description": "依規定放假1日。"},
    {"Date": "1150925", "Name": "中秋節", "Description": "依規定放假1日。"},
    {"Date": "1150928", "Name": "教師節", "Description": "依規定放假1日。"},
    {"Date": "1151009", "Name": "國慶日", "Description": "彈性放假。"},
    {"Date": "1151026", "Name": "臺灣光復節", "Description": "補假1日。"},
    {"Date": "1151225", "Name": "行憲紀念日", "Description": "依規定放假1日。"},
)


def _calendar_2026(extra_closures: tuple[date, ...] = ()) -> TradingCalendar:
    entries = parse_holiday_schedule(list(_SCHEDULE_2026))
    return TradingCalendar.from_holiday_entries(entries, year=2026, extra_closures=extra_closures)


def test_schedule_rows_classify_closures_and_informational_rows() -> None:
    entries = parse_holiday_schedule(list(_SCHEDULE_2026))

    by_date = {entry.entry_date: entry for entry in entries}
    assert by_date[date(2026, 1, 1)].kind is HolidayEntryKind.CLOSURE
    assert by_date[date(2026, 1, 2)].kind is HolidayEntryKind.TRADING_INFO
    assert by_date[date(2026, 2, 12)].kind is HolidayEntryKind.CLOSURE
    assert by_date[date(2026, 2, 20)].kind is HolidayEntryKind.CLOSURE


def test_unknown_schedule_wording_fails_loudly() -> None:
    with pytest.raises(MarketDataValidationError, match="unrecognized wording"):
        parse_holiday_schedule([{"Date": "1150707", "Name": "神秘日", "Description": "???"}])


def test_informational_rows_do_not_become_closures() -> None:
    calendar = _calendar_2026()
    assert calendar.is_trading_day(date(2026, 1, 2)) is True


def test_lunar_new_year_block_is_closed_and_resumes_2026_02_23() -> None:
    calendar = _calendar_2026()

    assert calendar.is_trading_day(date(2026, 2, 11)) is True
    for day in range(12, 23):
        assert calendar.is_trading_day(date(2026, 2, day)) is False, day
    assert calendar.is_trading_day(date(2026, 2, 23)) is True
    assert calendar.next_trading_day(date(2026, 2, 11)) == date(2026, 2, 23)
    assert calendar.previous_trading_day(date(2026, 2, 23)) == date(2026, 2, 11)


def test_make_up_workday_is_market_closed() -> None:
    calendar = _calendar_2026()
    # 2026-02-20 is a weekday government make-up workday; the market is closed.
    assert date(2026, 2, 20).weekday() < 5
    assert calendar.is_trading_day(date(2026, 2, 20)) is False


def test_trading_days_between_counts_only_trading_days() -> None:
    calendar = _calendar_2026()

    assert calendar.trading_days_between(date(2026, 2, 11), date(2026, 2, 11)) == 0
    assert calendar.trading_days_between(date(2026, 2, 11), date(2026, 2, 23)) == 1
    # Friday -> Monday across a plain weekend is exactly one trading day.
    assert calendar.trading_days_between(date(2026, 3, 6), date(2026, 3, 9)) == 1
    with pytest.raises(MarketDataValidationError):
        calendar.trading_days_between(date(2026, 3, 9), date(2026, 3, 6))


def test_typhoon_day_enters_as_extra_closure() -> None:
    typhoon = date(2026, 7, 15)
    calendar = _calendar_2026(extra_closures=(typhoon,))

    assert typhoon.weekday() < 5
    assert calendar.is_trading_day(typhoon) is False
    assert calendar.trading_days_between(date(2026, 7, 14), date(2026, 7, 16)) == 1


def test_queries_outside_covered_years_raise() -> None:
    calendar = _calendar_2026()

    with pytest.raises(CalendarCoverageError):
        calendar.is_trading_day(date(2025, 12, 31))
    with pytest.raises(CalendarCoverageError):
        calendar.next_trading_day(date(2026, 12, 31))


def test_merged_calendars_answer_cross_year_queries() -> None:
    calendar_2027 = TradingCalendar(
        covered_years=frozenset({2027}),
        closures=frozenset({date(2027, 1, 1)}),
    )
    merged = _calendar_2026().merge(calendar_2027)

    # 2026-12-31 is a Thursday; 2027-01-01 (Friday) is closed -> 2027-01-04.
    assert merged.next_trading_day(date(2026, 12, 31)) == date(2027, 1, 4)


def test_calendar_json_round_trip(tmp_path: object) -> None:
    calendar = _calendar_2026(extra_closures=(date(2026, 7, 15),))
    directory = str(tmp_path)

    written = write_calendar_json(calendar, directory)
    assert len(written) == 1
    restored = read_calendar_json(directory)

    assert restored == calendar


def test_extra_closure_outside_schedule_year_is_rejected() -> None:
    entries = parse_holiday_schedule(list(_SCHEDULE_2026))
    with pytest.raises(MarketDataValidationError):
        TradingCalendar.from_holiday_entries(entries, year=2026, extra_closures=(date(2027, 1, 5),))


def test_from_trading_dates_infers_closures_for_completed_years() -> None:
    from datetime import timedelta

    # Synthetic completed year: every weekday traded except two holidays.
    holidays = {date(2025, 1, 1), date(2025, 4, 4)}
    all_days = (date(2025, 1, 1) + timedelta(days=offset) for offset in range(365))
    traded = [day for day in all_days if day.weekday() < 5 and day not in holidays]

    calendar = TradingCalendar.from_trading_dates(traded, years=(2025,))

    assert calendar.closures == frozenset(holidays)
    assert calendar.is_trading_day(date(2025, 1, 2)) is True
    assert calendar.is_trading_day(date(2025, 1, 1)) is False
