"""Test support: temporary durable stores, and tampering as a writer of the file can."""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, Sequence

from exe_auth_ctrl_loop.authority import (
    Decision,
    ExecutionGateway,
    Policy,
    Route,
    utcnow,
)
from exe_auth_ctrl_loop.ledger import EventLedger, LedgerEvent
from exe_auth_ctrl_loop.principals import (
    Approval,
    GrantScope,
    Keyring,
    PrincipalKind,
    Principals,
    approval_payload,
    release_payload,
    root_grant_payload,
)
from exe_auth_ctrl_loop.shadow import (
    Oracle,
    OracleKind,
    ShadowEvidenceLog,
    SignedLabel,
    label_payload,
)
from exe_auth_ctrl_loop.store import Store, canonical_json, time_text

# The ledger key every test store uses (v3 Step 08: the hash chain is keyed). Subprocess
# workers that cannot import this module use the same literal.
TEST_LEDGER_KEY = b"test-ledger-key"

# Every store opened through scratch_store(), closed after each test by conftest.py.
OPEN: list[tuple[Store, tempfile.TemporaryDirectory[str]]] = []


def scratch_store() -> Store:
    """A fresh database file in its own temporary directory, removed after the test."""
    directory = tempfile.TemporaryDirectory()
    store = Store(Path(directory.name) / "authority.db", ledger_key=TEST_LEDGER_KEY)
    OPEN.append((store, directory))
    return store


def close_all() -> None:
    while OPEN:
        store, directory = OPEN.pop()
        store.close()
        directory.cleanup()


def rewrite_events(ledger: EventLedger, events: Sequence[LedgerEvent]) -> None:
    """
    Replace the ledger's rows with `events`, as someone with write access to the database
    file can: drop the append-only triggers, rewrite, and put the triggers back. This is
    outside the trust boundary on purpose; the chain-limit tests (claims matrix C22) pin
    what verify() can and cannot see after such a rewrite.
    """
    with ledger.store.transaction() as conn:
        triggers = [
            row[0] for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'ledger'"
            )
        ]
        for name in ("ledger_no_update", "ledger_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DELETE FROM ledger")
        conn.executemany(
            "INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (e.sequence, e.event_type, e.aggregate_id, e.actor, time_text(e.occurred_at),
                 canonical_json(dict(e.payload)), e.previous_hash, e.event_hash)
                for e in events
            ],
        )
        for sql in triggers:
            conn.execute(sql)


def rehash_chain(events: list[LedgerEvent], start: int, key: bytes | None = None,
                 renumber: bool = False) -> None:
    """
    Recompute every hash from `start` on, in place, as someone who can rewrite the file
    can. key=None is an attacker without the ledger key, who can only guess the hash
    function (plain SHA-256, v0.3.1's); pass a key to model a guess or a key holder.
    renumber=True also closes sequence gaps, as hiding a deletion requires (v3 Step 08).
    """
    # the body mirrors EventLedger.append/verify; a schema change there must land here
    previous = events[start - 1].event_hash if start else "GENESIS"
    for index in range(start, len(events)):
        event = events[index]
        sequence = index if renumber else event.sequence
        body = canonical_json({
            "sequence": sequence,
            "event_type": event.event_type,
            "aggregate_id": event.aggregate_id,
            "actor": event.actor,
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
            "previous_hash": previous,
        }).encode()
        event_hash = (sha256(body).hexdigest() if key is None
                      else hmac.new(key, body, sha256).hexdigest())
        events[index] = replace(event, sequence=sequence, previous_hash=previous,
                                event_hash=event_hash)
        previous = event_hash


def forge_token(store: Store, token_id: str, **columns: object) -> None:
    """
    Overwrite fields of a stored token that are fixed at issue, as someone with write
    access to the database file can: drop the `tokens_fixed` trigger, update, restore it.
    Values are given as stored (e.g. allowed_effects as a JSON list).
    """
    with store.transaction() as conn:
        known = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
        unknown = set(columns) - known
        if unknown:
            raise ValueError(f"not token columns: {sorted(unknown)}")
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'tokens_fixed'"
        ).fetchone()
        conn.execute("DROP TRIGGER tokens_fixed")
        assignments = ", ".join(f'"{name}" = ?' for name in columns)
        conn.execute(
            f"UPDATE tokens SET {assignments} WHERE token_id = ?", (*columns.values(), token_id)
        )
        conn.execute(sql)


class AdapterHandlers:
    """
    A HandlerTable stand-in for gateway-mechanism tests: run() supplies the handler for the
    next execute(), called with the proposal, as v0.3.1's `adapter` argument was. It goes
    through the same resolve() the gateway calls on a real HandlerTable, so consume,
    rollback, and state transitions are exercised unchanged.

    Adapters receive the Proposal, not the arguments dict, and no schema is checked. Tests
    of argument validation, a missing handler, or which handler runs use a real
    HandlerTable (tests/test_boundary.py).
    """

    def __init__(self) -> None:
        self._next: tuple[str, object, Callable[[object], object]] | None = None

    def use(self, proposal, adapter: Callable[[object], object]) -> None:
        self._next = (proposal.tool_name, proposal, adapter)

    def resolve(self, name: str, parameters: object) -> Callable[[], object]:
        if self._next is None:
            raise AssertionError("tests must call run(), not gateway.execute(), directly")
        tool, proposal, adapter = self._next
        self._next = None
        if name != tool:
            raise AssertionError(f"adapter was set up for {tool!r}, gateway resolved {name!r}")
        return lambda: adapter(proposal)


def run(gateway, token_id: str, proposal, adapter):
    """gateway.execute(token_id, proposal), with `adapter` as the handler that runs."""
    gateway.handlers.use(proposal, adapter)
    return gateway.execute(token_id, proposal)


def commit(store: Store, decision, proposal) -> None:
    """Commit a decision to the ledger in `store`, once (a second call is a no-op)."""
    try:
        EventLedger(store).commit_decision(decision, proposal)
    except sqlite3.IntegrityError:
        pass  # already committed: the primary key refused it and nothing was appended


def issue(gateway, decision, proposal, policy, human_approved: bool = False,
          audience: str | None = None):
    """
    Commit the decision, as the executor's hook does, then issue against it as the test
    agent under the test root grant; human_approved=True attaches a fresh signed approval.
    """
    commit(gateway.store, decision, proposal)
    principals_for(gateway.store)
    return gateway.issue(
        decision, proposal, policy, **AGENT,
        approval=approve(proposal) if human_approved else None, audience=audience,
    )


# -- principals (v3 Step 05) ----------------------------------------------------------------

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
HUMANS = {"alice": "k-alice", "rev": "k-rev", "operator": "k-operator"}
ORACLES = {"constraint-v1": "k-constraint", "reviewer": "k-reviewer", "sim-oracle": "k-sim"}
MODELS = ("agent", "openai")
MCP_SERVER = "mcp-server"  # a SERVICE principal: the receiving MCP server (v3 Step 07)
ROOT_GRANT = "g-root"
AGENT = {"principal_id": "agent", "grant_id": ROOT_GRANT}
TEST_KEYRING = Keyring({
    key_id: f"test-secret-{key_id}".encode() for key_id in (*HUMANS.values(), *ORACLES.values())
})


def principals_for(store: Store) -> Principals:
    """
    The test identities in `store`, registered once (idempotent): three humans, three
    oracles, two models, and a root grant from alice to agent with unrestricted scope.
    """
    principals = Principals(store, TEST_KEYRING)
    if principals.principal("alice") is None:
        for name, key_id in HUMANS.items():
            principals.register(name, PrincipalKind.HUMAN, by="controller", key_id=key_id)
        for name, key_id in ORACLES.items():
            principals.register(name, PrincipalKind.ORACLE, by="controller", key_id=key_id)
        for name in MODELS:
            principals.register(name, PrincipalKind.MODEL, by="controller")
        principals.register(MCP_SERVER, PrincipalKind.SERVICE, by="controller")
        scope = GrantScope()
        mac = TEST_KEYRING.sign(
            HUMANS["alice"], root_grant_payload(ROOT_GRANT, "alice", "agent", scope, FAR)
        )
        principals.grant_root("alice", "agent", scope, FAR, mac, grant_id=ROOT_GRANT)
    return principals


def gateway_store(store: Store) -> dict[str, object]:
    """`store=` and `principals=` for ExecutionGateway, guaranteed to share one Store."""
    return {"store": store, "principals": principals_for(store)}


def approve(proposal, human: str = "alice", expires_at: datetime = FAR) -> Approval:
    """A signed, single-use approval of `proposal` by a test human."""
    approval_id = f"ap-{secrets.token_hex(8)}"
    payload = approval_payload(
        approval_id, human, proposal.proposal_digest, proposal.partition, expires_at
    )
    mac = TEST_KEYRING.sign(HUMANS[human], payload) if human in HUMANS else ""
    return Approval(approval_id, human, proposal.proposal_digest, proposal.partition,
                    expires_at, mac)


# The oracle test partitions are bound to unless a test says otherwise (issue #41: binding
# is explicit, so a fixture binds as the host would when it opens an epoch)
TEST_ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")


def release(manager, key, actor: str, reason: str, oracle: Oracle = TEST_ORACLE) -> None:
    """
    manager.release(...), signed by `actor` over the partition's current epoch and the
    oracle the new epoch is bound to
    """
    record = manager.record(key)
    epoch = record.epoch if record is not None else 0
    payload = release_payload(key, epoch, reason, oracle.oracle_id, oracle.oracle_version)
    mac = TEST_KEYRING.sign(HUMANS[actor], payload) if actor in HUMANS else ""
    manager.release(key, actor, reason, mac, oracle=oracle)


def bind(log, key, oracle: Oracle = TEST_ORACLE) -> None:
    """Bind the partition's current epoch, as the host does when it opens one (#41)."""
    if log.bound_oracle(key) is None:
        log.bind_oracle(key, oracle)



# -- evidence admission (v3 Step 08) --------------------------------------------------------

_ROUTE_FOR = {"shadow": "human_approval", "autonomous": "autonomous", "audit": "audit",
              "human_approved": "human_approval"}


def shadow_log(ledger, deadline=None, clock=None):
    """A ShadowEvidenceLog on `ledger` with the test principals (oracles, reviewers)."""
    return ShadowEvidenceLog(ledger, deadline or timedelta(days=1), clock or utcnow,
                             principals=principals_for(ledger.store))


def trial(log, trial_id: str, proposal, provenance="shadow", oracle: Oracle | None = TEST_ORACLE):
    """
    Freeze a trial the way the host does: commit a decision whose route gives the wanted
    provenance (SHADOW <- HUMAN_APPROVAL, AUTONOMOUS, AUDIT), then freeze it. A
    HUMAN_APPROVED trial also gets a token issued on a signed approval, since an approval
    record is what makes a decision human-approved. The partition's current epoch is first
    bound to `oracle` if it is unbound (#41); oracle=None leaves it unbound.
    """
    if oracle is not None:
        bind(log, proposal.partition, oracle)
    provenance = getattr(provenance, "value", provenance)
    route = Route(_ROUTE_FOR[provenance])
    decision = Decision(trial_id, proposal.proposal_id, proposal.proposal_digest, None, None,
                        None, .9, route, (), 0.0, None, log.clock())
    commit(log.store, decision, proposal)
    if provenance == "human_approved":
        gateway = ExecutionGateway(log.clock, handlers=AdapterHandlers(),
                                   **gateway_store(log.store))
        policy = Policy(proposal.partition.policy_version, {}, 1, timedelta(days=1), 0.0)
        gateway.issue(decision, proposal, policy, **AGENT, approval=approve(proposal))
    return log.freeze(trial_id, proposal)


def sign_label(trial_id, partition, proposal_digest, value, oracle, claimed_at=None):
    """A SignedLabel signed with the test key of `oracle.oracle_id` (empty MAC if unknown)."""
    claimed_at = claimed_at or datetime.now(timezone.utc)
    key_id = ORACLES.get(oracle.oracle_id)
    payload = label_payload(trial_id, partition, proposal_digest, value, oracle, claimed_at)
    mac = TEST_KEYRING.sign(key_id, payload) if key_id else ""
    return SignedLabel(trial_id, partition, proposal_digest, value, oracle, claimed_at, mac)


def label(log, trial_id, partition, proposal_digest, value, oracle, at=None):
    """Adjudicate a signed label, as if the host received it at `at` (default: now)."""
    signed = sign_label(trial_id, partition, proposal_digest, value, oracle, at)
    if at is None:
        return log.adjudicate(signed)
    clock = log.clock
    log.clock = lambda: at
    try:
        return log.adjudicate(signed)
    finally:
        log.clock = clock


def replay(ledger, into: Store, *args):
    """
    ShadowEvidenceLog.replay(ledger, into, ...) with bare principals on `into`: replay needs
    an empty database and rebuilds evidence, not identities, so none are registered.
    """
    return ShadowEvidenceLog.replay(ledger, into, *args,
                                    principals=Principals(into, TEST_KEYRING))
