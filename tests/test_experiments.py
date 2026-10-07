"""
Step 08: the simulation harness is deterministic, starts at n=0, and reports the bad news.
Reduced sizes keep the suite fast; the committed results/ use the full configs.
"""

import csv
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_outstanding_token_is_refused_after_a_suspension(self):
        metrics = Simulation(small("delayed_defection", horizon=300), seed=1).run()
        self.assertGreater(metrics.stale_token_denials, 0)
        self.assertLess(metrics.stale_token_denials, metrics.tokens_consumed)

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
        self.assertTrue({"RISK_CLASS_MISMATCH", "NO_RISK_ENVELOPE"} & reasons, reasons)

    def test_censoring_is_reported(self):
        metrics = Simulation(small("censoring"), seed=1).run()
        self.assertGreater(metrics.shadow_censored, 0)
        self.assertIsNotNone(metrics.censored_fraction)


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
