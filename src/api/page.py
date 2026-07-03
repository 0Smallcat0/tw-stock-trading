"""Static dashboard page: human-first information design over the read-only API.

The HTML/CSS/JS lives in ``templates/dashboard.html`` (no CDN, no framework)
per the MVP dashboard contract: browser polling against the JSON endpoints,
presentation-layer translation of codes and numbers into the operator's
language.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"

DASHBOARD_HTML = _TEMPLATE_PATH.read_text(encoding="utf-8")
