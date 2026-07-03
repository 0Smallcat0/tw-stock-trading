from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.notify import (
    DECREASE_EXPOSURE,
    INCREASE_EXPOSURE,
    CollectingNotificationChannel,
    NotificationEvent,
    NotificationValidationError,
    WebhookNotificationChannel,
    ladder_notification_id,
)

_DECISION_TIME = datetime(2026, 7, 1, 23, 59, 59, 999000, tzinfo=UTC)


def _event(
    *,
    previous: str = "0",
    target: str = "0.5",
    action: str = INCREASE_EXPOSURE,
    delta: str | None = None,
) -> NotificationEvent:
    previous_fraction = Decimal(previous)
    target_fraction = Decimal(target)
    return NotificationEvent(
        notification_id=ladder_notification_id(
            namespace="paper-runtime",
            symbol_value="BTCUSDT",
            decision_time=_DECISION_TIME,
            previous_fraction=previous_fraction,
            target_fraction=target_fraction,
        ),
        symbol_value="BTCUSDT",
        action=action,
        previous_fraction=previous_fraction,
        target_fraction=target_fraction,
        delta_fraction=Decimal(delta) if delta else abs(target_fraction - previous_fraction),
        decision_price=Decimal("50000"),
        decision_time=_DECISION_TIME,
        reason_codes=("ABOVE_SMA_20", "LADDER_UP"),
        risk_status="OK",
        created_at=_DECISION_TIME,
    )


def test_notification_id_is_deterministic_and_transition_specific() -> None:
    first = ladder_notification_id(
        namespace="paper-runtime",
        symbol_value="BTCUSDT",
        decision_time=_DECISION_TIME,
        previous_fraction=Decimal("0"),
        target_fraction=Decimal("0.5"),
    )
    same = ladder_notification_id(
        namespace="paper-runtime",
        symbol_value="BTCUSDT",
        decision_time=_DECISION_TIME,
        previous_fraction=Decimal("0"),
        target_fraction=Decimal("0.5"),
    )
    different = ladder_notification_id(
        namespace="paper-runtime",
        symbol_value="BTCUSDT",
        decision_time=_DECISION_TIME,
        previous_fraction=Decimal("0.5"),
        target_fraction=Decimal("0.25"),
    )

    assert first == same
    assert first != different


def test_notification_requires_a_ladder_change() -> None:
    with pytest.raises(NotificationValidationError, match="ladder change"):
        _event(previous="0.5", target="0.5")


def test_notification_action_must_match_direction() -> None:
    with pytest.raises(NotificationValidationError, match="direction"):
        _event(previous="0.5", target="0.25", action=INCREASE_EXPOSURE)

    event = _event(previous="0.5", target="0.25", action=DECREASE_EXPOSURE)
    assert event.delta_fraction == Decimal("0.25")


def test_notification_delta_must_match_transition() -> None:
    with pytest.raises(NotificationValidationError, match="delta_fraction"):
        _event(delta="0.75")


def test_notification_requires_utc_timestamps() -> None:
    with pytest.raises(NotificationValidationError, match="UTC"):
        NotificationEvent(
            notification_id="notify:x",
            symbol_value="BTCUSDT",
            action=INCREASE_EXPOSURE,
            previous_fraction=Decimal("0"),
            target_fraction=Decimal("0.25"),
            delta_fraction=Decimal("0.25"),
            decision_price=Decimal("100"),
            decision_time=datetime(2026, 7, 1),  # noqa: DTZ001 - naive on purpose
            reason_codes=("TEST",),
            risk_status="OK",
            created_at=_DECISION_TIME,
        )


def test_collecting_channel_records_deliveries() -> None:
    channel = CollectingNotificationChannel()
    event = _event()

    channel.deliver(event)

    assert channel.delivered == (event,)


def test_webhook_channel_requires_https() -> None:
    with pytest.raises(NotificationValidationError, match="https"):
        WebhookNotificationChannel("http://insecure.example/hook")


def test_notification_id_canonicalizes_equal_decimals() -> None:
    padded = ladder_notification_id(
        namespace="paper-runtime",
        symbol_value="BTCUSDT",
        decision_time=_DECISION_TIME,
        previous_fraction=Decimal("0.50"),
        target_fraction=Decimal("0.750"),
    )
    canonical = ladder_notification_id(
        namespace="paper-runtime",
        symbol_value="BTCUSDT",
        decision_time=_DECISION_TIME,
        previous_fraction=Decimal("0.5"),
        target_fraction=Decimal("0.75"),
    )

    assert padded == canonical


def test_webhook_channel_treats_http_errors_as_failed_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def _fake_post(url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(400, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", _fake_post)
    channel = WebhookNotificationChannel("https://hook.example/x")

    with pytest.raises(httpx.HTTPStatusError):
        channel.deliver(_event())
