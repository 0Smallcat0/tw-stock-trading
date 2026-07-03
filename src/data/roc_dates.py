"""ROC (Minguo) calendar date parsing for TWSE/TPEx payloads.

TWSE endpoints emit ROC dates in three formats that must never leak past
the data layer:

- compact:  ``"1150702"``            (OpenAPI)
- slash:    ``"115/06/01"`` and 2-digit years with a LEADING SPACE
            (`` 99/01/04``)          (RWD after-trading rows)
- CJK:      ``"114年06月16日"``       (TWT49U corporate-action rows)

ROC year + 1911 = Gregorian year.
"""

from __future__ import annotations

import re
from datetime import date

from src.data.types import MarketDataValidationError

_ROC_OFFSET = 1911
_COMPACT_PATTERN = re.compile(r"^(\d{2,3})(\d{2})(\d{2})$")
_SLASH_PATTERN = re.compile(r"^(\d{2,3})/(\d{2})/(\d{2})$")
_CJK_PATTERN = re.compile(r"^(\d{2,3})年(\d{1,2})月(\d{1,2})日$")


def parse_roc_compact(raw: str) -> date:
    """Parse a compact ROC date such as ``"1150702"``."""

    match = _COMPACT_PATTERN.fullmatch(raw.strip())
    if match is None:
        msg = f"not a compact ROC date: {raw!r}"
        raise MarketDataValidationError(msg)
    return _build_date(raw, match.group(1), match.group(2), match.group(3))


def parse_roc_slash(raw: str) -> date:
    """Parse a slash ROC date such as ``"115/06/01"`` or `` 99/01/04``."""

    match = _SLASH_PATTERN.fullmatch(raw.strip())
    if match is None:
        msg = f"not a slash ROC date: {raw!r}"
        raise MarketDataValidationError(msg)
    return _build_date(raw, match.group(1), match.group(2), match.group(3))


def parse_roc_cjk(raw: str) -> date:
    """Parse a CJK ROC date such as ``"114年06月16日"``."""

    match = _CJK_PATTERN.fullmatch(raw.strip())
    if match is None:
        msg = f"not a CJK ROC date: {raw!r}"
        raise MarketDataValidationError(msg)
    return _build_date(raw, match.group(1), match.group(2), match.group(3))


def _build_date(raw: str, roc_year: str, month: str, day: str) -> date:
    try:
        return date(int(roc_year) + _ROC_OFFSET, int(month), int(day))
    except ValueError as exc:
        msg = f"not a valid ROC calendar date: {raw!r}"
        raise MarketDataValidationError(msg) from exc
