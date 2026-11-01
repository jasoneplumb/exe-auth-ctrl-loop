"""Consistency checks for the v3 contract and its revocation fixture (Step 01)."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "v3"
FIXTURE = json.loads((DOCS / "revocation_fixture.json").read_text())
SPEC = (DOCS / "spec.md").read_text()
PLAN = (DOCS / "plan.md").read_text()
EVENTS = FIXTURE["events"]

STEP_TAG = re.compile(r"\(Steps? \d{2}(, \d{2})*\)")
NORMATIVE = re.compile(r"\bMUST\b")
TEST_NAME = re.compile(r"^test_[a-z0-9_]+$")
SPEC_ROW = re.compile(r"^\| (RV-\d{2}) \| `([A-Z_]+)`[^|]*\| `([a-z_]+\.py)`")


def section(text: str, number: int) -> str:
    """Body of `## <number>.` up to the next level-2 heading."""
    match = re.search(rf"^## {number}\. .*?(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"spec has no section {number}")
    return match.group(0)


def spec_rows() -> list[tuple[str, str, str]]:
    return [m.groups() for line in section(SPEC, 5).splitlines() if (m := SPEC_ROW.match(line))]


class RevocationFixtureTest(unittest.TestCase):
    def test_ids_are_unique_and_sequential(self):
        expected = [f"RV-{i:02d}" for i in range(1, len(EVENTS) + 1)]
        self.assertEqual([e["id"] for e in EVENTS], expected)

    def test_fixture_matches_the_spec_table_row_for_row(self):
        expected = [(e["id"], e["event"], e["recorder"]) for e in EVENTS]
        self.assertEqual(spec_rows(), expected)

    def test_every_row_uses_declared_scopes_and_token_kinds(self):
        for e in EVENTS:
            self.assertIn(e["scope"], FIXTURE["scopes"], e["id"])
            self.assertIn(e["token_kinds"], FIXTURE["token_kinds"], e["id"])
            self.assertTrue(e["same_transaction_as"], e["id"])

    def test_every_recorder_is_a_planned_or_existing_module(self):
        src = ROOT / "src" / "exe_auth_ctrl_loop"
        for e in EVENTS:
            module = e["recorder"]
            self.assertTrue(
                (src / module).exists() or f"`{module}`" in PLAN,
                f"{e['id']}: {module} is neither in src/ nor in plan.md",
            )

    def test_every_row_and_boundary_case_names_a_unique_planned_test(self):
        rows = EVENTS + FIXTURE["not_revoking"] + FIXTURE["not_events"]
        names = [r["planned_test"] for r in rows]
        for name in names:
            self.assertRegex(name, TEST_NAME)
        self.assertEqual(len(names), len(set(names)))

    def test_planned_tests_exist_once_the_test_file_does(self):
        # Forward check: inert until Step 06 creates the file, binding from then on.
        path = ROOT / FIXTURE["planned_test_file"]
        if not path.exists():
            self.skipTest("planned at Step 06")
        body = path.read_text()
        rows = EVENTS + FIXTURE["not_revoking"] + FIXTURE["not_events"]
        for r in rows:
            self.assertIn(f"def {r['planned_test']}(", body, r["planned_test"])

    def test_spared_flag_agrees_with_token_kinds(self):
        for e in EVENTS:
            spared = e["token_kinds"] == "evidence_based"
            self.assertEqual(e["spares_approval_tokens"], spared, e["id"])

    def test_only_statistical_events_spare_approval_tokens(self):
        # Sentinel: adding an event that spares approvals needs a decision (spec §11, O-9).
        spared = {e["event"] for e in EVENTS if e["spares_approval_tokens"]}
        self.assertEqual(spared, {"GATE_LOST", "ORACLE_CHANGED"})

    def test_policy_change_is_one_event_that_retires(self):
        policy = next(e for e in EVENTS if e["event"] == "POLICY_CHANGED")
        self.assertTrue(policy["retires_partitions"])
        churn = re.search(r"`PARTITION_RETIRED` \(([^)]*)\)", SPEC)
        self.assertIsNotNone(churn)
        self.assertIn("other than `policy_version`", churn.group(1))

    def test_severe_failure_reaches_lineage(self):
        severe = next(e for e in EVENTS if e["event"] == "SEVERE_FAILURE")
        self.assertEqual(severe["scope"], "partition_and_lineage")

    def test_handler_started_tokens_are_reported_not_revoked(self):
        self.assertEqual(FIXTURE["handler_started"], "report_not_revoke")
        self.assertIn("effect unknown; not revocable, reported", section(SPEC, 5))


class SpecContractTest(unittest.TestCase):
    def test_spec_is_final(self):
        status = SPEC.splitlines()[2]
        self.assertTrue(status.startswith("Status: Step 01 contract (final"), status)

    def test_every_normative_requirement_names_its_step(self):
        missing = [
            line.strip()[:80]
            for line in SPEC.splitlines()[3:]
            if NORMATIVE.search(line) and not STEP_TAG.search(line)
        ]
        self.assertEqual(missing, [])

    def test_no_pending_rule_remains_for_a_decided_option(self):
        self.assertNotRegex(SPEC, r"[Pp]ending O-[2567]\b")
        decisions = section(SPEC, 11)
        for closed in ("O-1", "O-2", "O-5", "O-6", "O-7", "O-9"):
            self.assertRegex(decisions, rf"\| {closed} \| \*\*Closed \d{{4}}-\d{{2}}-\d{{2}}")
        for still_open in ("O-3", "O-4", "O-8"):
            self.assertRegex(decisions, rf"\| {still_open} \| Open until Step 09")

    def test_module_names_are_fixed_in_the_plan(self):
        self.assertNotIn("names provisional", PLAN)
        for module in ("store.py", "principals.py", "revocation.py", "tools.py"):
            self.assertIn(f"`{module}`", PLAN)


if __name__ == "__main__":
    unittest.main()
