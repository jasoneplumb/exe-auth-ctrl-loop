"""
Intent: When an event takes authority away, take it away from every outstanding token at the
        same instant, not at the next evaluation (docs/v3/spec.md §5; finding F4)
Context: Each revoking event in docs/v3/revocation_fixture.json is recorded by its own module
        (lifecycle, evidence store, risk policy, principals) and calls revoke() inside the
        transaction that records it. The two policy events are recorded here, by
        PolicyAuthority, which is the host's one holder of the current policy.
Pattern: Revoke-in-transaction. The event, the revocations, and one `authority.revoked`
        ledger record commit together or not at all. The gateway's redemption checks stay
        as a second line, so a scope this module misses is still caught at redemption.
Future: Scopes are SQL over the token table, written here, never taken from input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Sequence

from .authority import AuthorizationToken, Policy, Proposal, select_tokens
from .ledger import EventLedger
from .store import Store

if TYPE_CHECKING:  # lifecycle.py imports this module
    from .authority import AuthorityController
    from .lifecycle import LifecycleManager

REVOKED_EVENT = "authority.revoked"

# SQL fragments for the scopes in the fixture. Each takes one parameter.
LINEAGE_DESCENDANTS = (
    "partition IN (WITH RECURSIVE d(p) AS (SELECT ? UNION "
    "SELECT successor FROM lineage JOIN d ON lineage.predecessor = d.p) SELECT p FROM d)"
)
GRANT_SUBTREE = (
    "grant_id IN (WITH RECURSIVE g(id) AS (SELECT ? UNION "
    "SELECT grants.grant_id FROM grants JOIN g ON grants.parent_grant_id = g.id) "
    "SELECT id FROM g)"
)
HELD_GRANT_SUBTREES = (
    "grant_id IN (WITH RECURSIVE g(id) AS (SELECT grant_id FROM grants WHERE grantee = ? "
    "UNION SELECT grants.grant_id FROM grants JOIN g ON grants.parent_grant_id = g.id) "
    "SELECT id FROM g)"
)


@dataclass(frozen=True)
class Revocation:
    """What one revoking event did: the tokens it revoked and the ones it could not."""

    event: str
    revoked: tuple[str, ...]
    effect_unknown: tuple[str, ...]
    details: dict[str, Any] = field(default_factory=dict)


def revoke(
    store: Store,
    event: str,
    where: str,
    params: Sequence[Any] = (),
    *,
    evidence_only: bool = False,
    refuses: Callable[[AuthorizationToken, Proposal | None], bool] | None = None,
    details: dict[str, Any] | None = None,
    actor: str = "controller",
) -> Revocation:
    """
    intent: Revoke every issued token in an event's scope, inside the caller's transaction
    method: Select the scope's tokens in state `issued` or `handler_started`; narrow to
            those not issued on an approval when `evidence_only`, and to those `refuses`
            accepts when given; revoke the issued ones with the event as the reason; list
            the started ones; append one `authority.revoked` record.
    effect: Joins the caller's transaction (Store.transaction is re-entrant), so the event
            and its revocations commit together; a failure anywhere rolls back both.
    constraint: A token in `handler_started` has been consumed; it cannot be revoked and is
                never re-run. It is reported here and by ExecutionGateway.effect_unknown().
    """
    condition = f"({where}) AND state IN ('issued', 'handler_started')"
    if evidence_only:
        condition += " AND approval_id IS NULL"
    with store.transaction() as conn:
        scoped = [
            token for token, proposal in select_tokens(conn, condition, params)
            if refuses is None or refuses(token, proposal)
        ]
        revoked = tuple(t.token_id for t in scoped if t.state == "issued")
        started = tuple(t.token_id for t in scoped if t.state == "handler_started")
        for token_id in revoked:
            conn.execute(
                "UPDATE tokens SET state = 'revoked', revocation_reason = ?"
                " WHERE token_id = ? AND state = 'issued'",
                (event, token_id),
            )
        record = Revocation(event, revoked, started, dict(details or {}))
        EventLedger(store).append(REVOKED_EVENT, event, actor, {
            "event": event, "revoked": list(revoked), "effect_unknown": list(started),
            **record.details,
        })
    return record


class PolicyAuthority:
    """
    intent: The host's one holder of the current Policy, so a policy change is an event
    effect: change() records POLICY_CHANGED (RV-07) for a new version or EFFECT_PROHIBITED
            (RV-08) for a grown prohibited set under the same version, revoking in the same
            transaction; a looser policy under the same version revokes nothing.
    constraint: Give the gateway `current_policy=authority.current`, and pass the controller
                and lifecycle managers here so they switch policy in the same transaction.
                A policy object swapped in any other way is caught only lazily (at the
                next refresh and at redemption), which is what this class exists to avoid.
    """

    def __init__(
        self,
        store: Store,
        policy: Policy,
        *,
        controllers: Sequence[AuthorityController] = (),
        managers: Sequence[LifecycleManager] = (),
    ) -> None:
        self.store = store
        self._policy = policy
        self.controllers = tuple(controllers)
        self.managers = tuple(managers)

    def current(self) -> Policy:
        return self._policy

    def change(self, policy: Policy, *, by: str = "controller") -> Revocation | None:
        old = self._policy
        with self.store.transaction():
            if policy.version != old.version:
                retired: list[str] = []
                for manager in self.managers:
                    retired.extend(manager.retire_policy_version(old.version, policy))
                result: Revocation | None = revoke(
                    self.store, "POLICY_CHANGED", "policy_version = ?", (old.version,),
                    details={"from": old.version, "to": policy.version,
                             "retired_partitions": retired},
                    actor=by,
                )
            elif policy.prohibited_effects - old.prohibited_effects:
                added = policy.prohibited_effects - old.prohibited_effects
                result = revoke(
                    self.store, "EFFECT_PROHIBITED", "policy_version = ?", (old.version,),
                    refuses=lambda token, _: bool(token.allowed_effects & added),
                    details={"prohibited": sorted(added)},
                    actor=by,
                )
            else:
                result = None  # looser, or equal: nothing outstanding loses authority
            self._policy = policy
            for controller in self.controllers:
                controller.policy = policy
            for manager in self.managers:
                manager.policy = policy
        return result


def successor_key(text: str, policy_version: str) -> str:
    """The partition text a key becomes under another policy version."""
    raw = json.loads(text)
    raw["policy_version"] = policy_version
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))

