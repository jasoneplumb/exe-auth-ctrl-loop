"""
Intent: Make the authorization record tamper-evident, so "why was this allowed?" has an
        answer that cannot be quietly rewritten afterwards
Context: pipeline.py appends proposal and execution events, and wires the executor so that
        every decision -- audit draw included -- is committed here before a token is issued
        or a handler reached (docs/v2/audit-commitment.md). That ordering is what the
        audit-selection claim rests on, together with a trusted RNG and controller.
Pattern: Hash chain -- each event covers the previous event's hash, so altering any earlier
        entry invalidates every entry after it
Future: In-memory and single-process. verify() proves internal consistency only: a party
        who can rewrite the whole chain, or drop its tail, produces a chain that verifies.
        anchor()/verify_anchor() show what an externally held head hash would add; they
        are a demonstration, not part of this prototype's guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .authority import Decision, Proposal, digest, utcnow


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

    def commit_decision(self, decision: Decision, proposal: Proposal) -> LedgerEvent:
        """
        intent: Put the decision, with its audit draw, on the record before anything acts
        context: Called by the executor between evaluate() and issue(). If this raises,
                 the executor refuses the step, so no handler runs on an uncommitted
                 decision (docs/v2/audit-commitment.md, event-order contract).
        """
        return self.append(
            "authority.decision.committed",
            decision.proposal_id,
            "controller",
            {
                "decision_id": decision.decision_id,
                "proposal_digest": proposal.proposal_digest,
                "route": decision.route.value,
                "reason_codes": list(decision.reason_codes),
                "evidence_id": decision.evidence_id,
                "evidence_version": decision.evidence_version,
                "lower_bound": decision.lower_bound,
                "required_bound": decision.required_bound,
                "audit_probability": decision.audit_probability,
                "audit_draw": decision.audit_draw,
                "decided_at": decision.decided_at.isoformat(),
            },
            decision.decided_at,
        )

    def anchor(self) -> tuple[int, str]:
        """
        intent: Name the current head so it can be stored somewhere this process cannot write
        effect: (sequence, event_hash) of the last event, or (-1, "GENESIS") when empty
        constraint: Demonstration only. The value is meaningful if and only if it is held
                    outside the controller's trust domain; in this process it proves nothing.
        """
        if not self.events:
            return (-1, "GENESIS")
        head = self.events[-1]
        return (head.sequence, head.event_hash)

    def verify_anchor(self, sequence: int, event_hash: str) -> bool:
        """
        intent: Check that an externally held head is still part of this chain
        effect: Detects truncation below the anchor and any rewrite at or before it, which
                verify() alone cannot. Events appended after the anchor are covered by
                verify() only until the next anchor is taken.
        """
        if not self.verify():
            return False
        if sequence < 0:
            return True
        if sequence >= len(self.events):
            return False
        return self.events[sequence].event_hash == event_hash
