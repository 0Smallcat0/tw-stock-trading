"""Append-only JSONL runtime event store with idempotency keys.

Every runtime side effect (notification, order, fill, cycle state) persists
here BEFORE it happens; duplicate keys are refused, which is what makes a
restart unable to re-send or re-execute anything.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class RuntimeStoreError(ValueError):
    """Raised when the runtime event store cannot be used safely."""


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """One persisted runtime event."""

    kind: str
    key: str
    recorded_at: datetime
    payload: dict[str, object]


class JsonlEventStore:
    """File-backed append-only event store keyed for idempotency.

    ``durable_fsync=True`` fsyncs every append (the live daily cycle wants
    this; high-volume replays do not). A torn FINAL line — the power-loss
    artifact of an interrupted append — is treated as never-persisted and
    preserved in a ``.torn`` sidecar; corruption anywhere else fails loudly.
    """

    def __init__(self, path: str | Path, *, durable_fsync: bool = False) -> None:
        self._path = Path(path)
        self._durable_fsync = durable_fsync
        self._events: list[StoredEvent] = []
        self._keys: set[str] = set()
        if self._path.exists():
            lines = self._path.read_text(encoding="utf-8").splitlines()
            last_content_index = max(
                (index for index, line in enumerate(lines) if line.strip()),
                default=-1,
            )
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    if line_number - 1 == last_content_index:
                        self._quarantine_torn_tail(line)
                        break
                    msg = f"{self._path}:{line_number} is not valid JSON"
                    raise RuntimeStoreError(msg) from exc
                event = _event_from_row(row, self._path, line_number)
                self._events.append(event)
                self._keys.add(event.key)

    def _quarantine_torn_tail(self, line: str) -> None:
        sidecar = self._path.with_suffix(self._path.suffix + ".torn")
        with sidecar.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        intact = "\n".join(
            json.dumps(
                {
                    "kind": event.kind,
                    "key": event.key,
                    "recorded_at": event.recorded_at.isoformat(),
                    "payload": event.payload,
                },
                sort_keys=True,
            )
            for event in self._events
        )
        self._path.write_text(intact + ("\n" if intact else ""), encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def all_events(self) -> tuple[StoredEvent, ...]:
        """Every persisted event in append order."""

        return tuple(self._events)

    def has(self, key: str) -> bool:
        return key in self._keys

    def append(
        self,
        *,
        kind: str,
        key: str,
        recorded_at: datetime,
        payload: dict[str, object],
    ) -> bool:
        """Persist one event; False means the key already exists (no-op)."""

        if not kind.strip() or not key.strip():
            msg = "kind and key must not be empty"
            raise RuntimeStoreError(msg)
        _require_utc("recorded_at", recorded_at)
        if key in self._keys:
            return False
        event = StoredEvent(kind=kind, key=key, recorded_at=recorded_at, payload=payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "kind": event.kind,
                        "key": event.key,
                        "recorded_at": event.recorded_at.isoformat(),
                        "payload": event.payload,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            if self._durable_fsync:
                handle.flush()
                os.fsync(handle.fileno())
        self._events.append(event)
        self._keys.add(key)
        return True

    def events_of_kind(self, kind: str) -> tuple[StoredEvent, ...]:
        return tuple(event for event in self._events if event.kind == kind)

    def latest_of_kind(self, kind: str) -> StoredEvent | None:
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None

    def count_of_kind(self, kind: str) -> int:
        return sum(1 for event in self._events if event.kind == kind)


def _event_from_row(row: object, path: Path, line_number: int) -> StoredEvent:
    if not isinstance(row, dict):
        msg = f"{path}:{line_number} must be a JSON object"
        raise RuntimeStoreError(msg)
    try:
        recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
        if recorded_at.tzinfo is None:
            msg = f"{path}:{line_number} recorded_at must be timezone-aware"
            raise RuntimeStoreError(msg)
        payload = row["payload"]
        if not isinstance(payload, dict):
            msg = f"{path}:{line_number} payload must be an object"
            raise RuntimeStoreError(msg)
        return StoredEvent(
            kind=str(row["kind"]),
            key=str(row["key"]),
            recorded_at=recorded_at.astimezone(UTC),
            payload=payload,
        )
    except KeyError as exc:
        msg = f"{path}:{line_number} is missing required event fields"
        raise RuntimeStoreError(msg) from exc


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        msg = f"{name} must be timezone-aware UTC"
        raise RuntimeStoreError(msg)
