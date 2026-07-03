"""Non-order risk events for the Core MVP (disaster notices, limit days).

TW recalibration (docs/contracts/RISK_GATE_CONTRACT.md TW amendments): under
the ±10% daily price limit a -20% single day is impossible, so the disaster
brake is a DUAL trigger — a single-day close in limit-down territory (-9%),
or a multi-session slide (-15% over 3 sessions, the 2025-04 tariff-crash
shape where the limit spread one shock over three days).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.domain import Symbol
from src.risk.types import RiskGateError

DISASTER_SINGLE_DAY_DROP = "DISASTER_SINGLE_DAY_DROP"
DISASTER_MULTI_SESSION_DROP = "DISASTER_MULTI_SESSION_DROP"
REEVALUATE_REQUIRED = "REEVALUATE_REQUIRED"
LIMIT_DAY = "LIMIT_DAY"

DEFAULT_DISASTER_DROP_FRACTION = Decimal("0.09")
DEFAULT_MULTI_SESSION_COUNT = 3
DEFAULT_MULTI_SESSION_DROP_FRACTION = Decimal("0.15")
DEFAULT_LIMIT_DAY_FRACTION = Decimal("0.095")


@dataclass(frozen=True, slots=True)
class RiskEvent:
    """Auditable non-order risk event emitted by the composition layer.

    A disaster notice is risk control, not a strategy parameter: it forces a
    re-evaluation notification but never mutates strategy state by itself.
    """

    symbol: Symbol
    event_type: str
    observed_fraction: Decimal
    threshold_fraction: Decimal
    occurred_at: datetime
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            msg = "symbol must be Symbol"
            raise RiskGateError(msg)
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            msg = "event_type must not be empty"
            raise RiskGateError(msg)
        for name in ("observed_fraction", "threshold_fraction"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                msg = f"{name} must be a finite Decimal"
                raise RiskGateError(msg)
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            msg = "occurred_at must be timezone-aware UTC"
            raise RiskGateError(msg)
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            msg = "reason_codes must be a non-empty tuple"
            raise RiskGateError(msg)
        if any(
            not isinstance(reason_code, str) or not reason_code for reason_code in self.reason_codes
        ):
            msg = "reason_codes must contain non-empty strings"
            raise RiskGateError(msg)


def detect_single_day_disaster(
    *,
    symbol: Symbol,
    previous_close: Decimal,
    current_close: Decimal,
    occurred_at: datetime,
    threshold_fraction: Decimal = DEFAULT_DISASTER_DROP_FRACTION,
) -> RiskEvent | None:
    """Return a disaster event when a single close-to-close drop breaches the threshold."""

    _require_positive_decimal("previous_close", previous_close)
    _require_positive_decimal("current_close", current_close)
    _require_positive_decimal("threshold_fraction", threshold_fraction)
    if threshold_fraction > Decimal("1"):
        msg = "threshold_fraction must be at most 1"
        raise RiskGateError(msg)

    drop_fraction = (previous_close - current_close) / previous_close
    if drop_fraction < threshold_fraction:
        return None
    return RiskEvent(
        symbol=symbol,
        event_type=DISASTER_SINGLE_DAY_DROP,
        observed_fraction=drop_fraction,
        threshold_fraction=threshold_fraction,
        occurred_at=occurred_at,
        reason_codes=(DISASTER_SINGLE_DAY_DROP, REEVALUATE_REQUIRED),
    )


def detect_multi_session_disaster(
    *,
    symbol: Symbol,
    closes: Sequence[Decimal],
    occurred_at: datetime,
    sessions: int = DEFAULT_MULTI_SESSION_COUNT,
    threshold_fraction: Decimal = DEFAULT_MULTI_SESSION_DROP_FRACTION,
) -> RiskEvent | None:
    """Return a disaster event when the last N sessions fell past the threshold.

    ``closes`` are ordered oldest → newest and must include at least
    ``sessions + 1`` values (the cumulative return spans N session moves).
    Shorter histories return None: warmup is not a disaster.
    """

    if sessions <= 0:
        msg = "sessions must be positive"
        raise RiskGateError(msg)
    _require_positive_decimal("threshold_fraction", threshold_fraction)
    if threshold_fraction > Decimal("1"):
        msg = "threshold_fraction must be at most 1"
        raise RiskGateError(msg)
    if len(closes) < sessions + 1:
        return None
    window = closes[-(sessions + 1) :]
    for value in window:
        _require_positive_decimal("close", value)
    drop_fraction = (window[0] - window[-1]) / window[0]
    if drop_fraction < threshold_fraction:
        return None
    return RiskEvent(
        symbol=symbol,
        event_type=DISASTER_MULTI_SESSION_DROP,
        observed_fraction=drop_fraction,
        threshold_fraction=threshold_fraction,
        occurred_at=occurred_at,
        reason_codes=(DISASTER_MULTI_SESSION_DROP, REEVALUATE_REQUIRED),
    )


def detect_limit_day_open(
    *,
    symbol: Symbol,
    prior_close: Decimal,
    open_price: Decimal,
    occurred_at: datetime,
    limit_fraction: Decimal = DEFAULT_LIMIT_DAY_FRACTION,
) -> RiskEvent | None:
    """Flag an execution-day open in ±10% limit territory versus prior close.

    Annotation only: fills proceed normally, but the operator must see that
    the modeled fill landed on a day where real liquidity may have been a
    one-sided queue (2025-04-07 precedent: 0050 locked limit-down).
    """

    if not is_limit_day_move(
        reference_price=prior_close, observed_price=open_price, limit_fraction=limit_fraction
    ):
        return None
    move = abs(open_price - prior_close) / prior_close
    return RiskEvent(
        symbol=symbol,
        event_type=LIMIT_DAY,
        observed_fraction=move,
        threshold_fraction=limit_fraction,
        occurred_at=occurred_at,
        reason_codes=(LIMIT_DAY,),
    )


def is_limit_day_move(
    *,
    reference_price: Decimal,
    observed_price: Decimal,
    limit_fraction: Decimal = DEFAULT_LIMIT_DAY_FRACTION,
) -> bool:
    """True when a price sits in ±10% limit territory versus its reference.

    The exact limit price is tick-rounded by the exchange, so this uses a
    conservative 9.5% band. Callers annotate fills/health with ``LIMIT_DAY``;
    execution proceeds normally (the deferred-fill model is a registered
    experiment, not MVP behavior).
    """

    _require_positive_decimal("reference_price", reference_price)
    _require_positive_decimal("observed_price", observed_price)
    _require_positive_decimal("limit_fraction", limit_fraction)
    move = abs(observed_price - reference_price) / reference_price
    return move >= limit_fraction


def _require_positive_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        msg = f"{name} must be a finite Decimal"
        raise RiskGateError(msg)
    if value <= Decimal("0"):
        msg = f"{name} must be positive"
        raise RiskGateError(msg)
