"""
Intent: Make the authorization record tamper-evident, so "why was this allowed?" has an
        answer that cannot be quietly rewritten afterwards
Context: pipeline.py appends proposal, decision, and execution events; the committed audit
        draw lands here before any outcome exists, which is what makes audit selection
        independent of results
Pattern: Hash chain -- each event covers the previous event's hash, so altering any earlier
        entry invalidates every entry after it
Future: In-memory and single-process. Production needs durable append-only storage, and
        ideally external anchoring, since a chain an attacker can rewrite wholesale proves
        only internal consistency.
"""

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
    """
    intent: Append-only history of what was decided and what ran
    constraint: No update or delete method exists, and none should be added -- the value of
                the chain is that the only legal operation is append
    """

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
        """
        intent: Add one event and link it to everything that came before
        method: Hash the event body together with the previous event's hash
        effect: Editing event 3 changes its hash, which breaks the link event 4 recorded --
                so tampering is detectable at verify() rather than silent
        """
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
        """
        intent: Re-derive every hash and confirm the chain still holds
        constraint: Detects edits to a retained chain. It cannot detect truncation of the
                    tail or wholesale replacement, because a shorter chain re-derived from
                    GENESIS is internally consistent -- that needs external anchoring.
        """
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
