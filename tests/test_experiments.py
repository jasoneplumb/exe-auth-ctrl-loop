"""
Step 08: the simulation harness is deterministic, starts at n=0, and reports the bad news.
v3 Step 09: it drives the shipped loop (ShippedLoopTests, criterion 1: the four L6
differences are gone). Reduced sizes keep the suite fast; committed results use full configs.
"""

import ast
import csv
import json
import tempfile
import unittest
from pathlib import Path

import experiments.simulate
from exe_auth_ctrl_loop import Provenance
from experiments.run import CONFIG_DIR, load_config, main, run_scenario
from experiments.simulate import ScenarioConfig, Simulation


def small(name: str, **overrides) -> ScenarioConfig:
    cfg = load_config(CONFIG_DIR / f"{name}.json")
    return ScenarioConfig.from_dict({**cfg.__dict__, "seeds": (1, 2), "horizon": 160, **overrides})


class HarnessTests(unittest.TestCase):
    def test_stable_reaches_autonomy_from_zero_without_seeding(self):
        sim = Simulation(small("stable"), seed=1, trace=True)
        self.assertIsNone(sim.store.get(sim.key))
        metrics = sim.run()
        self.assertTrue(metrics.qualified, metrics)
        self.assertIsNotNone(metrics.time_to_authority)
        self.assertEqual(sim.trace[0]["n"], 0)
        self.assertGreater(metrics.autonomous_executions + metrics.audit_executions, 0)
        self.assertEqual(metrics.unauthorized_side_effects, 0)
        self.assertGreater(metrics.shadow_trials, 0)
        # tokens are redeemed, not mostly expired or withdrawn, in the benign scenario
        self.assertLess(metrics.stale_token_denials, metrics.tokens_consumed)

    def test_every_token_is_spent_or_refused_in_the_step_it_is_issued(self):
        # v3 Step 09: the shipped loop issues and redeems inside one run, so the harness no
        # longer holds tokens across steps (its old redeem_delay). Revocation of an
        # outstanding token is tested on the gateway itself (test_gateway_revocation.py).
        sim = Simulation(small("risk_envelopes", horizon=300), seed=1)
        metrics = sim.run()
        issued = sim.db.connection().execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        self.assertGreater(issued, 0)
        self.assertEqual(issued, metrics.tokens_consumed + metrics.stale_token_denials)

    def test_below_threshold_never_qualifies(self):
        for seed in (1, 2, 3):
            metrics = Simulation(small("never_qualifies"), seed=seed).run()
            self.assertFalse(metrics.qualified, (seed, metrics))
            self.assertEqual(metrics.autonomous_executions, 0)

    def test_fast_turnover_prevents_qualification_before_invalidation(self):
        metrics = Simulation(small("turnover_fast", churn_every=25), seed=1).run()
        self.assertGreater(metrics.turnovers, 0)
        self.assertFalse(metrics.qualified_before_turnover)

    def test_delayed_defection_causes_failures_after_autonomy_and_is_contained(self):
        metrics = Simulation(small("delayed_defection", horizon=300), seed=1).run()
        self.assertTrue(metrics.qualified)
        self.assertGreater(metrics.failures_after_autonomy, 0)
        self.assertIn(metrics.final_state, ("SUSPENDED", "QUALIFYING"))
        self.assertIsNotNone(metrics.intervention_delay)

    def test_human_only_never_executes_autonomously(self):
        metrics = Simulation(small("human_only"), seed=1).run()
        self.assertEqual(metrics.autonomous_executions, 0)
        self.assertEqual(metrics.human_reviews, metrics.horizon)
        self.assertFalse(metrics.qualified)

    def test_legacy_seeded_demo_is_marked_as_seeded_not_earned(self):
        metrics = Simulation(small("legacy_wilson_seeded"), seed=1).run()
        self.assertEqual(metrics.time_to_authority, 0)
        self.assertEqual(metrics.shadow_trials, 0)
        self.assertGreater(metrics.failures_after_autonomy, 0)

    def test_risk_scenario_records_denials_by_reason(self):
        metrics = Simulation(small("risk_envelopes"), seed=1).run()
        self.assertGreater(metrics.policy_denials, 0)
        reasons = set(metrics.denial_reasons)
        # since v3 Step 09 an amount outside every envelope is refused by the shipped
        # proposer, which derives the risk class from the host's envelopes; no proposal can
        # carry a mislabelled class, so RISK_CLASS_MISMATCH no longer occurs
        self.assertTrue(any(r.startswith("PROPOSER: no risk envelope") for r in reasons),
                        reasons)

    def test_censoring_is_reported(self):
        metrics = Simulation(small("censoring"), seed=1).run()
        self.assertGreater(metrics.shadow_censored, 0)
        self.assertIsNotNone(metrics.censored_fraction)


class ShippedLoopTests(unittest.TestCase):
    """
    Criterion 1: the harness drives the shipped loop, so each L6 difference is gone. One
    test per difference, each failing on the v0.3.x harness.
    """

    @classmethod
    def setUpClass(cls):
        cls.sim = Simulation(small("stable", horizon=200, audit_rate=0.2), seed=1)
        cls.metrics = cls.sim.run()
        cls.events = cls.sim.ledger.events

    def test_autonomous_executions_are_not_frozen_or_labelled(self):
        # L6 (1): the v0.3.x harness froze and labelled every autonomous execution
        key = self.sim.key
        self.assertGreater(self.metrics.autonomous_executions, 0)
        self.assertEqual(self.sim.shadow.counts(key, Provenance.AUTONOMOUS).frozen, 0)
        self.assertGreater(self.metrics.audit_executions, 0)
        self.assertGreaterEqual(self.sim.shadow.counts(key, Provenance.AUDIT).frozen,
                                self.metrics.audit_executions)

    def test_decisions_are_committed_by_the_executor_hook(self):
        # L6 (2): the harness committed decisions itself (or, before v3 Step 04, not at all)
        loop = self.sim.loop
        self.assertEqual(loop.executor.commit, loop.commit)
        committed = [e for e in self.events if e.event_type == "authority.decision.committed"]
        steps = [e for e in self.events if e.event_type == "execution.step.recorded"
                 and e.payload["decision_id"] is not None]
        self.assertEqual({e.payload["decision_id"] for e in committed},
                         {e.payload["decision_id"] for e in steps})
        self.assertGreater(len(committed), 0)

    def test_audits_are_frozen_before_their_token(self):
        # L6 (3): the harness froze audits after execution
        recorded = {e.payload["decision_id"]: e.sequence for e in self.events
                    if e.event_type == "execution.step.recorded"}
        audits = [e for e in self.events if e.event_type == "shadow.frozen"
                  and e.payload["provenance"] == "audit"]
        self.assertGreater(len(audits), 0)
        for frozen in audits:
            self.assertLess(frozen.sequence, recorded[frozen.aggregate_id])

    def test_human_approved_executions_go_through_the_gateway(self):
        # L6 (4): the harness ran the handler and counted a token without the gateway
        approved = self.sim.db.connection().execute(
            "SELECT COUNT(*) FROM tokens WHERE approval_id IS NOT NULL"
            " AND state = 'handler_completed'"
        ).fetchone()[0]
        self.assertGreater(self.metrics.human_approved_executions, 0)
        self.assertEqual(approved, self.metrics.human_approved_executions)
        self.assertEqual(self.metrics.unauthorized_side_effects, 0)

    def test_the_harness_calls_no_authority_api_itself(self):
        # every commit, freeze, issue, and redemption happens inside the shipped loop
        tree = ast.parse(Path(experiments.simulate.__file__).read_text())
        forbidden = {"commit_decision", "freeze", "issue", "redeem_remote", "accept_nonce",
                     "publish", "commit"}
        calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)]
        self.assertEqual([c.attr for c in calls if c.attr in forbidden], [])
        gateway_calls = [c.attr for c in calls if isinstance(c.value, ast.Attribute)
                         and c.value.attr == "gateway"]
        self.assertEqual(gateway_calls, [])


class ArtifactTests(unittest.TestCase):
    def run_all_small(self, root: Path) -> dict:
        main(["--all", "--output", str(root), "--seeds", "2", "--horizon", "120"])
        return json.loads((root / "manifest.json").read_text())

    def test_rerun_reproduces_the_canonical_hash(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = self.run_all_small(Path(a))
            second = self.run_all_small(Path(b))
        self.assertEqual(first["canonical_hash"], second["canonical_hash"])
        self.assertEqual(first["files"], second["files"])
        self.assertIn("generated_at", first["metadata_excluded_from_canonical_hash"])
        self.assertNotIn("generated_at", first["files"])

    def test_scenario_artifacts_exist_and_report_unqualified_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            summary = run_scenario(small("never_qualifies", horizon=120), out)
            for name in ("config.json", "runs.csv", "trace.jsonl", "summary.json", "trace.svg"):
                self.assertTrue((out / name).exists(), name)
            with (out / "runs.csv").open() as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["tta_censored"] == "True" for r in rows))
            self.assertEqual(summary["qualified_fraction"], 0.0)
            self.assertEqual(summary["time_to_authority"]["censored_runs"], 2)
            self.assertEqual(summary["unauthorized_side_effects_total"], 0)


if __name__ == "__main__":
    unittest.main()
