"""
Intent: Say who did each thing, who may do what on whose behalf, and record every change to
        either (docs/v3/spec.md §8; finding F5)
Context: The ledger refuses an actor that is not an active principal here. The gateway asks
        this module whether a requesting principal holds a live grant whose scope admits the
        operation, and whether an approval is genuine; the lifecycle asks whether a release
        is signed by a human.
Pattern: Attenuation only. A grant is created by a human (root) or delegated by its holder
        to someone else with no more scope and no later expiry. Nothing widens authority.
        Every mutation is one transaction with exactly one ledger event of six types.
Future: Keys are HMAC secrets held by the host in a Keyring, never in the database. Stronger
        authentication (asymmetric keys, an external identity provider) is a production
        requirement (docs/v3/threat-model.md).
"""

from __future__ import annotations

import hmac
import json
import math
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from .authority import PartitionKey, Proposal, utcnow
from .ledger import EventLedger
from .revocation import GRANT_SUBTREE, HELD_GRANT_SUBTREES, revoke
from .store import Store, canonical_json, partition_text, time_text, time_us

PERMISSION_EVENTS = (
    "principal.registered",
    "principal.deactivated",
    "grant.created",
    "grant.revoked",
    "approval.recorded",
    "approval.rescinded",
)


class PrincipalKind(str, Enum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"
    MODEL = "MODEL"
    ORACLE = "ORACLE"


# constraint: kinds that sign things, and therefore must have a key
SIGNING_KINDS = frozenset({PrincipalKind.HUMAN, PrincipalKind.ORACLE})


class Keyring:
    """
    intent: The host's signing secrets, kept out of the database
    constraint: The database is reachable from the agent side (through the catalog's risk
                policy), and spec §1 forbids the agent side reaching a key. So keys live
                here, supplied at start-up, and only Principals holds this object.
    """

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        if any(not key for key in keys.values()):
            raise ValueError("empty signing key")
        self._keys = dict(keys)

    def __contains__(self, key_id: object) -> bool:
        return key_id in self._keys

    def __repr__(self) -> str:
        return f"Keyring({sorted(self._keys)})"

    def sign(self, key_id: str, payload: Mapping[str, Any]) -> str:
        return hmac.new(self._keys[key_id], canonical_json(payload).encode(), sha256).hexdigest()

    def verify(self, key_id: str, payload: Mapping[str, Any], mac: str) -> bool:
        if key_id not in self._keys or not isinstance(mac, str):
            return False
        return hmac.compare_digest(self.sign(key_id, payload), mac)


@dataclass(frozen=True)
class Principal:
    principal_id: str
    kind: PrincipalKind
    key_id: str | None
    active: bool


@dataclass(frozen=True)
class GrantScope:
    """
    intent: What a grant permits, in dimensions the host can check before issuing
    constraint: None in a set dimension means unrestricted. `ceilings` caps named numeric
                arguments of a single operation (e.g. {"usd": 100.0}); a proposal lacking
                a ceilinged argument, or carrying a non-finite one, is outside the scope.
                Cumulative budgets are RiskPolicy's, not a grant's.
    """

    tools: frozenset[str] | None = None
    effects: frozenset[str] | None = None
    risk_classes: frozenset[str] | None = None
    task_categories: frozenset[str] | None = None
    ceilings: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # constraint: frozen=True freezes the reference, not the dict; copy and wrap it so
        # a caller holding the original cannot widen a grant's ceilings after the fact
        object.__setattr__(self, "ceilings", MappingProxyType(dict(self.ceilings)))

    def within(self, parent: GrantScope) -> bool:
        """True iff this scope permits nothing the parent does not."""
        for mine, theirs in (
            (self.tools, parent.tools), (self.effects, parent.effects),
            (self.risk_classes, parent.risk_classes),
            (self.task_categories, parent.task_categories),
        ):
            if theirs is not None and (mine is None or not mine <= theirs):
                return False
        return all(
            name in self.ceilings and self.ceilings[name] <= limit
            for name, limit in parent.ceilings.items()
        )

    def blockers(self, proposal: Proposal) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.tools is not None and proposal.tool_name not in self.tools:
            reasons.append("GRANT_TOOL")
        if self.effects is not None and not proposal.requested_effects <= self.effects:
            reasons.append("GRANT_EFFECT")
        if self.risk_classes is not None and proposal.partition.risk_class not in self.risk_classes:
            reasons.append("GRANT_RISK_CLASS")
        if (self.task_categories is not None
                and proposal.partition.task_category not in self.task_categories):
            reasons.append("GRANT_TASK_CATEGORY")
        for name, limit in self.ceilings.items():
            value = proposal.parameters.get(name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value > limit):
                reasons.append(f"GRANT_CEILING:{name}")
        return tuple(reasons)

    def to_json(self) -> str:
        return canonical_json({
            "tools": _listed(self.tools), "effects": _listed(self.effects),
            "risk_classes": _listed(self.risk_classes),
            "task_categories": _listed(self.task_categories),
            "ceilings": dict(self.ceilings),
        })

    @classmethod
    def from_json(cls, text: str) -> GrantScope:
        raw = json.loads(text)
        return cls(
            _unlisted(raw["tools"]), _unlisted(raw["effects"]),
            _unlisted(raw["risk_classes"]), _unlisted(raw["task_categories"]),
            dict(raw["ceilings"]),
        )


@dataclass(frozen=True)
class Grant:
    grant_id: str
    parent_grant_id: str | None
    grantor: str
    grantee: str
    scope: GrantScope
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True)
class Approval:
    """
    intent: A human's consent to one proposal, usable for one token
    effect: The MAC covers the proposal digest and partition, so the approval cannot be
            moved to a different operation or configuration; the token table's unique
            approval_id makes it single use (owner decision 2026-11-02, spec §8 amended).
    """

    approval_id: str
    principal_id: str
    proposal_digest: str
    partition: PartitionKey
    expires_at: datetime
    mac: str

    def payload(self) -> dict[str, Any]:
        return approval_payload(
            self.approval_id, self.principal_id, self.proposal_digest, self.partition,
            self.expires_at,
        )


def approval_payload(
    approval_id: str, principal_id: str, proposal_digest: str, partition: PartitionKey,
    expires_at: datetime,
) -> dict[str, Any]:
    return {
        "action": "approve", "approval_id": approval_id, "principal_id": principal_id,
        "proposal_digest": proposal_digest, "partition": asdict(partition),
        "expires_at": time_text(expires_at),
    }


def root_grant_payload(
    grant_id: str, grantor: str, grantee: str, scope: GrantScope, expires_at: datetime,
) -> dict[str, Any]:
    return {
        "action": "grant", "grant_id": grant_id, "grantor": grantor, "grantee": grantee,
        "scope": scope.to_json(), "expires_at": time_text(expires_at),
    }


def release_payload(partition: PartitionKey, epoch: int, reason: str) -> dict[str, Any]:
    """The epoch is in the payload, so a signed release cannot be replayed at a later one."""
    return {"action": "release", "partition": asdict(partition), "epoch": epoch, "reason": reason}


def rescind_payload(approval_id: str) -> dict[str, Any]:
    return {"action": "rescind", "approval_id": approval_id}


class Principals:
    """
    intent: The one place principals, grants, and approvals change, each with its event
    constraint: Every method that changes state writes exactly one ledger event of
                PERMISSION_EVENTS in the same transaction (tests/test_principals.py
                enumerates them). Six are public; _record_approval is the gateway's. The
                database triggers refuse any other change to the three tables except a
                fresh insert, which only these methods make.
    """

    def __init__(self, store: Store, keyring: Keyring, clock: Any = utcnow) -> None:
        self.store = store
        self.keyring = keyring
        self.clock = clock
        self.ledger = EventLedger(store)

    # -- queries ---------------------------------------------------------------------

    def principal(self, principal_id: str) -> Principal | None:
        row = self.store.connection().execute(
            "SELECT kind, key_id, active FROM principals WHERE principal_id = ?", (principal_id,)
        ).fetchone()
        if row is None:
            return None
        return Principal(principal_id, PrincipalKind(row[0]), row[1], bool(row[2]))

    def grant(self, grant_id: str) -> Grant | None:
        row = self.store.connection().execute(
            "SELECT parent_grant_id, grantor, grantee, scope, expires_at, revoked"
            " FROM grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            return None
        parent, grantor, grantee, scope, expires_at, revoked = row
        return Grant(
            grant_id, parent, grantor, grantee, GrantScope.from_json(scope),
            datetime.fromisoformat(expires_at), bool(revoked),
        )

    def chain(self, grant_id: str) -> list[Grant]:
        """The grant and every ancestor, from the grant up to its root."""
        grants: list[Grant] = []
        current: str | None = grant_id
        while current is not None:
            grant = self.grant(current)
            if grant is None:
                raise KeyError(f"unknown grant {current}")
            grants.append(grant)
            current = grant.parent_grant_id
        return grants

    def lineage(self, token_id: str) -> list[Grant]:
        """The chain of grants a token was issued under, from its grant to the root."""
        row = self.store.connection().execute(
            "SELECT grant_id FROM tokens WHERE token_id = ?", (token_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise KeyError(f"no grant recorded for token {token_id}")
        return self.chain(row[0])

    def grant_blockers(
        self, grant_id: str, principal_id: str, proposal: Proposal | None = None,
    ) -> tuple[str, ...]:
        """
        intent: Every reason the principal cannot act under this grant now
        method: The requester must be the grant's grantee and active; every grant in the
                chain must be unrevoked and unexpired, and every grantor and grantee in it
                active; and with a proposal, every scope in the chain must admit it
        """
        reasons: list[str] = []
        requester = self.principal(principal_id)
        if requester is None or not requester.active:
            reasons.append("PRINCIPAL_INACTIVE")
        try:
            chain = self.chain(grant_id)
        except KeyError:
            return (*reasons, "GRANT_UNKNOWN")
        if chain[0].grantee != principal_id:
            reasons.append("GRANT_NOT_HELD")
        now = time_us(self.clock())
        for grant in chain:
            if grant.revoked:
                reasons.append("GRANT_REVOKED")
            if time_us(grant.expires_at) <= now:
                reasons.append("GRANT_EXPIRED")
            for party in (grant.grantor, grant.grantee):
                holder = self.principal(party)
                if holder is None or not holder.active:
                    reasons.append("GRANT_PARTY_INACTIVE")
            if proposal is not None:
                reasons.extend(grant.scope.blockers(proposal))
        return tuple(dict.fromkeys(reasons))

    def approval_blockers(self, approval: Approval, proposal: Proposal) -> tuple[str, ...]:
        """Every reason this approval does not authorize this proposal now."""
        reasons: list[str] = []
        human = self.principal(approval.principal_id)
        if human is None or human.kind is not PrincipalKind.HUMAN or not human.active:
            reasons.append("APPROVER_NOT_ACTIVE_HUMAN")
        elif not (human.key_id and self.keyring.verify(human.key_id, approval.payload(),
                                                       approval.mac)):
            reasons.append("APPROVAL_SIGNATURE")
        if approval.proposal_digest != proposal.proposal_digest:
            reasons.append("APPROVAL_OTHER_PROPOSAL")
        if approval.partition != proposal.partition:
            reasons.append("APPROVAL_OTHER_PARTITION")
        if time_us(approval.expires_at) <= time_us(self.clock()):
            reasons.append("APPROVAL_EXPIRED")
        if self.rescinded(approval.approval_id):
            reasons.append("APPROVAL_RESCINDED")
        return tuple(reasons)

    def rescinded(self, approval_id: str) -> bool:
        row = self.store.connection().execute(
            "SELECT rescinded FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return bool(row and row[0])

    def verify_human(self, principal_id: str, payload: Mapping[str, Any], mac: str) -> bool:
        human = self.principal(principal_id)
        return bool(
            human is not None and human.kind is PrincipalKind.HUMAN and human.active
            and human.key_id and self.keyring.verify(human.key_id, payload, mac)
        )

    # -- the six permission changes -------------------------------------------------------

    def register(
        self, principal_id: str, kind: PrincipalKind, *, by: str, key_id: str | None = None,
    ) -> Principal:
        """principal.registered. Humans and oracles need a key the keyring holds."""
        if not principal_id:
            raise ValueError("principal_id is required")
        kind = PrincipalKind(kind)
        if kind in SIGNING_KINDS and (key_id is None or key_id not in self.keyring):
            raise ValueError(f"a {kind.value} principal needs a key the keyring holds")
        with self.store.transaction() as conn:
            self._require_active(by, (PrincipalKind.SERVICE, PrincipalKind.HUMAN))
            if self.principal(principal_id) is not None:
                raise ValueError(f"principal already registered: {principal_id}")
            conn.execute(
                "INSERT INTO principals VALUES (?, ?, ?, 1)", (principal_id, kind.value, key_id)
            )
            self.ledger.append("principal.registered", principal_id, by,
                               {"kind": kind.value, "key_id": key_id})
        return Principal(principal_id, kind, key_id, True)

    def deactivate(self, principal_id: str, *, by: str) -> None:
        """principal.deactivated. Its grants and approvals stop working at the next check."""
        with self.store.transaction() as conn:
            self._require_active(by, (PrincipalKind.SERVICE, PrincipalKind.HUMAN))
            principal = self.principal(principal_id)
            if principal is None or not principal.active:
                raise ValueError(f"no active principal {principal_id}")
            conn.execute("UPDATE principals SET active = 0 WHERE principal_id = ?",
                         (principal_id,))
            self.ledger.append("principal.deactivated", principal_id, by, {})
            # RV-13: every token under a grant it holds (and their descendants) and every
            # token on an approval it gave, recorded as this one event with the grants
            held = [row[0] for row in conn.execute(
                "WITH RECURSIVE g(id) AS (SELECT grant_id FROM grants WHERE grantee = ?"
                " UNION SELECT grants.grant_id FROM grants JOIN g"
                " ON grants.parent_grant_id = g.id) SELECT id FROM g ORDER BY id",
                (principal_id,),
            )]
            revoke(
                self.store, "PRINCIPAL_DEACTIVATED",
                f"{HELD_GRANT_SUBTREES} OR approval_id IN"
                " (SELECT approval_id FROM approvals WHERE principal_id = ?)",
                (principal_id, principal_id), actor=by,
                details={"principal_id": principal_id, "grants": held},
            )

    def grant_root(
        self, grantor: str, grantee: str, scope: GrantScope, expires_at: datetime, mac: str,
        *, grant_id: str | None = None,
    ) -> Grant:
        """grant.created with no parent: only a human, and only with that human's signature."""
        grant_id = grant_id or f"grant-{secrets.token_hex(8)}"
        payload = root_grant_payload(grant_id, grantor, grantee, scope, expires_at)
        if not self.verify_human(grantor, payload, mac):
            raise PermissionError("a root grant needs a valid signature from an active human")
        return self._create(Grant(grant_id, None, grantor, grantee, scope, expires_at))

    def delegate(
        self, parent_grant_id: str, grantee: str, scope: GrantScope, expires_at: datetime,
        *, grant_id: str | None = None,
    ) -> Grant:
        """
        grant.created under a parent, by the parent's holder: no more scope, no later
        expiry, and only while the parent is live
        """
        with self.store.transaction():
            parent = self.grant(parent_grant_id)
            if parent is None:
                raise KeyError(f"unknown grant {parent_grant_id}")
            live = self.grant_blockers(parent_grant_id, parent.grantee)
            if live:
                raise PermissionError(f"parent grant is not live: {', '.join(live)}")
            if not scope.within(parent.scope):
                raise PermissionError("a delegated grant cannot be broader than its parent")
            if time_us(expires_at) > time_us(parent.expires_at):
                raise PermissionError("a delegated grant cannot outlive its parent")
            return self._create(Grant(
                grant_id or f"grant-{secrets.token_hex(8)}", parent_grant_id, parent.grantee,
                grantee, scope, expires_at,
            ))

    def revoke_grant(self, grant_id: str, *, by: str) -> None:
        """grant.revoked, by a grantor anywhere in the chain above or at this grant"""
        with self.store.transaction() as conn:
            chain = self.chain(grant_id)
            if by not in {g.grantor for g in chain}:
                raise PermissionError(f"{by} did not grant {grant_id} or any grant above it")
            self._require_active(by, tuple(PrincipalKind))
            if chain[0].revoked:
                raise ValueError(f"grant already revoked: {grant_id}")
            conn.execute("UPDATE grants SET revoked = 1 WHERE grant_id = ?", (grant_id,))
            self.ledger.append("grant.revoked", grant_id, by, {})
            # RV-12: tokens under this grant and every grant delegated from it
            revoke(self.store, "GRANT_REVOKED", GRANT_SUBTREE, (grant_id,), actor=by,
                   details={"grant_id": grant_id})

    def _record_approval(self, approval: Approval) -> None:
        """
        approval.recorded. Private to the gateway: ExecutionGateway.issue() calls it inside
        its issue transaction after approval_blockers() came back empty, and this
        transaction joins that one, so the approval row exists if and only if its token
        does. Called anywhere else it would mark an approval used with no token.
        """
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, 0)",
                (approval.approval_id, approval.principal_id, approval.proposal_digest,
                 partition_text(approval.partition), time_text(approval.expires_at)),
            )
            self.ledger.append("approval.recorded", approval.approval_id, approval.principal_id,
                               {"proposal_digest": approval.proposal_digest})

    def rescind_approval(self, approval: Approval, mac: str) -> None:
        """approval.rescinded, signed by the approving human; works before or after use"""
        if not self.verify_human(approval.principal_id, rescind_payload(approval.approval_id),
                                 mac):
            raise PermissionError("a rescission needs the approver's valid signature")
        with self.store.transaction() as conn:
            if self.rescinded(approval.approval_id):
                raise ValueError(f"approval already rescinded: {approval.approval_id}")
            conn.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, 1)"
                " ON CONFLICT (approval_id) DO UPDATE SET rescinded = 1",
                (approval.approval_id, approval.principal_id, approval.proposal_digest,
                 partition_text(approval.partition), time_text(approval.expires_at)),
            )
            self.ledger.append("approval.rescinded", approval.approval_id,
                               approval.principal_id, {})
            # RV-11: the token issued on this approval, if it is still unspent
            revoke(self.store, "APPROVAL_RESCINDED", "approval_id = ?",
                   (approval.approval_id,), actor=approval.principal_id,
                   details={"approval_id": approval.approval_id})

    # -- internals -------------------------------------------------------------------

    def _create(self, grant: Grant) -> Grant:
        with self.store.transaction() as conn:
            self._require_active(grant.grantor, tuple(PrincipalKind))
            grantee = self.principal(grant.grantee)
            if grantee is None or not grantee.active:
                raise ValueError(f"no active principal {grant.grantee}")
            conn.execute(
                "INSERT INTO grants VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (grant.grant_id, grant.parent_grant_id, grant.grantor, grant.grantee,
                 grant.scope.to_json(), time_text(grant.expires_at), time_us(grant.expires_at)),
            )
            self.ledger.append("grant.created", grant.grant_id, grant.grantor, {
                "parent_grant_id": grant.parent_grant_id, "grantee": grant.grantee,
                "scope": json.loads(grant.scope.to_json()),
                "expires_at": time_text(grant.expires_at),
            })
        return grant

    def _require_active(self, principal_id: str, kinds: tuple[PrincipalKind, ...]) -> None:
        principal = self.principal(principal_id)
        if principal is None or not principal.active or principal.kind not in kinds:
            allowed = ", ".join(k.value for k in kinds)
            raise PermissionError(f"{principal_id} is not an active principal of kind {allowed}")


def _listed(values: frozenset[str] | None) -> list[str] | None:
    return None if values is None else sorted(values)


def _unlisted(values: list[str] | None) -> frozenset[str] | None:
    return None if values is None else frozenset(values)
