"""Consistency checks for the v2 lifecycle fixture and spec (Step 01)."""

import json
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "v2"
FIXTURE = json.loads((DOCS / "lifecycle_fixture.json").read_text())
SPEC = (DOCS / "spec.md").read_text()
THREAT = (DOCS / "threat-model.md").read_text()
TRANSITIONS = FIXTURE["transitions"]


def step(state, event, guard):
    """Reference lookup: the fixture row for (state, event, guard); None means undefined."""
    for t in TRANSITIONS:
        if t["from"] == state and t["event"] == event and t["guard"] == guard:
            return t
    return None


class LifecycleFixtureTest(unittest.TestCase):
    def test_enough_transitions_and_unique_ids(self):
        ids = [t["id"] for t in TRANSITIONS]
        self.assertGreaterEqual(len(ids), 12)
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_row_uses_declared_states_and_events(self):
        for t in TRANSITIONS:
            self.assertIn(t["from"], FIXTURE["states"] + [None], t["id"])
            self.assertIn(t["to"], FIXTURE["states"], t["id"])
            self.assertIn(t["event"], FIXTURE["events"], t["id"])

    def test_every_state_has_a_severe_failure_to_suspended(self):
        for state in ("UNESTABLISHED", "QUALIFYING", "AUTONOMOUS"):
            row = next(
                t for t in TRANSITIONS if t["from"] == state and t["event"] == "SEVERE_FAILURE"
            )
            self.assertEqual(row["to"], "SUSPENDED")
            self.assertFalse(row["blocked"])

    def test_only_qualifying_gate_met_enters_autonomous(self):
        entries = [
            t for t in TRANSITIONS
            if t["to"] == "AUTONOMOUS" and t["from"] != "AUTONOMOUS" and not t["blocked"]
        ]
        self.assertEqual([(t["from"], t["event"]) for t in entries], [("QUALIFYING", "GATE_MET")])

    def test_suspended_exits_only_via_human_release_to_qualifying(self):
        exits = [
            t for t in TRANSITIONS
            if t["from"] == "SUSPENDED" and t["to"] != "SUSPENDED" and not t["blocked"]
        ]
        self.assertEqual([t["event"] for t in exits], ["HUMAN_RELEASE"])
        self.assertEqual(exits[0]["to"], "QUALIFYING")
        self.assertEqual(exits[0]["epoch_delta"], 1)
        self.assertTrue(exits[0]["reset_counters"])

    def test_empty_store_cannot_reach_autonomy_directly(self):
        self.assertEqual(step(None, "FIRST_PROPOSAL", "none")["to"], "UNESTABLISHED")
        blocked = step("UNESTABLISHED", "GATE_MET", "n_below_n_min")
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["to"], "UNESTABLISHED")

    def test_n0_trace_reaches_autonomy_only_through_qualifying(self):
        state = step(None, "FIRST_PROPOSAL", "none")["to"]
        for event, guard in (
            ("TRIAL_ADMITTED", "admissible"),
            ("GATE_MET", "all_entry_guards"),
        ):
            row = step(state, event, guard)
            self.assertIsNotNone(row, (state, event))
            self.assertFalse(row["blocked"])
            state = row["to"]
        self.assertEqual(state, "AUTONOMOUS")

    def test_human_approved_evidence_is_never_counted(self):
        self.assertFalse(FIXTURE["evidence_rules"]["HUMAN_APPROVED"]["counts"])
        for state in ("UNESTABLISHED", "AUTONOMOUS"):
            rows = [
                t for t in TRANSITIONS
                if t["from"] == state and t["event"] == "HUMAN_APPROVED_OUTCOME"
            ]
            self.assertTrue(rows and all(t["blocked"] and t["to"] == state for t in rows))

    def test_inconclusive_never_counts_as_success(self):
        for prov in ("SHADOW", "AUTONOMOUS", "AUDIT"):
            self.assertEqual(FIXTURE["evidence_rules"][prov]["inconclusive"], "censored")

    def test_churn_from_suspended_taints_successor(self):
        row = step("SUSPENDED", "KEY_FIELD_CHURN", "key_field_differs")
        self.assertTrue(row["retired"])
        self.assertTrue(row["successor_lineage_suspended"])
        self.assertEqual(row["successor"], "UNESTABLISHED")


class SpecDocumentTest(unittest.TestCase):
    def test_spec_names_every_state_provenance_and_transition(self):
        for name in FIXTURE["states"] + FIXTURE["provenance"]:
            self.assertIn(name, SPEC, name)
        spec_ids = set(re.findall(r"\bT-\d+", SPEC))
        fixture_ids = {re.match(r"T-\d+", t["id"]).group(0) for t in TRANSITIONS}
        self.assertEqual(spec_ids, fixture_ids)

    def test_spec_states_estimand_and_assumptions(self):
        for needle in ("Estimand", "time-uniform", "A1", "A5", "not** establish real-world"):
            self.assertIn(needle, SPEC, needle)

    def test_threat_model_covers_required_classes(self):
        for needle in (
            "Trusted", "adversarial", "Fallible", "Audit RNG", "Ledger",
            "Unsupported adversaries", "Prototype versus production",
        ):
            self.assertIn(needle, THREAT, needle)


if __name__ == "__main__":
    unittest.main()
