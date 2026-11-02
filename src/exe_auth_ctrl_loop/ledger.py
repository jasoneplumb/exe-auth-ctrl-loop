"""
Intent: Make the authorization record tamper-evident, so "why was this allowed?" has an
        answer that cannot be quietly rewritten afterwards
Context: pipeline.py appends proposal and execution events, and wires the executor so that
        every decision -- audit draw included -- is committed here before a token is issued
        or a handler reached (docs/v2/audit-commitment.md). That ordering is what the
        audit-selection claim rests on, together with a trusted RNG and controller.
Pattern: Hash chain -- each event covers the previous event's hash, so altering any earlier
        entry invalidates every entry after it
Future: Durable since v3 Step 02: events are rows of the append-only `ledger` table, so a
        restart keeps them and UPDATE/DELETE are refused by the database. verify() still
        proves internal consistency only -- someone who can write the file with the
        triggers dropped can rewrite the chain or drop its tail and produce a chain that
        verifies (C22). The keyed chain and the anchor table are v3 Step 08.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .authority import Decision, Proposal, digest, utcnow
from .store import Store, canonical_json, time_text


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


def _body(
    sequence: int,
    event_type: str,
    aggregate_id: str,
    actor: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
    previous_hash: str,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "event_type": event_type,
        "aggregate_id": aggregate_id,
        "actor": actor,
        "occurred_at": occurred_at.isoformat(),
        "payload": payload,
        "previous_hash": previous_hash,
    }


def _event(row: tuple[Any, ...]) -> LedgerEvent:
    sequence, event_type, aggregate_id, actor, occurred_at, payload, previous, event_hash = row
    return LedgerEvent(
        sequence=sequence,
        event_type=event_type,
        aggregate_id=aggregate_id,
        actor=actor,
        occurred_at=datetime.fromisoformat(occurred_at),
        payload=json.loads(payload),
        previous_hash=previous,
        event_hash=event_hash,
    )


_COLUMNS = (
    "sequence, event_type, aggregate_id, actor, occurred_at, payload, previous_hash, event_hash"
)


class EventLedger:
    """
    intent: Append-only history of what was decided and what ran
    constraint: No update or delete method exists, and none should be added -- the value of
                the chain is that the only legal operation is append. The table's triggers
                refuse UPDATE and DELETE from any other path too.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def events(self) -> list[LedgerEvent]:
        """Every event, in sequence order, read from the database (a fresh list each call)."""
        rows = self.store.connection().execute(
            f"SELECT {_COLUMNS} FROM ledger ORDER BY sequence"
        ).fetchall()
        return [_event(row) for row in rows]

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
        method: Hash the event body together with the previous event's hash. The read of
                the head and the insert are one transaction, so two appenders (threads or
                processes) cannot both link to the same predecessor.
        effect: Editing event 3 changes its hash, which breaks the link event 4 recorded --
                so tampering is detectable at verify() rather than silent
        constraint: The payload is stored in exactly the canonical encoding digest() hashes,
                    so the event returned here equals the one read back later, and verify()
                    re-derives the same hash from the stored form.
        """
        when = occurred_at or utcnow()
        encoded = canonical_json(dict(payload))
        with self.store.transaction() as conn:
            # constraint: the actor is an active registered principal (spec §8, v3 Step 05),
            # checked in the same transaction as the append, so a deactivation committed
            # first is seen. The host's own service principals exist by schema.
            known = conn.execute(
                "SELECT active FROM principals WHERE principal_id = ?", (actor,)
            ).fetchone()
            if known is None or not known[0]:
                raise PermissionError(
                    f"ledger actor {actor!r} is not an active registered principal"
                )
            head = conn.execute(
                "SELECT sequence, event_hash FROM ledger ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence, previous = (head[0] + 1, head[1]) if head else (0, "GENESIS")
            stored = json.loads(encoded)
            event_hash = digest(
                _body(sequence, event_type, aggregate_id, actor, when, stored, previous)
            )
            conn.execute(
                f"INSERT INTO ledger ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sequence, event_type, aggregate_id, actor, time_text(when), encoded,
                 previous, event_hash),
            )
        return LedgerEvent(
            sequence, event_type, aggregate_id, actor, when, stored, previous, event_hash
        )

    def verify(self) -> bool:
        """
        intent: Re-derive every hash and confirm the chain still holds
        constraint: Detects edits to a retained chain. It cannot detect truncation of the
                    tail or wholesale replacement, because a shorter chain re-derived from
                    GENESIS is internally consistent -- that needs external anchoring.
        """
        previous = "GENESIS"
        for event in self.events:
            body = _body(
                event.sequence, event.event_type, event.aggregate_id, event.actor,
                event.occurred_at, event.payload, previous,
            )
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
        effect: Since v3 Step 04 this also writes the decision's row in `decisions`, in the
                same transaction as the event. ExecutionGateway.issue() refuses a decision
                without that row, so commit-before-issue is enforced by the gateway, not by
                the caller's convention (C20), and a Decision built by hand cannot be
                issued against (L7).
        constraint: A decision is committed once; a second commit of the same id fails on
                    the table's primary key and appends nothing.
        """
        # constraint: atomic because Store.transaction() is re-entrant on a thread: the
        # append inside _append_decision joins this transaction on the same connection, so
        # a failed INSERT below rolls the ledger event back with it
        with self.store.transaction() as conn:
            event = self._append_decision(decision, proposal)
            conn.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?)",
                (decision.decision_id, proposal.proposal_digest, decision.route.value,
                 event.sequence),
            )
        return event

    def _append_decision(self, decision: Decision, proposal: Proposal) -> LedgerEvent:
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
        head = self.store.connection().execute(
            "SELECT sequence, event_hash FROM ledger ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return (-1, "GENESIS") if head is None else (head[0], head[1])

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
        row = self.store.connection().execute(
            "SELECT event_hash FROM ledger WHERE sequence = ?", (sequence,)
        ).fetchone()
        return row is not None and row[0] == event_hash
