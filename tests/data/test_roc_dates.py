from __future__ import annotations

from datetime import date

import pytest

from src.data import MarketDataValidationError, parse_roc_cjk, parse_roc_compact, parse_roc_slash


def test_compact_roc_date_parses() -> None:
    assert parse_roc_compact("1150702") == date(2026, 7, 2)
    assert parse_roc_compact("990104") == date(2010, 1, 4)


def test_slash_roc_date_parses_including_leading_space_two_digit_years() -> None:
    assert parse_roc_slash("115/06/01") == date(2026, 6, 1)
    assert parse_roc_slash(" 99/01/04") == date(2010, 1, 4)


def test_cjk_roc_date_parses() -> None:
    assert parse_roc_cjk("114年06月16日") == date(2025, 6, 16)
    assert parse_roc_cjk("92年5月5日") == date(2003, 5, 5)


@pytest.mark.parametrize("raw", ("", "2026-07-02", "115/13/01", "1151340", "abc", "115年99月99日"))
def test_invalid_roc_dates_raise(raw: str) -> None:
    for parser in (parse_roc_compact, parse_roc_slash, parse_roc_cjk):
        with pytest.raises(MarketDataValidationError):
            parser(raw)
