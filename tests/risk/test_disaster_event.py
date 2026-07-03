from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.domain import Symbol
from src.risk import (
    DISASTER_SINGLE_DAY_DROP,
    REEVALUATE_REQUIRED,
    RiskEvent,
    RiskGateError,
    detect_single_day_disaster,
)

_OCCURRED_AT = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def _symbol() -> Symbol:
    return Symbol(value="0050", base_asset="0050", quote_asset="TWD")


def test_limit_down_class_single_day_drop_emits_disaster_event() -> None:
    # TW default threshold is -9% (limit-down territory under the ±10% cap).
    event = detect_single_day_disaster(
        symbol=_symbol(),
        previous_close=Decimal("100"),
        current_close=Decimal("90.3"),
        occurred_at=_OCCURRED_AT,
    )

    assert event is not None
    assert event.event_type == DISASTER_SINGLE_DAY_DROP
    assert event.observed_fraction == Decimal("0.097")
    assert event.threshold_fraction == Decimal("0.09")
    assert DISASTER_SINGLE_DAY_DROP in event.reason_codes
    assert REEVALUATE_REQUIRED in event.reason_codes


def test_drop_below_threshold_emits_nothing() -> None:
    event = detect_single_day_disaster(
        symbol=_symbol(),
        previous_close=Decimal("100"),
        current_close=Decimal("95"),
        occurred_at=_OCCURRED_AT,
    )

    assert event is None


def test_rising_close_emits_nothing() -> None:
    event = detect_single_day_disaster(
        symbol=_symbol(),
        previous_close=Decimal("100"),
        current_close=Decimal("120"),
        occurred_at=_OCCURRED_AT,
    )

    assert event is None


def test_custom_threshold_is_respected() -> None:
    event = detect_single_day_disaster(
        symbol=_symbol(),
        previous_close=Decimal("100"),
        current_close=Decimal("90"),
        occurred_at=_OCCURRED_AT,
        threshold_fraction=Decimal("0.10"),
    )

    assert event is not None
    assert event.threshold_fraction == Decimal("0.10")


@pytest.mark.parametrize(
    ("previous_close", "current_close", "threshold"),
    (
        (Decimal("0"), Decimal("80"), Decimal("0.2")),
        (Decimal("100"), Decimal("0"), Decimal("0.2")),
        (Decimal("100"), Decimal("80"), Decimal("0")),
        (Decimal("100"), Decimal("80"), Decimal("1.5")),
    ),
)
def test_invalid_disaster_inputs_are_rejected(
    previous_close: Decimal, current_close: Decimal, threshold: Decimal
) -> None:
    with pytest.raises(RiskGateError):
        detect_single_day_disaster(
            symbol=_symbol(),
            previous_close=previous_close,
            current_close=current_close,
            occurred_at=_OCCURRED_AT,
            threshold_fraction=threshold,
        )


def test_risk_event_requires_utc_timestamp() -> None:
    with pytest.raises(RiskGateError, match="UTC"):
        RiskEvent(
            symbol=_symbol(),
            event_type=DISASTER_SINGLE_DAY_DROP,
            observed_fraction=Decimal("0.25"),
            threshold_fraction=Decimal("0.20"),
            occurred_at=datetime(2026, 7, 1, 0, 0),  # noqa: DTZ001 - naive on purpose
            reason_codes=(DISASTER_SINGLE_DAY_DROP,),
        )


def test_multi_session_slide_emits_disaster_event() -> None:
    from src.risk import DISASTER_MULTI_SESSION_DROP, detect_multi_session_disaster

    # 2025-04 tariff-crash shape: ~-18% over three sessions, each inside ±10%.
    closes = (
        Decimal("100"),
        Decimal("100"),
        Decimal("90.3"),
        Decimal("85"),
        Decimal("82"),
    )
    event = detect_multi_session_disaster(symbol=_symbol(), closes=closes, occurred_at=_OCCURRED_AT)

    assert event is not None
    assert event.event_type == DISASTER_MULTI_SESSION_DROP
    assert event.observed_fraction == Decimal("0.18")
    assert event.threshold_fraction == Decimal("0.15")


def test_multi_session_slide_below_threshold_or_short_history_emits_nothing() -> None:
    from src.risk import detect_multi_session_disaster

    mild = (Decimal("100"), Decimal("97"), Decimal("94"), Decimal("92"))
    assert (
        detect_multi_session_disaster(symbol=_symbol(), closes=mild, occurred_at=_OCCURRED_AT)
        is None
    )
    short = (Decimal("100"), Decimal("82"))
    assert (
        detect_multi_session_disaster(symbol=_symbol(), closes=short, occurred_at=_OCCURRED_AT)
        is None
    )


def test_limit_day_move_detection() -> None:
    from src.risk import is_limit_day_move

    assert is_limit_day_move(
        reference_price=Decimal("848"), observed_price=Decimal("763.5")
    )  # 2330 2025-04-07 shape (-9.96%)
    assert is_limit_day_move(reference_price=Decimal("100"), observed_price=Decimal("109.6"))
    assert not is_limit_day_move(reference_price=Decimal("100"), observed_price=Decimal("95"))
