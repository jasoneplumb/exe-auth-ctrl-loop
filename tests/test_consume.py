"""v3 Step 03: capabilities are consumed by one database transaction (docs/v3/spec.md §3)."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _support import forge_token

from exe_auth_ctrl_loop.authority import (
    Decision,
    ExecutionGateway,
    Policy,
    Proposal,
    Route,
)
from exe_auth_ctrl_loop.store import Store

# Shared by the test and the worker processes, so both build byte-identical proposals.
PROPOSALS = textwrap.dedent("""
    from exe_auth_ctrl_loop.authority import PartitionKey, Proposal
    KEY = PartitionKey("openai", "m1", "pp1", "anthropic", "m2", "ep1",
                       "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low")
    def make(i):
        return Proposal(f"p-{i}", "refund", "create_refund", {"order_id": i, "usd": 5},
                        .96, frozenset({"refund:write"}), KEY, "openai")
""")
namespace: dict = {}
exec(PROPOSALS, namespace)
make = namespace["make"]

POLICY = Policy("pol1", {"low": .90}, 1, timedelta(days=1), 0.0, token_ttl=timedelta(hours=1))


def decision(i: int, proposal: Proposal) -> Decision:
    return Decision(
        f"dec-{i}", proposal.proposal_id, proposal.proposal_digest, None, None, None, .9,
        Route.AUTONOMOUS, ("AUTHORITY_SUFFICIENT",), 0.0, 0.5, datetime.now(timezone.utc),
    )


class StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "authority.db"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.gateway = ExecutionGateway(store=self.store)

    def issue(self, i: int):
        p = make(i)
        return self.gateway.issue(decision(i, p), p, POLICY), p


class CrossProcessRedemptionTests(StoreCase):
    """Criterion 2: two OS processes race to redeem the same 200 tokens."""

    TOKENS = 200
    WORKER = textwrap.dedent("""
        import json, os, sys, time
        from exe_auth_ctrl_loop.authority import ExecutionGateway
        from exe_auth_ctrl_loop.store import Store
        namespace = {}
        exec(sys.argv[4], namespace)
        make = namespace["make"]
        path, ids_file, start = sys.argv[1:4]
        store = Store(path)
        gateway = ExecutionGateway(store=store)
        ids = json.load(open(ids_file))
        while not os.path.exists(start):
            time.sleep(0.001)
        ran = 0
        for i, token_id in enumerate(ids):
            def handler(proposal, token_id=token_id):
                with store.transaction() as conn:
                    conn.execute("INSERT INTO effects VALUES (?, ?)", (token_id, os.getpid()))
                return "ok"
            try:
                gateway.execute(token_id, make(i), handler)
                ran += 1
            except PermissionError:
                pass
        store.close()
        print(ran)
    """)

    def test_two_processes_run_each_handler_exactly_once(self):
        with self.store.transaction() as conn:
            # no uniqueness on purpose: a second effect for a token must be countable
            conn.execute("CREATE TABLE effects (token_id TEXT NOT NULL, pid INTEGER NOT NULL)")
        ids = [self.issue(i)[0].token_id for i in range(self.TOKENS)]
        ids_file = self.path.parent / "ids.json"
        ids_file.write_text(json.dumps(ids))
        start = self.path.parent / "start"
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", self.WORKER, str(self.path), str(ids_file), str(start),
                 PROPOSALS],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        start.touch()
        ran = []
        for worker in workers:
            out, err = worker.communicate(timeout=300)
            self.assertEqual(worker.returncode, 0, err)
            ran.append(int(out.strip()))
        conn = self.store.connection()
        per_token = dict(conn.execute("SELECT token_id, COUNT(*) FROM effects GROUP BY token_id"))
        self.assertEqual(sum(ran), self.TOKENS)
        self.assertEqual(set(per_token), set(ids))
        self.assertEqual(set(per_token.values()), {1})
        states = {t.state for t in self.gateway.tokens.values()}
        self.assertEqual(states, {"handler_completed"})


class OneTokenPerDecisionTests(StoreCase):
    """Criteria 5 and 5b."""

    def test_a_second_issue_is_refused_whatever_the_first_tokens_state(self):
        for state in ("issued", "revoked", "consumed"):
            with self.subTest(state=state):
                i = {"issued": 1, "revoked": 2, "consumed": 3}[state]
                token, p = self.issue(i)
                if state == "revoked":
                    self.gateway.revoke(token.token_id)
                if state == "consumed":
                    self.gateway.execute(token.token_id, p, lambda _: "ok")
                with self.assertRaisesRegex(PermissionError, "already has a token"):
                    self.gateway.issue(decision(i, p), p, POLICY)
                self.assertEqual(len(self.gateway.tokens), i)

    def test_the_database_refuses_a_second_row_for_a_decision(self):
        token, _ = self.issue(1)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "decision_id"):
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO tokens SELECT 'other', decision_id, proposal_digest,"
                    " allowed_tool, allowed_effects, partition, policy_version, evidence_id,"
                    " evidence_version, expires_at, human_approved, 'issued', NULL"
                    " FROM tokens WHERE token_id = ?",
                    (token.token_id,),
                )


class TransitionTests(StoreCase):
    """The database refuses every state change spec §3 does not list."""

    def state(self, token_id: str) -> str:
        return self.gateway.token(token_id).state

    def set_state(self, token_id: str, state: str) -> None:
        with self.store.transaction() as conn:
            conn.execute("UPDATE tokens SET state = ? WHERE token_id = ?", (state, token_id))

    def test_illegal_transitions_are_refused_at_the_database(self):
        token, p = self.issue(1)
        self.gateway.execute(token.token_id, p, lambda _: "ok")  # → handler_completed
        revoked, _ = self.issue(2)
        self.gateway.revoke(revoked.token_id)
        issued, _ = self.issue(3)
        cases = [
            (token.token_id, "issued"), (token.token_id, "handler_completed"),
            (revoked.token_id, "issued"), (revoked.token_id, "consumed"),
            (issued.token_id, "handler_started"), (issued.token_id, "issued"),
        ]
        for token_id, target in cases:
            with self.subTest(target=target):
                before = self.state(token_id)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "illegal token transition"):
                    self.set_state(token_id, target)
                self.assertEqual(self.state(token_id), before)

    def test_fields_fixed_at_issue_cannot_change_and_rows_cannot_go(self):
        token, _ = self.issue(1)
        for column, value in (("allowed_effects", "[]"), ("expires_at", "2999-01-01"),
                              ("human_approved", 1), ("proposal_digest", "x")):
            with self.subTest(column=column), self.assertRaisesRegex(
                sqlite3.IntegrityError, "fixed at issue"
            ):
                with self.store.transaction() as conn:
                    conn.execute(
                        f"UPDATE tokens SET {column} = ? WHERE token_id = ?",
                        (value, token.token_id),
                    )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "never deleted"):
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM tokens")

    def test_a_refused_redemption_leaves_the_token_issued(self):
        token, p = self.issue(1)
        with self.assertRaisesRegex(PermissionError, "proposal changed"):
            self.gateway.execute(token.token_id, make(2), lambda _: "ok")
        self.assertEqual(self.state(token.token_id), "issued")
        self.assertEqual(self.gateway.execute(token.token_id, p, lambda _: "ok"), "ok")

    def test_a_guard_whose_commit_raises_leaves_the_token_unspent(self):
        class Breaks:
            def redemption_blockers(self, token, proposal):
                return ()

            def commit(self, token, proposal):
                raise RuntimeError("settlement failed")

        gateway = ExecutionGateway(guards=(Breaks(),), store=self.store)
        token, p = self.issue(1)
        with self.assertRaisesRegex(RuntimeError, "settlement failed"):
            gateway.execute(token.token_id, p, lambda _: "ok")
        self.assertEqual(self.state(token.token_id), "issued")

    def test_a_transition_from_the_wrong_state_is_loud(self):
        token, _ = self.issue(1)
        with self.assertRaisesRegex(RuntimeError, "not in state 'consumed'"):
            self.gateway._advance(token.token_id, "consumed", "handler_started")
        self.assertEqual(self.state(token.token_id), "issued")

    def test_forging_refuses_a_column_tokens_do_not_have(self):
        token, _ = self.issue(1)
        with self.assertRaisesRegex(ValueError, "not token columns"):
            forge_token(self.store, token.token_id, alowed_effects="[]")

    def test_handler_states_are_recorded(self):
        token, p = self.issue(1)
        seen = []
        self.gateway.execute(token.token_id, p, lambda _: seen.append(self.state(token.token_id)))
        self.assertEqual(seen, ["handler_started"])
        self.assertEqual(self.state(token.token_id), "handler_completed")
        failing, q = self.issue(2)

        def broken(_proposal):
            raise ValueError("handler failed")

        with self.assertRaises(ValueError):
            self.gateway.execute(failing.token_id, q, broken)
        self.assertEqual(self.state(failing.token_id), "handler_failed")


class RestartTests(StoreCase):
    """Criteria 4 and 4b."""

    CRASH = textwrap.dedent("""
        import os, sys
        from exe_auth_ctrl_loop.authority import ExecutionGateway
        from exe_auth_ctrl_loop.store import Store
        namespace = {}
        exec(sys.argv[3], namespace)
        gateway = ExecutionGateway(store=Store(sys.argv[1]))
        def handler(proposal):
            os._exit(3)  # the process dies inside the effect
        gateway.execute(sys.argv[2], namespace["make"](1), handler)
    """)

    def reopen(self) -> ExecutionGateway:
        self.store.close()
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        return ExecutionGateway(store=self.store)

    def test_consumed_stays_consumed(self):
        token, p = self.issue(1)
        self.gateway.execute(token.token_id, p, lambda _: "ok")
        gateway = self.reopen()
        with self.assertRaisesRegex(PermissionError, "consumed"):
            gateway.execute(token.token_id, p, lambda _: "again")

    def test_an_issued_token_redeems_exactly_once_after_reopening(self):
        token, p = self.issue(1)
        gateway = self.reopen()
        self.assertEqual(gateway.execute(token.token_id, p, lambda _: "ok"), "ok")
        with self.assertRaisesRegex(PermissionError, "consumed"):
            gateway.execute(token.token_id, p, lambda _: "again")

    def test_a_handler_interrupted_by_a_crash_is_reported_and_never_rerun(self):
        token, p = self.issue(1)
        done = subprocess.run(
            [sys.executable, "-c", self.CRASH, str(self.path), token.token_id, PROPOSALS],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 3, done.stderr)
        gateway = self.reopen()
        self.assertEqual(gateway.effect_unknown(), [token.token_id])
        self.assertEqual(gateway.token(token.token_id).state, "handler_started")
        ran = []
        with self.assertRaisesRegex(PermissionError, "consumed"):
            gateway.execute(token.token_id, p, ran.append)
        self.assertEqual(ran, [])

    BEFORE_START = textwrap.dedent("""
        import os, sys
        from exe_auth_ctrl_loop.authority import ExecutionGateway
        from exe_auth_ctrl_loop.store import Store
        namespace = {}
        exec(sys.argv[3], namespace)
        gateway = ExecutionGateway(store=Store(sys.argv[1]))
        advance = gateway._advance
        def dies_before_start(token_id, current, target):
            if target == "handler_started":
                os._exit(4)  # after the consume committed, before handler_started is written
            advance(token_id, current, target)
        gateway._advance = dies_before_start
        gateway.execute(sys.argv[2], namespace["make"](1), lambda p: print("HANDLER RAN"))
    """)

    def test_a_crash_before_the_handler_starts_is_reported_as_unrun(self):
        token, p = self.issue(1)
        done = subprocess.run(
            [sys.executable, "-c", self.BEFORE_START, str(self.path), token.token_id, PROPOSALS],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertNotIn("HANDLER RAN", done.stdout)
        gateway = self.reopen()
        self.assertEqual(gateway.consumed_unrun(), [token.token_id])
        self.assertEqual(gateway.effect_unknown(), [])
        with self.assertRaisesRegex(PermissionError, "consumed"):
            gateway.execute(token.token_id, p, lambda _: "again")

    def test_effect_unknown_is_empty_when_every_handler_reported(self):
        token, p = self.issue(1)
        self.gateway.execute(token.token_id, p, lambda _: "ok")
        self.assertEqual(self.reopen().effect_unknown(), [])


if __name__ == "__main__":
    unittest.main()
