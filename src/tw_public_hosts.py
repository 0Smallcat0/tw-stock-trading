"""Public Taiwan market data hosts used by the Core MVP.

All endpoints are keyless public data sources. FinMind works anonymously
(300 requests/hour); a free registered token only raises the quota and is
never required. Broker APIs are permanently out of scope.
"""

from __future__ import annotations

TWSE_RWD_BASE_URL = "https://www.twse.com.tw"
TWSE_OPENAPI_BASE_URL = "https://openapi.twse.com.tw"
FINMIND_API_BASE_URL = "https://api.finmindtrade.com"
