"""
v3 Step 06: synchronous invalidation (docs/v3/spec.md §5; finding F4).

Every revoking event in docs/v3/revocation_fixture.json has a test here named by its row's
`planned_test`, and tests/test_v3_spec_fixture.py checks that each one exists. Each test
issues a token, records the event, and redeems with no evaluate() in between: the
redemption is denied with the event as its reason.
"""

import json
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest import mock

from _support import (
    AGENT,
    FAR,
    HUMANS,
    ROOT_GRANT,
    TEST_KEYRING,
    AdapterHandlers,
    approve,
    commit,
    issue,
    principals_for,
    run,
    scratch_store,
)

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceStore,
    ExecutionGateway,
    PartitionKey,
    Policy,
    Proposal,
    Route,
)
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.lifecycle import LifecycleManager, LifecycleState
from exe_auth_ctrl_loop.principals import GrantScope, PrincipalKind, rescind_payload
from exe_auth_ctrl_loop.revocation import REVOKED_EVENT, PolicyAuthority
from exe_auth_ctrl_loop.risk import Bound, RiskEnvelope, RiskPolicy, SequenceBudget
from exe_auth_ctrl_loop.shadow import (
    Oracle,
    OracleKind,
    Provenance,
    ShadowEvidenceLog,
    TrialLabel,
)
from exe_auth_ctrl_loop.store import partition_text

T0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")
EFFECTS = frozenset({"refund:write", "refund:notify"})


def key(task: str = "refund", policy_version: str = "pol1") -> PartitionKey:
    return PartitionKey(
        "openai", "m1", "pp1", "anthropic", "m2", "ep1",
        "refund-api-v2", policy_version, "prod1", task, "0.95-1.00", "low",
    )


def envelope(maximum: float = 100.0) -> RiskEnvelope:
    return RiskEnvelope("create_refund", "low", 0, EFFECTS,
                        (Bound("usd", maximum=maximum, minimum=0.0),))


def budget(limit: float = 1000.0) -> SequenceBudget:
    return SequenceBudget("daily", frozenset({"create_refund"}), "global", "usd", limit)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class World(unittest.TestCase):
    """The shipped composition on one database, with one partition earned to AUTONOMOUS."""

    def setUp(self):
        self.clock = Clock()
        self.db = scratch_store()
        self.principals = principals_for(self.db)
        self.ledger = EventLedger(self.db)
        self.shadow = ShadowEvidenceLog(self.ledger, timedelta(hours=1), self.clock)
        self.store = EvidenceStore(self.db)
        # a token lives longer than evidence stays fresh, so staleness can be observed at
        # redemption before expiry would refuse the token anyway
        self.policy = Policy("pol1", {"low": .50}, 10, timedelta(days=30), 0.0,
                             token_ttl=timedelta(days=60))
        self.risk = RiskPolicy("risk-v1", (envelope(),), (budget(),), store=self.db)
        self.controller = AuthorityController(
            self.store, self.policy, random.Random(1), self.clock, guards=(self.risk,)
        )
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, self.controller, self.clock,
            principals=self.principals,
        )
        self.authority = PolicyAuthority(
            self.db, self.policy, controllers=(self.controller,), managers=(self.manager,)
        )
        self.gateway = ExecutionGateway(
            self.clock, evidence=self.store, current_policy=self.authority.current,
            guards=(self.manager, self.risk), store=self.db, handlers=AdapterHandlers(),
            principals=self.principals,
        )
        self.trials = 0
        self.key = key()
        self.earn(self.key)

    # -- helpers -----------------------------------------------------------------------

    def proposal(self, n: int, usd: float = 5, k: PartitionKey | None = None,
                 effects: frozenset[str] = frozenset({"refund:write"})) -> Proposal:
        return Proposal(f"p-{n}", "refund", "create_refund", {"order_id": n, "usd": usd}, .96,
                        effects, k or self.key, "openai")

    def label(self, k: PartitionKey, value: TrialLabel) -> None:
        self.trials += 1
        trial = f"t-{self.trials}"
        p = self.proposal(1000 + self.trials, k=k)
        self.assertTrue(self.shadow.freeze(trial, p, Provenance.SHADOW).accepted)
        result = self.shadow.adjudicate(trial, k, p.proposal_digest, value, ORACLE, self.clock())
        self.assertTrue(result.accepted)

    def earn(self, k: PartitionKey) -> None:
        for _ in range(10):
            self.label(k, TrialLabel.POSITIVE)
        self.assertEqual(self.manager.refresh(k), LifecycleState.AUTONOMOUS)

    def token(self, n: int, approved: bool = False, **kwargs):
        p = self.proposal(n, **kwargs)
        decision = self.manager.evaluate(p)
        self.assertEqual(decision.route, Route.AUTONOMOUS, decision.reason_codes)
        return issue(self.gateway, decision, p, self.policy, human_approved=approved), p

    def human_token(self, n: int, k: PartitionKey):
        """An approval-based token on a partition with no evidence (HUMAN_APPROVAL)."""
        p = self.proposal(n, k=k)
        decision = self.manager.evaluate(p)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL, decision.reason_codes)
        return issue(self.gateway, decision, p, self.policy, human_approved=True), p

    def revocations(self, event: str) -> list[dict]:
        return [e.payload for e in self.ledger.events
                if e.event_type == REVOKED_EVENT and e.payload["event"] == event]

    def assert_revoked(self, token, p, event: str) -> None:
        with self.assertRaisesRegex(PermissionError, f"revoked by {event}"):
            run(self.gateway, token.token_id, p, lambda _: "ran")
        stored = self.gateway.token(token.token_id)
        self.assertEqual((stored.state, stored.revocation_reason), ("revoked", event))
        self.assertTrue(any(token.token_id in r["revoked"] for r in self.revocations(event)))

    def assert_redeems(self, token, p) -> None:
        self.assertEqual(run(self.gateway, token.token_id, p, lambda _: "ran"), "ran")

    def link(self, predecessor: PartitionKey, successor: PartitionKey) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO lineage VALUES (?, ?, 'KEY_FIELD_CHURN', ?)",
                         (partition_text(predecessor), partition_text(successor),
                          T0.isoformat()))


class RevokingEventTests(World):
    """Criterion 1: one test per fixture row, named by its planned_test."""

    def test_severe_failure_revokes_partition_and_lineage(self):  # RV-01
        evidence_token, p1 = self.token(1)
        successor = key("refund-v2")
        self.link(self.key, successor)
        human_token, p2 = self.human_token(2, successor)
        self.manager.record_severe(self.key, Provenance.AUDIT, "SEVERE_HARM")
        self.assert_revoked(evidence_token, p1, "SEVERE_FAILURE")
        self.assert_revoked(human_token, p2, "SEVERE_FAILURE")

    def test_evidence_invalidation_revokes_partition_tokens(self):  # RV-02
        token, p = self.token(1)
        self.store.invalidate(self.key, "oracle recalled")
        self.assert_revoked(token, p, "EVIDENCE_INVALIDATED")

    def test_gate_lost_revokes_evidence_based_tokens_only(self):  # RV-03 (criteria 3, 6d)
        evidence_token, p1 = self.token(1)
        human_token, p2 = self.token(2, approved=True)
        for _ in range(10):
            self.label(self.key, TrialLabel.NEGATIVE)
            if self.manager.state(self.key) != LifecycleState.AUTONOMOUS:
                break
        self.assertEqual(self.manager.state(self.key), LifecycleState.QUALIFYING)
        self.assert_revoked(evidence_token, p1, "GATE_LOST")
        self.assertEqual(self.gateway.token(human_token.token_id).state, "issued")
        self.assert_redeems(human_token, p2)

    def test_oracle_change_opens_epoch_and_revokes_evidence_based_tokens(self):  # RV-04
        evidence_token, p1 = self.token(1)
        human_token, p2 = self.token(2, approved=True)
        epoch = self.manager.record(self.key).epoch
        self.manager.change_oracle(self.key, "constraint-v2", "2", by="alice")
        self.assertEqual(self.manager.state(self.key), LifecycleState.QUALIFYING)
        self.assertEqual(self.manager.record(self.key).epoch, epoch + 1)
        self.assertIsNone(self.shadow.snapshot(self.key, T0))  # counts reset
        self.assert_revoked(evidence_token, p1, "ORACLE_CHANGED")
        self.assert_redeems(human_token, p2)

    def test_key_churn_retires_partition_and_revokes(self):  # RV-05
        token, p = self.token(1)
        self.manager.on_churn(self.key, key("refund-v2"))
        self.assertTrue(self.manager.record(self.key).retired)
        self.assert_revoked(token, p, "PARTITION_RETIRED")

    def test_lineage_suspension_revokes_successor_tokens(self):  # RV-06
        successor = key("refund-v2")
        human_token, p = self.human_token(1, successor)  # successor exists, unsuspended
        self.manager.record_severe(self.key, Provenance.AUDIT, "SEVERE_HARM")
        self.manager.on_churn(self.key, successor)
        self.assertTrue(self.manager.record(successor).lineage_suspended)
        self.assert_revoked(human_token, p, "LINEAGE_SUSPENDED")

    def test_policy_change_is_one_event_retiring_every_old_partition(self):  # RV-07 (6c)
        other = key("cancel")
        self.earn(other)
        first, p1 = self.token(1)
        second, p2 = self.token(2, k=other)
        self.authority.change(replace(self.policy, version="pol2"))
        events = [e.payload["event"] for e in self.ledger.events
                  if e.event_type == REVOKED_EVENT]
        self.assertEqual(events.count("POLICY_CHANGED"), 1)
        self.assertNotIn("PARTITION_RETIRED", events)
        for k in (self.key, other):
            self.assertTrue(self.manager.record(k).retired)
        edges = dict(self.db.connection().execute(
            "SELECT predecessor, successor FROM lineage WHERE reason = 'POLICY_CHANGE'"
        ).fetchall())
        for k in (self.key, other):
            self.assertEqual(json.loads(edges[partition_text(k)])["policy_version"], "pol2")
        self.assert_revoked(first, p1, "POLICY_CHANGED")
        self.assert_revoked(second, p2, "POLICY_CHANGED")

    def test_newly_prohibited_effect_revokes_matching_tokens(self):  # RV-08
        notify, p1 = self.token(1, effects=frozenset({"refund:notify"}))
        write, p2 = self.token(2, effects=frozenset({"refund:write"}))
        self.authority.change(replace(self.policy,
                                      prohibited_effects=frozenset({"refund:notify"})))
        self.assert_revoked(notify, p1, "EFFECT_PROHIBITED")
        self.assert_redeems(write, p2)

    def test_budget_tightening_revokes_tokens_it_would_refuse(self):  # RV-09
        large, p1 = self.token(1, usd=50)
        small, p2 = self.token(2, usd=5)
        self.risk.update("risk-v2", (envelope(),), (budget(limit=20.0),))
        self.assert_revoked(large, p1, "BUDGET_TIGHTENED")
        self.assert_redeems(small, p2)

    def test_envelope_change_revokes_tokens_it_would_refuse(self):  # RV-10
        large, p1 = self.token(1, usd=50)
        small, p2 = self.token(2, usd=5)
        self.risk.update("risk-v2", (envelope(maximum=30.0),), (budget(),))
        self.assert_revoked(large, p1, "ENVELOPE_CHANGED")
        self.assert_redeems(small, p2)

    def test_rescinded_approval_revokes_its_token(self):  # RV-11
        p = self.proposal(1, k=key("unearned"))
        decision = self.manager.evaluate(p)
        approval = approve(p)
        commit(self.db, decision, p)
        token = self.gateway.issue(decision, p, self.policy, **AGENT, approval=approval)
        self.principals.rescind_approval(
            approval, TEST_KEYRING.sign(HUMANS["alice"], rescind_payload(approval.approval_id))
        )
        self.assert_revoked(token, p, "APPROVAL_RESCINDED")

    def test_grant_revocation_cascades_to_descendant_tokens(self):  # RV-12 (criterion 6)
        mid = self.principals.delegate(ROOT_GRANT, "openai", GrantScope(), FAR)
        leaf = self.principals.delegate(mid.grant_id, "agent", GrantScope(), FAR)
        tokens = {}
        for n, (principal, grant) in enumerate(
            (("agent", ROOT_GRANT), ("openai", mid.grant_id), ("agent", leaf.grant_id)), 1
        ):
            p = self.proposal(n)
            decision = self.manager.evaluate(p)
            commit(self.db, decision, p)
            tokens[grant] = (self.gateway.issue(decision, p, self.policy,
                                                principal_id=principal, grant_id=grant), p)
        self.principals.revoke_grant(mid.grant_id, by="alice")
        self.assert_revoked(*tokens[mid.grant_id], "GRANT_REVOKED")
        self.assert_revoked(*tokens[leaf.grant_id], "GRANT_REVOKED")
        self.assert_redeems(*tokens[ROOT_GRANT])

    def test_principal_deactivation_cascades_as_one_event(self):  # RV-13 (criterion 6)
        held = self.principals.delegate(ROOT_GRANT, "openai", GrantScope(), FAR)
        p1 = self.proposal(1)
        d1 = self.manager.evaluate(p1)
        commit(self.db, d1, p1)
        under_held = self.gateway.issue(d1, p1, self.policy, principal_id="openai",
                                        grant_id=held.grant_id)
        p2 = self.proposal(2, k=key("unearned"))
        d2 = self.manager.evaluate(p2)
        commit(self.db, d2, p2)
        approved_by_rev = self.gateway.issue(d2, p2, self.policy, **AGENT,
                                             approval=approve(p2, human="rev"))
        before = len(self.revocations("PRINCIPAL_DEACTIVATED"))
        self.principals.deactivate("openai", by="alice")
        record = self.revocations("PRINCIPAL_DEACTIVATED")[before]
        self.assertEqual(record["grants"], [held.grant_id])
        self.assertEqual(len(self.revocations("PRINCIPAL_DEACTIVATED")), before + 1)
        self.assert_revoked(under_held, p1, "PRINCIPAL_DEACTIVATED")
        self.principals.deactivate("rev", by="alice")
        self.assert_revoked(approved_by_rev, p2, "PRINCIPAL_DEACTIVATED")


class ListenerTests(World):
    """PR #35 review: a re-walk that resolves another partition's trials revokes there too."""

    def test_censoring_another_partitions_trials_revokes_its_tokens_at_once(self):
        other = key("cancel")
        self.earn(other)
        token, p = self.token(1, k=other)
        for n in range(10):  # pending trials on the other partition, never labelled
            pending = self.proposal(2000 + n, k=other)
            self.assertTrue(self.shadow.freeze(f"pending-{n}", pending, Provenance.SHADOW).accepted)
        self.clock.now = T0 + timedelta(hours=2)  # past the label deadline, evidence fresh
        self.label(self.key, TrialLabel.POSITIVE)  # this partition's walk censors them
        self.assertEqual(self.manager.record(other).state, LifecycleState.QUALIFYING)
        self.assert_revoked(token, p, "GATE_LOST")


class NotRevokingTests(World):
    """The fixture's deliberate non-events."""

    def test_label_that_keeps_the_gate_revokes_nothing(self):
        token, p = self.token(1)
        before = len(self.ledger.events)
        self.label(self.key, TrialLabel.POSITIVE)
        self.assertFalse(any(e.event_type == REVOKED_EVENT
                             for e in self.ledger.events[before:]))
        self.assert_redeems(token, p)

    def test_new_principal_or_grant_revokes_nothing(self):
        token, p = self.token(1)
        before = len(self.ledger.events)
        self.principals.register("carol", PrincipalKind.MODEL, by="controller")
        self.principals.delegate(ROOT_GRANT, "carol", GrantScope(), FAR)
        self.assertFalse(any(e.event_type == REVOKED_EVENT
                             for e in self.ledger.events[before:]))
        self.assert_redeems(token, p)

    def test_looser_policy_same_version_revokes_nothing(self):
        self.authority.change(replace(self.policy, prohibited_effects=frozenset({"x"})))
        token, p = self.token(1)
        before = len(self.revocations("EFFECT_PROHIBITED"))
        self.assertIsNone(self.authority.change(replace(self.policy)))  # "x" allowed again
        self.assertEqual(len(self.revocations("EFFECT_PROHIBITED")), before)
        self.assert_redeems(token, p)

    def test_stale_evidence_is_refused_at_redemption(self):
        token, p = self.token(1)
        self.clock.now = T0 + timedelta(days=31)
        with self.assertRaisesRegex(PermissionError, "EVIDENCE_STALE"):
            run(self.gateway, token.token_id, p, lambda _: "ran")
        self.assertEqual(self.gateway.token(token.token_id).state, "issued")  # not revoked


class TransactionTests(World):
    """Criterion 2: the event and its revocations commit together or not at all."""

    def test_a_failure_between_event_and_revocation_leaves_neither(self):
        token, p = self.token(1)
        version = self.store.get(self.key).version
        real = EventLedger.append

        def failing(ledger, event_type, *args, **kwargs):
            if event_type == REVOKED_EVENT:
                raise RuntimeError("crash between the revoke and its record")
            return real(ledger, event_type, *args, **kwargs)

        with mock.patch.object(EventLedger, "append", failing):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.store.invalidate(self.key, "recall")
        self.assertTrue(self.store.get(self.key).valid)  # the invalidation rolled back
        self.assertEqual(self.store.get(self.key).version, version)
        self.assertEqual(self.gateway.token(token.token_id).state, "issued")
        self.assert_redeems(token, p)


class RedemptionRecomputeTests(World):
    """Criterion 4: the lifecycle guard recomputes the gate from current counts."""

    def test_a_bound_that_fell_without_a_transition_is_refused(self):
        token, p = self.token(1)
        self.shadow.listeners.clear()  # labels arrive with no state change and no revoke
        for _ in range(10):
            self.label(self.key, TrialLabel.NEGATIVE)
        self.assertEqual(self.manager.record(self.key).state, LifecycleState.AUTONOMOUS)
        with self.assertRaisesRegex(PermissionError, "BOUND_BELOW_POLICY"):
            run(self.gateway, token.token_id, p, lambda _: "ran")
        self.assertEqual(self.gateway.token(token.token_id).state, "issued")


class LineageTests(World):
    """Criterion 5: a policy-version successor inherits a suspension (closes L12)."""

    def test_policy_version_successor_inherits_suspension(self):
        self.manager.record_severe(self.key, Provenance.AUDIT, "SEVERE_HARM")
        self.authority.change(replace(self.policy, version="pol2"))
        successor = replace(self.key, policy_version="pol2")
        self.manager.refresh(successor)
        self.assertTrue(self.manager.record(successor).lineage_suspended)
        decision = self.manager.evaluate(self.proposal(1, k=successor))
        self.assertIn("LINEAGE_SUSPENDED", decision.reason_codes)

    def test_an_unsuspended_predecessor_passes_nothing_on(self):
        self.authority.change(replace(self.policy, version="pol2"))
        successor = replace(self.key, policy_version="pol2")
        self.manager.refresh(successor)
        self.assertFalse(self.manager.record(successor).lineage_suspended)


class EffectUnknownTests(World):
    """Criterion 6b: a token already in its handler is reported, not revoked or re-run."""

    def test_a_started_token_is_listed_not_revoked(self):
        token, p = self.token(1)
        with self.db.transaction() as conn:  # a process stopped inside the handler
            conn.execute("UPDATE tokens SET state = 'consumed' WHERE token_id = ?",
                         (token.token_id,))
            conn.execute("UPDATE tokens SET state = 'handler_started' WHERE token_id = ?",
                         (token.token_id,))
        self.store.invalidate(self.key, "recall")
        (record,) = self.revocations("EVIDENCE_INVALIDATED")
        self.assertEqual(record["effect_unknown"], [token.token_id])
        self.assertNotIn(token.token_id, record["revoked"])
        self.assertEqual(self.gateway.token(token.token_id).state, "handler_started")
        self.assertEqual(self.gateway.effect_unknown(), [token.token_id])
        ran = []
        with self.assertRaises(PermissionError):
            run(self.gateway, token.token_id, p, ran.append)
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
