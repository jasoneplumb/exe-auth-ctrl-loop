"""v3 Step 02: authority state survives a restart (closes claims matrix L4)."""

import random
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _support import TEST_LEDGER_KEY, principals_for, trial

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    AuthorizationToken,
    EvidenceStore,
    PartitionKey,
    Policy,
    Proposal,
)
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.lifecycle import LifecycleManager, LifecycleState
from exe_auth_ctrl_loop.risk import RiskPolicy, SequenceBudget
from exe_auth_ctrl_loop.shadow import Provenance, ShadowEvidenceLog, TrialStatus
from exe_auth_ctrl_loop.store import Store

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def key(prompt: str) -> PartitionKey:
    return PartitionKey(
        "openai", "m1", prompt, "anthropic", "m2", "ep1",
        "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
    )


SUSPENDED, SUCCESSOR, PENDING = key("pp-suspended"), key("pp-successor"), key("pp-pending")
BUDGET = SequenceBudget("refund-total", frozenset({"pay"}), "partition", "usd", 100.0)


def proposal(partition: PartitionKey, usd: float = 0.0) -> Proposal:
    return Proposal(
        f"p-{partition.proposal_prompt_version}", "pay", "pay", {"usd": usd}, .96,
        frozenset({"refund:write"}), partition, "openai",
    )


def token(p: Proposal) -> AuthorizationToken:
    return AuthorizationToken(
        "tok-1", "dec-1", p.proposal_digest, p.tool_name, p.requested_effects, p.partition,
        "pol1", None, None, T0 + timedelta(minutes=2), False,
    )


class Components:
    """Everything a host process builds over one database at start-up."""

    def __init__(self, path: Path, now: datetime) -> None:
        self.db = Store(path, ledger_key=TEST_LEDGER_KEY)
        clock = lambda: now  # noqa: E731
        self.ledger = EventLedger(self.db)
        self.principals = principals_for(self.db)
        self.shadow = ShadowEvidenceLog(
            self.ledger, timedelta(hours=1), clock, principals=self.principals
        )
        self.store = EvidenceStore(self.db)
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        controller = AuthorityController(self.store, self.policy, random.Random(1), clock)
        self.manager = LifecycleManager(
            self.shadow, self.store, self.policy, controller, clock, principals=self.principals,
        )
        self.risk = RiskPolicy("risk-v1", budgets=(BUDGET,), store=self.db)


class RestartTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "authority.db"
        before = Components(self.path, T0)
        before.manager.record_severe(SUSPENDED, Provenance.SHADOW, "SEVERE_HARM")
        before.manager.on_churn(SUSPENDED, SUCCESSOR)
        charged = proposal(SUCCESSOR, usd=40.0)
        before.risk.commit(token(charged), charged)
        self.assertTrue(trial(before.shadow, "t-pending", proposal(PENDING)).accepted)
        self.events_before = len(before.ledger.events)
        before.db.close()
        # A new process: new objects, the same file, a later clock.
        self.after = Components(self.path, T0 + timedelta(hours=2))
        self.addCleanup(self.after.db.close)

    def test_suspension_survives(self):
        self.assertEqual(self.after.manager.state(SUSPENDED), LifecycleState.SUSPENDED)
        self.assertTrue(self.after.store.get(SUSPENDED).suspended)
        decision = self.after.manager.evaluate(proposal(SUSPENDED))
        self.assertIn("PARTITION_SUSPENDED", decision.reason_codes)

    def test_lineage_flag_survives(self):
        record = self.after.manager.record(SUCCESSOR)
        self.assertTrue(record.lineage_suspended)
        self.assertTrue(self.after.manager.record(SUSPENDED).retired)
        decision = self.after.manager.evaluate(proposal(SUCCESSOR))
        self.assertIn("LINEAGE_SUSPENDED", decision.reason_codes)

    def test_budget_charge_survives_and_still_binds(self):
        scope = BUDGET.scope_key(proposal(SUCCESSOR))
        self.assertEqual(self.after.risk.charged("refund-total", scope), (40.0, 1))
        over = proposal(SUCCESSOR, usd=61.0)
        self.assertIn("BUDGET_EXCEEDED:refund-total", self.after.risk.proposal_blockers(over))

    def test_pending_trial_survives_and_its_deadline_still_applies(self):
        self.assertEqual(self.after.shadow.status("t-pending"), TrialStatus.PENDING)
        self.assertEqual(self.after.shadow.expire_unlabeled(), ["t-pending"])
        self.assertEqual(self.after.shadow.status("t-pending"), TrialStatus.CENSORED)

    def test_ledger_survives_intact(self):
        self.assertEqual(len(self.after.ledger.events), self.events_before)
        self.assertTrue(self.after.ledger.verify())


class SecondProcessTests(unittest.TestCase):
    """A suspension written by one process is seen by another, not only after a restart."""

    PROBE = textwrap.dedent("""
        import sys
        from exe_auth_ctrl_loop.authority import EvidenceStore, PartitionKey
        from exe_auth_ctrl_loop.store import Store
        store = EvidenceStore(Store(sys.argv[1], ledger_key=b"test-ledger-key"))
        snapshot = store.get(PartitionKey(*sys.argv[2:]))
        print("suspended" if snapshot is not None and snapshot.suspended else "live")
    """)

    def test_another_process_sees_the_suspension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authority.db"
            host = Components(path, T0)
            try:
                host.manager.record_severe(SUSPENDED, Provenance.AUTONOMOUS, "SEVERE_HARM")
                out = subprocess.run(
                    [sys.executable, "-c", self.PROBE, str(path), *vars(SUSPENDED).values()],
                    capture_output=True, text=True, timeout=60, check=True,
                ).stdout.strip()
            finally:
                host.db.close()
        self.assertEqual(out, "suspended")


if __name__ == "__main__":
    unittest.main()
