"""
v3 Step 08 (evidence admission, finding F3): gate criteria 1-6 of docs/v3/gates/step-08.md,
spec §7 rules 7-12. Each test names the criterion it is evidence for.
"""

import inspect
import unittest
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from _support import (
    AGENT,
    HUMANS,
    TEST_KEYRING,
    TEST_LEDGER_KEY,
    approve,
    commit,
    gateway_store,
    principals_for,
    rehash_chain,
    replay,
    rewrite_events,
    scratch_store,
    shadow_log,
    sign_label,
)
from _support import label as support_label
from _support import trial as freeze_trial

from exe_auth_ctrl_loop import (
    AuthorityController,
    ClaudeExecutionAgent,
    CrossModelAuthorityLoop,
    Decision,
    EventLedger,
    EvidenceStore,
    ExecutionGateway,
    ExecutionStatus,
    HandlerTable,
    LifecycleManager,
    LifecycleState,
    Oracle,
    OracleKind,
    PartitionKey,
    Policy,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
    Provenance,
    Reason,
    ReviewAction,
    Route,
    ShadowEvidenceLog,
    SignedLabel,
    Store,
    ToolCatalog,
    ToolDefinition,
    TrialLabel,
    TrialStatus,
)

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
ORACLE = Oracle("constraint-v1", "1", OracleKind.CONSTRAINT_CHECK, "frozen-proposal criteria")
OTHER_ORACLE = Oracle("reviewer", "1", OracleKind.INDEPENDENT_REVIEW, "post-hoc")
KEY = PartitionKey(
    "openai", "m1", "pp1", "anthropic", "claude-pinned", "execution-v1",
    "refund-api-v2", "pol1", "prod1", "bounded_refund", "0.95-1.00", "low",
)
# the tables replay rebuilds, each with a total order for row-by-row comparison
REPLAYED_TABLES = {
    "trials": "trial_id", "labels": "trial_id", "epochs": "floor_sequence",
    "epoch_oracles": "floor_sequence", "quarantines": "sequence",
}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def proposal(n: int, key: PartitionKey = KEY, usd: float = 24) -> Proposal:
    return Proposal(
        f"p-{n}", "refund order", "create_refund", {"order_id": n, "usd": usd}, .96,
        frozenset({"refund:write"}), key, "openai",
    )


class AdmissionCase(unittest.TestCase):
    """A shadow log on a fresh store, with the test principals and a host clock at T0."""

    def setUp(self):
        self.clock = Clock()
        self.ledger = EventLedger(scratch_store())
        self.log = shadow_log(self.ledger, timedelta(hours=1), self.clock)
        self.principals = self.log.principals

    def frozen(self, n: int, provenance="shadow", key: PartitionKey = KEY) -> Proposal:
        p = proposal(n, key)
        self.assertTrue(freeze_trial(self.log, f"t-{n}", p, provenance).accepted)
        return p

    def label(self, n: int, p: Proposal, value=TrialLabel.POSITIVE, oracle=ORACLE, at=None):
        return support_label(self.log, f"t-{n}", p.partition, p.proposal_digest, value, oracle,
                             at or self.clock.now + timedelta(minutes=1))

    def assert_quarantined(self, result, trial_id: str, reason: Reason) -> None:
        """Refused, kept in the quarantines table, and on the ledger with the same reason."""
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, reason)
        self.assertEqual(self.log.quarantines()[-1], (trial_id, reason.value))
        event = self.ledger.events[-1]
        self.assertEqual((event.event_type, event.aggregate_id, event.payload["reason"]),
                         ("shadow.quarantined", trial_id, reason.value))


class DerivedProvenanceTests(AdmissionCase):
    """Criterion 1 (spec §7 rule 7): provenance comes from the committed decision."""

    def decision(self, decision_id: str, p: Proposal, route: Route) -> Decision:
        return Decision(decision_id, p.proposal_id, p.proposal_digest, None, None, None, .9,
                        route, (), 0.0, None, self.clock())

    def test_no_admission_api_accepts_provenance(self):
        # the write side of the log, the loop's commit hook, and the signed label; counts()
        # takes a provenance only to read one stream back
        writers = (ShadowEvidenceLog.freeze, ShadowEvidenceLog.adjudicate,
                   ShadowEvidenceLog.record_human_review, ShadowEvidenceLog.bind_oracle,
                   ShadowEvidenceLog.open_epoch, ShadowEvidenceLog.publish,
                   CrossModelAuthorityLoop.commit)
        for method in writers:
            with self.subTest(method=method.__qualname__):
                self.assertNotIn("provenance", inspect.signature(method).parameters)
        self.assertNotIn("provenance", {f.name for f in fields(SignedLabel)})
        # v0.3.1's call shape is refused outright, not ignored
        with self.assertRaises(TypeError):
            self.log.freeze("t-x", proposal(0), Provenance.AUTONOMOUS)

    def test_each_route_gives_its_provenance(self):
        expected = {Route.AUDIT: Provenance.AUDIT, Route.AUTONOMOUS: Provenance.AUTONOMOUS,
                    Route.HUMAN_APPROVAL: Provenance.SHADOW}
        for n, (route, provenance) in enumerate(expected.items()):
            with self.subTest(route=route):
                p = proposal(n)
                commit(self.log.store, self.decision(f"d-{n}", p, route), p)
                result = self.log.freeze(f"d-{n}", p)
                self.assertEqual((result.accepted, result.provenance), (True, provenance))
                self.assertEqual(self.log.trial(f"d-{n}").provenance, provenance)
                frozen = self.ledger.events[-1]
                self.assertEqual(frozen.payload["provenance"], provenance.value)

    def test_an_approval_backed_decision_is_human_approved_and_never_counted(self):
        p = proposal(1)
        result = freeze_trial(self.log, "t-1", p, "human_approved")
        self.assertEqual(result.provenance, Provenance.HUMAN_APPROVED)
        self.assertTrue(self.label(1, p).accepted)
        self.assertEqual(self.log.counts(KEY, Provenance.HUMAN_APPROVED).positive, 1)
        self.assertIsNone(self.log.snapshot(KEY, T0))  # recorded, not evidence

    def test_what_is_not_a_committed_trialable_decision_is_refused(self):
        p = proposal(1)
        self.assert_quarantined(self.log.freeze("d-none", p), "d-none", Reason.NOT_COMMITTED)
        commit(self.log.store, self.decision("d-deny", p, Route.DENY), p)
        self.assert_quarantined(self.log.freeze("d-deny", p), "d-deny", Reason.NOT_TRIALABLE)
        commit(self.log.store, self.decision("d-ok", p, Route.AUTONOMOUS), p)
        other = proposal(1, usd=9000)  # same id, different content: not what was decided
        self.assert_quarantined(self.log.freeze("d-ok", other), "d-ok", Reason.DIGEST_MISMATCH)
        self.assertEqual(self.log.counts(KEY, Provenance.AUTONOMOUS).frozen, 0)

    def test_a_proposal_counts_once_per_epoch(self):
        p = self.frozen(1)
        commit(self.log.store, self.decision("d-again", p, Route.HUMAN_APPROVAL), p)
        self.assert_quarantined(self.log.freeze("d-again", p), "d-again",
                                Reason.DUPLICATE_PROPOSAL)
        self.assertEqual(self.log.counts(KEY, Provenance.SHADOW).frozen, 1)
        self.log.open_epoch(KEY)  # a new epoch counts it afresh
        self.assertTrue(self.log.freeze("d-again", p).accepted)


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)

    def create(self, **kwargs):
        return next(self.responses)


def fake_claude(p: Proposal) -> SimpleNamespace:
    """A model that requests exactly `p`, once, then finishes."""
    request = SimpleNamespace(content=[SimpleNamespace(
        type="tool_use", id="toolu-1", name=p.tool_name,
        input={"proposal_id": p.proposal_id, **p.parameters},
    )])
    done = SimpleNamespace(content=[SimpleNamespace(type="text", text="complete")])
    return SimpleNamespace(messages=FakeMessages([request, done]))


class ShippedLoopTests(unittest.TestCase):
    """
    Criterion 2 (spec §7 rule 10, O-6): the shipped CrossModelAuthorityLoop, with fake
    providers and nothing else, freezes every human-routed proposal on a non-autonomous
    partition as SHADOW and takes a partition from n=0 to AUTONOMOUS. The test never calls
    freeze(), publish(), or put(): the only outside input is the oracle's signed labels.
    """

    def setUp(self):
        self.clock = Clock()
        db = scratch_store()
        self.ledger = EventLedger(db)
        principals = principals_for(db)
        self.shadow = ShadowEvidenceLog(self.ledger, timedelta(hours=1), self.clock,
                                        principals=principals)
        self.store = EvidenceStore(db)
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        controller = AuthorityController(self.store, self.policy, clock=self.clock)
        self.manager = LifecycleManager(self.shadow, self.store, self.policy, controller,
                                        self.clock, principals=principals)
        catalog = ToolCatalog()
        catalog.register(ToolDefinition(
            "create_refund", "Create one bounded refund.",
            {"type": "object",
             "properties": {"order_id": {"type": "integer"}, "usd": {"type": "number"}},
             "required": ["order_id", "usd"], "additionalProperties": False},
            frozenset({"refund:write"}), "refund-api-v2", "bounded_refund", "low",
        ))
        handlers = HandlerTable(catalog)
        self.effects: list[dict] = []
        handlers.register("create_refund", lambda args: self.effects.append(args) or "ok")
        gateway = ExecutionGateway(self.clock, evidence=self.store,
                                   current_policy=lambda: self.policy, guards=(self.manager,),
                                   handlers=handlers, **gateway_store(db))
        self.agent = ClaudeExecutionAgent("claude-pinned", "execution-v1", self.manager,
                                          gateway, catalog, client=object(), **AGENT)
        self.loop = CrossModelAuthorityLoop(None, self.agent, self.ledger, "pol1", "prod1",
                                            shadow=self.shadow)

    def run_one(self, p: Proposal, approvals=()):
        self.agent.client = fake_claude(p)
        bundle = ProposalBundle(f"b-{p.proposal_id}", "refund order",
                                ProposalReadiness.EXECUTABLE, (p,), "one refund")
        start = len(self.ledger.events)
        run = self.loop.execute(bundle, approvals)
        return run, self.ledger.events[start:]

    def oracle_labels(self, decision_id: str, p: Proposal) -> None:
        result = support_label(self.shadow, decision_id, p.partition, p.proposal_digest,
                               TrialLabel.POSITIVE, ORACLE, self.clock.now)
        self.assertTrue(result.accepted, result.reason)

    def test_a_partition_reaches_autonomy_from_zero_through_the_shipped_loop(self):
        self.assertIsNone(self.store.get(KEY))
        human_routed = 0
        for n in range(200):
            p = proposal(n)
            run, events = self.run_one(p)
            step = run.steps[0]
            if step.route is Route.AUTONOMOUS:
                break
            # routed to a human, and frozen as SHADOW before the run asked for approval
            self.assertEqual((step.route, run.status, step.executed),
                             (Route.HUMAN_APPROVAL, ExecutionStatus.AWAITING_APPROVAL, False))
            human_routed += 1
            trial = self.shadow.trial(step.decision.decision_id)
            self.assertEqual((trial.provenance, trial.proposal_digest),
                             (Provenance.SHADOW, p.proposal_digest))
            kinds = [e.event_type for e in events]
            self.assertLess(kinds.index("authority.decision.committed"),
                            kinds.index("shadow.frozen"))
            self.assertLess(kinds.index("shadow.frozen"), kinds.index("execution.step.recorded"))
            self.oracle_labels(step.decision.decision_id, p)
        else:
            self.fail("the partition never reached AUTONOMOUS")
        self.assertEqual(self.manager.state(KEY), LifecycleState.AUTONOMOUS)
        self.assertTrue(step.executed)
        self.assertEqual(self.effects, [dict(p.parameters)])  # the first effect, unapproved
        # every human-routed proposal became a trial: none skipped, none added
        self.assertEqual(self.shadow.counts(KEY, Provenance.SHADOW).frozen, human_routed)
        self.assertGreaterEqual(human_routed, self.policy.n_min)
        self.assertEqual(self.store.get(KEY).successes, human_routed)

    def test_an_approved_rerun_executes_without_counting_its_proposal_twice(self):
        p = proposal(1)
        first, _ = self.run_one(p)
        self.assertEqual(first.status, ExecutionStatus.AWAITING_APPROVAL)
        second, events = self.run_one(p, approvals=(approve(p),))
        self.assertTrue(second.steps[0].executed)
        self.assertNotIn("shadow.frozen", [e.event_type for e in events])
        self.assertEqual(self.shadow.quarantines()[-1],
                         (second.steps[0].decision.decision_id, "DUPLICATE_PROPOSAL"))
        self.assertEqual(self.shadow.counts(KEY, Provenance.SHADOW).frozen, 1)


class SignedLabelTests(AdmissionCase):
    """Criterion 3 (spec §7 rules 8, 11): who may label, and which oracle an epoch takes."""

    def assert_refused(self, n: int, p: Proposal, signed: SignedLabel, reason: Reason) -> None:
        before = self.log.counts(KEY, Provenance.SHADOW)
        self.assert_quarantined(self.log.adjudicate(signed), f"t-{n}", reason)
        self.assertEqual(self.log.status(f"t-{n}"), TrialStatus.PENDING)  # still labelable
        self.assertEqual(self.log.counts(KEY, Provenance.SHADOW), before)

    def test_a_bad_mac_is_quarantined(self):
        p = self.frozen(1)
        signed = sign_label("t-1", KEY, p.proposal_digest, TrialLabel.NEGATIVE, ORACLE)
        flipped = replace(signed, label=TrialLabel.POSITIVE)  # verdict changed after signing
        self.assert_refused(1, p, flipped, Reason.BAD_SIGNATURE)
        forged = replace(signed, mac=TEST_KEYRING.sign("k-reviewer", signed.payload()))
        self.assert_refused(1, p, forged, Reason.BAD_SIGNATURE)  # another oracle's key
        self.assertTrue(self.log.adjudicate(signed).accepted)  # the genuine label still lands

    def test_an_unregistered_inactive_or_non_oracle_labeller_is_quarantined(self):
        p = self.frozen(1)
        ghost = Oracle("ghost", "1", OracleKind.CONSTRAINT_CHECK, "x")
        self.assert_refused(1, p, sign_label("t-1", KEY, p.proposal_digest,
                                             TrialLabel.POSITIVE, ghost),
                            Reason.ORACLE_NOT_REGISTERED)
        # a human principal with a valid key of its own is still not an oracle
        human = Oracle("alice", "1", OracleKind.INDEPENDENT_REVIEW, "x")
        payload = sign_label("t-1", KEY, p.proposal_digest, TrialLabel.POSITIVE, human)
        signed = replace(payload, mac=TEST_KEYRING.sign(HUMANS["alice"], payload.payload()))
        self.assert_refused(1, p, signed, Reason.ORACLE_NOT_REGISTERED)
        # a deactivated oracle's correctly signed label
        self.principals.deactivate(ORACLE.oracle_id, by="alice")
        self.assert_refused(1, p, sign_label("t-1", KEY, p.proposal_digest,
                                             TrialLabel.POSITIVE, ORACLE),
                            Reason.ORACLE_NOT_REGISTERED)

    def test_a_label_from_another_oracle_than_the_epochs_is_wrong_oracle(self):
        first, second = self.frozen(1), self.frozen(2)
        self.assertTrue(self.label(1, first).accepted)  # binds this epoch to ORACLE
        self.assert_refused(2, second, sign_label("t-2", KEY, second.proposal_digest,
                                                  TrialLabel.POSITIVE, OTHER_ORACLE),
                            Reason.WRONG_ORACLE)
        upgraded = replace(ORACLE, oracle_version="2")  # same principal, other version
        self.assert_refused(2, second, sign_label("t-2", KEY, second.proposal_digest,
                                                  TrialLabel.POSITIVE, upgraded),
                            Reason.WRONG_ORACLE)
        self.assertTrue(self.label(2, second).accepted)

    def test_an_oracle_change_opens_an_epoch_bound_to_the_new_oracle(self):
        # spec §7 rules 11 and 12, through the lifecycle (RV-04)
        store = EvidenceStore(self.log.store)
        policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        manager = LifecycleManager(self.log, store, policy,
                                   AuthorityController(store, policy, clock=self.clock),
                                   self.clock, principals=self.principals)
        old = self.frozen(1)
        self.assertTrue(self.label(1, old).accepted)
        manager.change_oracle(KEY, OTHER_ORACLE, by="alice")
        new = self.frozen(2)
        self.assert_refused(2, new, sign_label("t-2", KEY, new.proposal_digest,
                                               TrialLabel.POSITIVE, ORACLE),
                            Reason.WRONG_ORACLE)
        self.assertTrue(self.label(2, new, oracle=OTHER_ORACLE).accepted)
        counts = self.log.counts(KEY, Provenance.SHADOW)
        self.assertEqual((counts.frozen, counts.positive), (1, 1))  # the old label is gone
        rows = self.log.store.connection().execute(
            "SELECT epoch, alpha, reason FROM alpha_ledger ORDER BY entry_id").fetchall()
        self.assertEqual(rows, [(1, policy.alpha, "ORACLE_CHANGED")])
        with self.assertRaisesRegex(ValueError, "another oracle"):
            self.log.bind_oracle(KEY, ORACLE)

    def test_the_reviewer_of_a_trial_cannot_label_it(self):
        p = self.frozen(1)
        self.log.record_human_review("t-1", ORACLE.oracle_id, ReviewAction.APPROVE)
        self.assert_refused(1, p, sign_label("t-1", KEY, p.proposal_digest,
                                             TrialLabel.POSITIVE, ORACLE),
                            Reason.REVIEWER_IS_ORACLE)
        # the separation is per trial: the same oracle labels a trial it did not review
        other = self.frozen(2)
        self.assertTrue(self.label(2, other).accepted)


class ReceiptTimeTests(AdmissionCase):
    """Criterion 4 (spec §7 rule 9): the deadline runs on the host's received_at."""

    def adjudicate(self, n: int, p: Proposal, claimed_at: datetime, received_at: datetime):
        signed = sign_label(f"t-{n}", KEY, p.proposal_digest, TrialLabel.POSITIVE, ORACLE,
                            claimed_at)
        self.clock.now = received_at
        return self.log.adjudicate(signed)

    def test_a_backdated_claim_cannot_admit_a_late_label(self):
        p = self.frozen(1)
        result = self.adjudicate(1, p, claimed_at=T0 + timedelta(minutes=5),
                                 received_at=T0 + timedelta(hours=3))
        self.assert_quarantined(result, "t-1", Reason.LATE_LABEL)
        self.assertEqual(self.log.status("t-1"), TrialStatus.CENSORED)
        counts = self.log.counts(KEY, Provenance.SHADOW)
        self.assertEqual((counts.positive, counts.inconclusive), (0, 1))

    def test_a_label_received_in_time_is_admitted_whatever_it_claims(self):
        p = self.frozen(1)
        claimed, received = T0 + timedelta(days=9), T0 + timedelta(minutes=10)
        self.assertTrue(self.adjudicate(1, p, claimed, received).accepted)
        row = self.log.store.connection().execute(
            "SELECT claimed_at, resolved_at FROM labels WHERE trial_id = 't-1'").fetchone()
        self.assertEqual([datetime.fromisoformat(v) for v in row], [claimed, received])
        payload = self.ledger.events[-1].payload
        self.assertEqual((payload["claimed_at"], payload["received_at"]),
                         (row[0], row[1]))


class KeyedLedgerTests(AdmissionCase):
    """
    Criterion 5: the chain is an HMAC under the host's key, sequences are contiguous, and
    the C22 re-hash attack on evidence fails without the key. The generic chain cases
    (edit, deletion, truncation, wholesale rewrite) are ChainLimitTests in
    tests/test_audit_commitment.py.
    """

    def evidence(self):
        good, bad = self.frozen(1), self.frozen(2)
        self.label(1, good)
        self.label(2, bad, TrialLabel.NEGATIVE)
        # later history, so removing the label is an interior deletion; removing the last
        # event is truncation, which verify() cannot see (ChainLimitTests)
        self.frozen(3)
        self.assertTrue(self.ledger.verify())

    def without_the_negative_label(self, original=None):
        """The ledger as an attacker wants it: the negative label deleted, gap closed."""
        events = list(original if original is not None else self.ledger.events)
        index = next(i for i, e in enumerate(events)
                     if e.event_type == "shadow.labeled" and e.payload["label"] == "negative")
        del events[index]
        return events, index

    def replay(self):
        return replay(self.ledger, scratch_store())

    def test_the_key_is_needed_to_verify_and_is_not_in_the_file(self):
        self.evidence()
        path = Path(self.log.store.path)
        with Store(path, ledger_key=b"another-host") as other:
            self.assertFalse(EventLedger(other).verify())
        files = [path, *path.parent.glob(path.name + "-*")]  # the -wal and -shm files too
        for file in files:
            self.assertNotIn(TEST_LEDGER_KEY, file.read_bytes(), file.name)

    def test_deleting_a_label_and_rehashing_without_the_key_is_detected(self):
        self.evidence()
        original = self.ledger.events
        for guess in (None, b"guessed-key"):
            with self.subTest(guess=guess):
                events, index = self.without_the_negative_label(original)
                rehash_chain(events, index, guess, renumber=True)
                rewrite_events(self.ledger, events)
                self.assertFalse(self.ledger.verify())
                with self.assertRaisesRegex(ValueError, "does not verify"):
                    self.replay()

    def test_a_key_holder_can_rewrite_the_evidence(self):
        # the stated limit (docs/v3/threat-model.md): the key defends against someone who
        # can write the file, not against the host or same-process code holding the key
        self.evidence()
        events, index = self.without_the_negative_label()
        rehash_chain(events, index, TEST_LEDGER_KEY, renumber=True)
        rewrite_events(self.ledger, events)
        self.assertTrue(self.ledger.verify())
        self.assertEqual(self.replay().counts(KEY, Provenance.SHADOW).negative, 0)


class ReplayTests(AdmissionCase):
    """Criterion 6: replaying the append-only record reproduces counts and quarantines."""

    def history(self) -> None:
        """Every admission outcome this step adds, across two epochs and four provenances."""
        ps = {n: self.frozen(n, provenance) for n, provenance in
              enumerate(("shadow", "shadow", "audit", "autonomous", "human_approved",
                         "shadow", "shadow"))}
        self.label(0, ps[0])
        self.label(1, ps[1], TrialLabel.NEGATIVE)
        self.label(2, ps[2], TrialLabel.INCONCLUSIVE)
        self.label(3, ps[3])
        self.label(4, ps[4])
        signed = sign_label("t-5", KEY, ps[5].proposal_digest, TrialLabel.NEGATIVE, ORACLE)
        self.log.adjudicate(replace(signed, label=TrialLabel.POSITIVE))       # BAD_SIGNATURE
        self.label(5, ps[5], oracle=OTHER_ORACLE)                             # WRONG_ORACLE
        self.label(5, ps[5], oracle=Oracle("ghost", "1", OracleKind.CONSTRAINT_CHECK, "x"))
        self.log.record_human_review("t-6", ORACLE.oracle_id, ReviewAction.REJECT)
        self.label(6, ps[6])                                                   # REVIEWER_IS_ORACLE
        self.label(5, ps[5], at=T0 + timedelta(hours=2))                      # LATE_LABEL
        freeze_trial(self.log, "t-0", proposal(99))                           # DUPLICATE_TRIAL
        self.log.freeze("t-none", proposal(98))                              # NOT_COMMITTED
        # a second epoch with another oracle; its first label binds nothing new
        self.log.open_epoch(KEY)
        self.log.bind_oracle(KEY, OTHER_ORACLE)
        later = self.frozen(10)
        self.label(10, later, oracle=OTHER_ORACLE, at=T0 + timedelta(hours=2))
        self.frozen(11)
        self.clock.now = T0 + timedelta(hours=4)
        self.log.expire_unlabeled()                                           # t-6, t-11

    def rows(self, store: Store) -> dict[str, list[tuple]]:
        conn = store.connection()
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
                for table, order in REPLAYED_TABLES.items()}

    def test_replay_reproduces_counts_quarantines_and_bindings_exactly(self):
        self.history()
        reasons = {reason for _, reason in self.log.quarantines()}
        self.assertEqual(reasons, {r.value for r in (
            Reason.BAD_SIGNATURE, Reason.WRONG_ORACLE, Reason.ORACLE_NOT_REGISTERED,
            Reason.REVIEWER_IS_ORACLE, Reason.LATE_LABEL, Reason.DUPLICATE_TRIAL,
            Reason.NOT_COMMITTED)})
        into = scratch_store()
        replayed = replay(self.ledger, into, timedelta(hours=1), self.clock)
        self.assertEqual(self.rows(into), self.rows(self.log.store))
        self.assertEqual(replayed.quarantines(), self.log.quarantines())
        for provenance in Provenance:
            self.assertEqual(replayed.counts(KEY, provenance), self.log.counts(KEY, provenance))
        self.assertEqual(replayed.snapshot(KEY, T0), self.log.snapshot(KEY, T0))
        for trial_id in [f"t-{n}" for n in (*range(7), 10, 11)]:
            self.assertEqual(replayed.status(trial_id), self.log.status(trial_id), trial_id)

    def test_replay_refuses_a_database_holding_any_table_it_writes(self):
        # PR #39 review: the empty-database guard covers every table replay writes
        self.frozen(1)
        for statement in ("INSERT INTO quarantines VALUES (0, 't', 'BAD_SIGNATURE')",
                          "INSERT INTO epoch_oracles VALUES ('k', -1, 'o', '1',"
                          " 'constraint_check', 's', 0)"):
            with self.subTest(statement=statement):
                into = scratch_store()
                with into.transaction() as conn:
                    conn.execute(statement)
                with self.assertRaisesRegex(ValueError, "empty database"):
                    replay(self.ledger, into)

    def test_a_ledger_chained_before_step_08_is_refused(self):
        # an unkeyed SHA-256 chain (v0.3.1 to Step 07) does not verify under the keyed one
        self.frozen(1)
        events = self.ledger.events
        rehash_chain(events, 0, None)
        rewrite_events(self.ledger, events)
        self.assertFalse(self.ledger.verify())
        with self.assertRaisesRegex(ValueError, "does not verify"):
            replay(self.ledger, scratch_store())


if __name__ == "__main__":
    unittest.main()
