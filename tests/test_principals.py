"""v3 Step 05: principals, grants, approvals, lineage (docs/v3/spec.md §8; F5, closes L9)."""

import inspect
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from _support import (
    AGENT,
    FAR,
    HUMANS,
    ROOT_GRANT,
    TEST_KEYRING,
    AdapterHandlers,
    approve,
    commit,
    principals_for,
    release,
    run,
    scratch_store,
)

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    PartitionKey,
    Policy,
    Proposal,
    Route,
)
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.lifecycle import LifecycleManager, LifecycleState
from exe_auth_ctrl_loop.principals import (
    PERMISSION_EVENTS,
    GrantScope,
    PrincipalKind,
    Principals,
    rescind_payload,
    root_grant_payload,
)
from exe_auth_ctrl_loop.shadow import Provenance, ShadowEvidenceLog

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
KEY = PartitionKey(
    "openai", "m1", "pp1", "anthropic", "m2", "ep1",
    "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
)
POLICY = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)


def proposal(n: int = 1, usd: float = 24, tool: str = "create_refund") -> Proposal:
    return Proposal(
        f"p-{n}", "refund", tool, {"order_id": n, "usd": usd}, .96,
        frozenset({"refund:write"}), KEY, "openai",
    )


class Fixture(unittest.TestCase):
    def setUp(self):
        self.db = scratch_store()
        self.principals = principals_for(self.db)
        self.ledger = EventLedger(self.db)
        self.store = EvidenceStore(self.db)
        self.controller = AuthorityController(self.store, POLICY, random.Random(1), lambda: NOW)
        self.gateway = ExecutionGateway(
            lambda: NOW, store=self.db, handlers=AdapterHandlers(), principals=self.principals,
        )

    def mature(self):
        self.store.put(EvidenceSnapshot("e1", 1, KEY, 99, 1, NOW - timedelta(days=1), NOW))

    def decide(self, p: Proposal):
        decision = self.controller.evaluate(p)
        commit(self.db, decision, p)
        return decision

    def events(self):
        return [e.event_type for e in self.ledger.events]

    def child(self, scope=GrantScope(), expires=FAR, parent=ROOT_GRANT, grantee="openai"):
        return self.principals.delegate(parent, grantee, scope, expires)


class LedgerActorTests(Fixture):
    """Criterion 1."""

    def test_an_unregistered_actor_is_refused(self):
        with self.assertRaisesRegex(PermissionError, "not an active registered principal"):
            self.ledger.append("x", "a", "nobody", {})

    def test_a_deactivated_actor_is_refused(self):
        self.ledger.append("x", "a", "rev", {})
        self.principals.deactivate("rev", by="controller")
        with self.assertRaisesRegex(PermissionError, "not an active registered principal"):
            self.ledger.append("x", "a", "rev", {})

    def test_the_host_identities_exist_by_schema(self):
        for name in ("controller", "execution-gateway", "claude-execution-loop"):
            self.assertEqual(self.principals.principal(name).kind, PrincipalKind.SERVICE)


class ApprovalTests(Fixture):
    """Criterion 2, approvals: a forged or wrong-principal approval is refused at issue."""

    def setUp(self):
        super().setUp()
        self.p = proposal()
        self.decision = self.decide(self.p)  # no evidence: HUMAN_APPROVAL
        self.assertEqual(self.decision.route, Route.HUMAN_APPROVAL)

    def issue(self, approval):
        return self.gateway.issue(self.decision, self.p, POLICY, **AGENT, approval=approval)

    def test_a_valid_approval_issues_once_and_is_recorded(self):
        approval = approve(self.p)
        token = self.issue(approval)
        self.assertTrue(token.human_approved)
        self.assertEqual(token.approval_id, approval.approval_id)
        self.assertIn("approval.recorded", self.events())

    def test_without_an_approval_the_route_does_not_issue(self):
        with self.assertRaisesRegex(PermissionError, "does not authorize"):
            self.issue(None)

    def test_forged_and_misattributed_approvals_are_refused(self):
        good = approve(self.p)
        cases = {
            "forged MAC": replace(good, mac="0" * 64),
            "another human's name on alice's MAC": replace(good, principal_id="rev"),
            "a model": approve(self.p, human="agent"),
            "an oracle": approve(self.p, human="constraint-v1"),
            "unregistered": approve(self.p, human="mallory"),
        }
        for label, approval in cases.items():
            with self.subTest(label), self.assertRaisesRegex(PermissionError, "approval refused"):
                self.issue(approval)
        self.assertEqual(self.gateway.tokens, {})

    def test_an_approval_does_not_transfer(self):
        other = proposal(2)
        with self.assertRaisesRegex(PermissionError, "APPROVAL_OTHER_PROPOSAL"):
            self.issue(approve(other))
        moved = replace(approve(self.p), partition=replace(KEY, risk_class="high"))
        with self.assertRaisesRegex(PermissionError, "APPROVAL_OTHER_PARTITION"):
            self.issue(moved)

    def test_expired_rescinded_and_reused_approvals_are_refused(self):
        with self.assertRaisesRegex(PermissionError, "APPROVAL_EXPIRED"):
            self.issue(approve(self.p, expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
        rescinded = approve(self.p)
        self.principals.rescind_approval(
            rescinded, TEST_KEYRING.sign(HUMANS["alice"], rescind_payload(rescinded.approval_id))
        )
        with self.assertRaisesRegex(PermissionError, "APPROVAL_RESCINDED"):
            self.issue(rescinded)
        used = approve(self.p)
        self.issue(used)
        second = self.decide(self.p)
        with self.assertRaisesRegex(PermissionError, "approval already used"):
            self.gateway.issue(second, self.p, POLICY, **AGENT, approval=used)

    def test_a_rescission_after_issue_refuses_redemption(self):
        approval = approve(self.p)
        token = self.issue(approval)
        self.principals.rescind_approval(
            approval, TEST_KEYRING.sign(HUMANS["alice"], rescind_payload(approval.approval_id))
        )
        with self.assertRaisesRegex(PermissionError, "APPROVAL_RESCINDED"):
            run(self.gateway, token.token_id, self.p, lambda _: "ran")


class ReleaseTests(Fixture):
    """Criterion 2, release: only a verified human releases a suspension (closes L9)."""

    def setUp(self):
        super().setUp()
        self.shadow = ShadowEvidenceLog(self.ledger, timedelta(hours=1), lambda: NOW)
        self.manager = LifecycleManager(
            self.shadow, self.store, POLICY, self.controller, lambda: NOW,
            principals=self.principals,
        )
        self.manager.record_severe(KEY, Provenance.SHADOW, "SEVERE_HARM")

    def test_a_signed_human_release_works(self):
        release(self.manager, KEY, "alice", "root cause fixed")
        self.assertEqual(self.manager.state(KEY), LifecycleState.QUALIFYING)

    def test_unsigned_misattributed_and_non_human_releases_are_refused(self):
        for actor, mac in (("alice", ""), ("alice", "f" * 64), ("agent", ""),
                           ("constraint-v1", ""), ("nobody", "")):
            with self.subTest(actor=actor), self.assertRaises(PermissionError):
                self.manager.release(KEY, actor, "because", mac)
        self.assertEqual(self.manager.state(KEY), LifecycleState.SUSPENDED)

    def test_a_signed_release_cannot_be_replayed_at_a_later_epoch(self):
        from exe_auth_ctrl_loop.principals import release_payload
        mac = TEST_KEYRING.sign(HUMANS["alice"], release_payload(KEY, 0, "fixed"))
        self.manager.release(KEY, "alice", "fixed", mac)
        self.manager.record_severe(KEY, Provenance.SHADOW, "SEVERE_AGAIN")
        with self.assertRaises(PermissionError):
            self.manager.release(KEY, "alice", "fixed", mac)

    def test_a_manager_without_principals_cannot_release(self):
        bare = LifecycleManager(self.shadow, self.store, POLICY, self.controller, lambda: NOW)
        with self.assertRaisesRegex(PermissionError, "signature from an active human"):
            release(bare, KEY, "alice", "fixed")


class GrantTests(Fixture):
    """Criterion 3, and the grant checks at issue."""

    NARROW = GrantScope(tools=frozenset({"create_refund"}), effects=frozenset({"refund:write"}),
                        ceilings={"usd": 100.0})

    def test_delegation_narrower_and_sooner_is_accepted(self):
        grant = self.child(self.NARROW, FAR - timedelta(days=1))
        self.assertEqual(self.principals.grant(grant.grant_id).parent_grant_id, ROOT_GRANT)

    def test_a_broader_child_is_refused(self):
        parent = self.child(self.NARROW)
        broader = {
            "unrestricted tools": replace(self.NARROW, tools=None),
            "another tool": replace(self.NARROW, tools=frozenset({"create_refund", "delete"})),
            "more effects": replace(self.NARROW, effects=frozenset({"refund:write", "x"})),
            "a dropped ceiling": replace(self.NARROW, ceilings={}),
            "a higher ceiling": replace(self.NARROW, ceilings={"usd": 101.0}),
        }
        for label, scope in broader.items():
            with self.subTest(label), self.assertRaisesRegex(PermissionError, "broader"):
                self.principals.delegate(parent.grant_id, "agent", scope, FAR)

    def test_a_child_cannot_outlive_its_parent(self):
        parent = self.child(self.NARROW, FAR - timedelta(days=10))
        with self.assertRaisesRegex(PermissionError, "outlive"):
            self.principals.delegate(parent.grant_id, "agent", self.NARROW, FAR)

    def test_root_grants_need_a_signing_human(self):
        scope = GrantScope()
        with self.assertRaises(PermissionError):  # a model cannot create a root grant
            self.principals.grant_root("agent", "openai", scope, FAR, "", grant_id="g-x")
        payload = root_grant_payload("g-y", "alice", "openai", scope, FAR)
        with self.assertRaises(PermissionError):  # signed for different terms
            self.principals.grant_root(
                "alice", "openai", scope, FAR - timedelta(days=1),
                TEST_KEYRING.sign(HUMANS["alice"], payload), grant_id="g-y",
            )

    def test_issue_checks_holder_scope_and_liveness(self):
        self.mature()
        narrow = self.child(self.NARROW)  # held by openai
        p = proposal(usd=500)
        decision = self.decide(p)
        with self.assertRaisesRegex(PermissionError, "GRANT_NOT_HELD"):
            self.gateway.issue(decision, p, POLICY, principal_id="agent", grant_id=narrow.grant_id)
        with self.assertRaisesRegex(PermissionError, "GRANT_CEILING:usd"):
            self.gateway.issue(decision, p, POLICY, principal_id="openai",
                               grant_id=narrow.grant_id)
        self.principals.revoke_grant(ROOT_GRANT, by="alice")
        ok = proposal(2, usd=50)
        with self.assertRaisesRegex(PermissionError, "GRANT_REVOKED"):
            self.gateway.issue(self.decide(ok), ok, POLICY, principal_id="openai",
                               grant_id=narrow.grant_id)

    def test_a_revoked_ancestor_refuses_redemption(self):
        self.mature()
        p = proposal()
        token = self.gateway.issue(self.decide(p), p, POLICY, **AGENT)
        self.principals.revoke_grant(ROOT_GRANT, by="alice")
        with self.assertRaisesRegex(PermissionError, "GRANT_REVOKED"):
            run(self.gateway, token.token_id, p, lambda _: "ran")
        # v3 Step 06: revoked at the grant revocation (RV-12), not merely refused
        self.assertEqual(self.gateway.token(token.token_id).state, "revoked")


class LineageTests(Fixture):
    """Criterion 4: every token traces to its root grant."""

    def test_lineage_runs_from_the_tokens_grant_to_the_root(self):
        self.mature()
        mid = self.child(grantee="openai")
        leaf = self.principals.delegate(mid.grant_id, "agent", GrantScope(), FAR)
        tokens = []
        for n, (principal, grant) in enumerate(
            (("agent", ROOT_GRANT), ("openai", mid.grant_id), ("agent", leaf.grant_id)), start=1
        ):
            p = proposal(n)
            tokens.append((self.gateway.issue(self.decide(p), p, POLICY,
                                              principal_id=principal, grant_id=grant), grant))
        expected = {
            ROOT_GRANT: [ROOT_GRANT],
            mid.grant_id: [mid.grant_id, ROOT_GRANT],
            leaf.grant_id: [leaf.grant_id, mid.grant_id, ROOT_GRANT],
        }
        for token, grant in tokens:
            chain = self.principals.lineage(token.token_id)
            self.assertEqual([g.grant_id for g in chain], expected[grant])
            self.assertIsNone(chain[-1].parent_grant_id)
            self.assertEqual(token.requesting_principal, chain[0].grantee)
        self.assertTrue(all(t.grant_id for t in self.gateway.tokens.values()))


class PermissionEventTests(Fixture):
    """Criterion 5: every permission change is exactly one of the six ledger events."""

    QUERIES = {"principal", "grant", "chain", "lineage", "grant_blockers", "approval_blockers",
               "rescinded", "verify_human"}

    def mutations(self):
        p = proposal()
        approval_a, approval_b = approve(p), approve(p)
        scope = GrantScope()
        return {
            "register": ("principal.registered",
                         lambda: self.principals.register("bob", PrincipalKind.MODEL,
                                                          by="controller")),
            "deactivate": ("principal.deactivated",
                           lambda: self.principals.deactivate("openai", by="alice")),
            "grant_root": ("grant.created", lambda: self.principals.grant_root(
                "alice", "agent", scope, FAR,
                TEST_KEYRING.sign(HUMANS["alice"],
                                  root_grant_payload("g-2", "alice", "agent", scope, FAR)),
                grant_id="g-2")),
            "delegate": ("grant.created",
                         lambda: self.principals.delegate(ROOT_GRANT, "rev", scope, FAR)),
            "revoke_grant": ("grant.revoked",
                             lambda: self.principals.revoke_grant(ROOT_GRANT, by="alice")),
            "_record_approval": ("approval.recorded",
                                 lambda: self.principals._record_approval(approval_a)),
            "rescind_approval": ("approval.rescinded", lambda: self.principals.rescind_approval(
                approval_b,
                TEST_KEYRING.sign(HUMANS["alice"], rescind_payload(approval_b.approval_id)))),
        }

    def test_the_public_api_is_fully_classified(self):
        public = {name for name, _ in inspect.getmembers(Principals, inspect.isfunction)
                  if not name.startswith("_")}
        mutations = {name for name in self.mutations() if not name.startswith("_")}
        self.assertEqual(public, self.QUERIES | mutations)

    def test_each_change_writes_exactly_one_event_of_its_type(self):
        for name, (event_type, call) in self.mutations().items():
            with self.subTest(name):
                before = len(self.ledger.events)
                call()
                added = [e.event_type for e in self.ledger.events[before:]
                         if e.event_type in PERMISSION_EVENTS]
                self.assertEqual(added, [event_type])  # plus any authority.revoked (Step 06)
        self.assertEqual(set(PERMISSION_EVENTS),
                         {event for event, _ in self.mutations().values()})

    def test_a_refused_change_writes_nothing(self):
        before = len(self.ledger.events)
        with self.assertRaises(PermissionError):
            self.principals.delegate(ROOT_GRANT, "rev", GrantScope(), FAR + timedelta(days=1))
        with self.assertRaises(PermissionError):
            self.principals.register("eve", PrincipalKind.MODEL, by="agent")  # models cannot
        self.assertEqual(len(self.ledger.events), before)

    def test_an_approval_row_rolls_back_with_the_issue_that_recorded_it(self):
        # the issue path relies on _record_approval joining the caller's transaction
        approval = approve(proposal())
        before = len(self.ledger.events)
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.principals._record_approval(approval)
                raise RuntimeError("the token insert failed")
        self.assertIsNone(self.db.connection().execute(
            "SELECT 1 FROM approvals WHERE approval_id = ?", (approval.approval_id,)
        ).fetchone())
        self.assertEqual(len(self.ledger.events), before)

    def test_ceilings_cannot_be_widened_through_the_original_dict(self):
        ceilings = {"usd": 100.0}
        scope = GrantScope(ceilings=ceilings)
        ceilings["usd"] = 999.0
        self.assertEqual(scope.ceilings["usd"], 100.0)
        with self.assertRaises(TypeError):
            scope.ceilings["usd"] = 999.0  # type: ignore[index]

    def test_the_tables_refuse_changes_outside_the_api(self):
        import sqlite3
        conn = self.db.connection()
        for sql in ("UPDATE principals SET kind = 'HUMAN' WHERE principal_id = 'agent'",
                    "UPDATE principals SET active = 1 WHERE principal_id = 'agent'",
                    "DELETE FROM principals WHERE principal_id = 'agent'",
                    f"UPDATE grants SET scope = '{{}}' WHERE grant_id = '{ROOT_GRANT}'",
                    f"DELETE FROM grants WHERE grant_id = '{ROOT_GRANT}'"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)


if __name__ == "__main__":
    unittest.main()
