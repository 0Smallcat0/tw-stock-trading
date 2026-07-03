"""Notification delivery channels for the signal runtime."""

from __future__ import annotations

import httpx

from src.notify.types import NotificationEvent, NotificationValidationError


class CollectingNotificationChannel:
    """In-memory channel: the dashboard and replay smoke read from here."""

    def __init__(self) -> None:
        self._delivered: list[NotificationEvent] = []

    @property
    def delivered(self) -> tuple[NotificationEvent, ...]:
        return tuple(self._delivered)

    def deliver(self, event: NotificationEvent) -> None:
        self._delivered.append(event)


class WebhookNotificationChannel:
    """Config-gated webhook push (e.g. Discord/Telegram bridge endpoint).

    Advisory JSON only; delivery failures are the caller's to log — the event
    is already persisted before any delivery attempt.
    """

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        if not url.startswith("https://"):
            msg = "webhook url must use https"
            raise NotificationValidationError(msg)
        self._url = url
        self._timeout_seconds = timeout_seconds

    def deliver(self, event: NotificationEvent) -> None:
        response = httpx.post(self._url, json=event.to_json_dict(), timeout=self._timeout_seconds)
        # A 4xx/5xx response is a failed delivery, not a delivered one.
        response.raise_for_status()
