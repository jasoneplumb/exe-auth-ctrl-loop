from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .authority import digest, utcnow


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    event_type: str
    aggregate_id: str
    actor: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    previous_hash: str
    event_hash: str


class EventLedger:
    """In-memory hash chain; replace with durable append-only storage in production."""

    def __init__(self) -> None:
        self.events: list[LedgerEvent] = []

    def append(
        self,
        event_type: str,
        aggregate_id: str,
        actor: str,
        payload: Mapping[str, Any],
        occurred_at: datetime | None = None,
    ) -> LedgerEvent:
        when = occurred_at or utcnow()
        previous = self.events[-1].event_hash if self.events else "GENESIS"
        body = {
            "sequence": len(self.events),
            "event_type": event_type,
            "aggregate_id": aggregate_id,
            "actor": actor,
            "occurred_at": when.isoformat(),
            "payload": payload,
            "previous_hash": previous,
        }
        event = LedgerEvent(
            sequence=len(self.events),
            event_type=event_type,
            aggregate_id=aggregate_id,
            actor=actor,
            occurred_at=when,
            payload=dict(payload),
            previous_hash=previous,
            event_hash=digest(body),
        )
        self.events.append(event)
        return event

    def verify(self) -> bool:
        previous = "GENESIS"
        for event in self.events:
            body = {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "actor": event.actor,
                "occurred_at": event.occurred_at.isoformat(),
                "payload": event.payload,
                "previous_hash": previous,
            }
            if event.previous_hash != previous or event.event_hash != digest(body):
                return False
            previous = event.event_hash
        return True
