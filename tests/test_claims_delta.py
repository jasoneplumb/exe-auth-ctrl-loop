"""
v3 Step 10 (criterion 1): docs/v3/claims-delta.md gives every row of the paper-v2 claims
matrix exactly one verdict, and every test and path it cites exists, so the 0.4.0 statement
of a claim cannot point at evidence that is not there.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = (ROOT / "docs" / "paper-v2" / "claims-matrix.md").read_text()
DELTA = (ROOT / "docs" / "v3" / "claims-delta.md").read_text()
VERDICTS = {"kept", "changed", "closed"}
PATH_PREFIXES = ("results-v3/", "results/", "docs/", "experiments/", "tests/", "SECURITY",
                 "CHANGELOG")


def row_ids(text: str) -> list[str]:
    return re.findall(r"^\| ([CLN]\d+) \|", text, flags=re.MULTILINE)


def delta_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in DELTA.splitlines():
        match = re.match(r"^\| ([CLN]\d+) \|", line)
        if match:
            rows.setdefault(match.group(1), []).append(line)
    return rows


def cited_tests() -> set[tuple[str, str]]:
    """(file, name) for every `file.py::A::B` and every `::name` that continues one."""
    found = set()
    for line in DELTA.splitlines():
        current = None
        for token in re.findall(r"`([^`]+)`", line):
            if "::" not in token:
                continue
            head, *names = token.split("::")
            if head.startswith("test_") and head.endswith(".py"):
                current = head
            elif head:
                continue
            if current is None:
                continue
            for name in names:
                found.add((current, name))
    return found


class ClaimsDeltaTests(unittest.TestCase):
    def test_every_matrix_row_has_exactly_one_verdict(self):
        matrix = row_ids(MATRIX)
        self.assertEqual(len(matrix), 46)  # C1-C33, L1-L13
        rows = delta_rows()
        for row in matrix:
            with self.subTest(row=row):
                self.assertEqual(len(rows.get(row, [])), 1)
                verdict = rows[row][0].split(" | ")[1].strip()
                self.assertIn(verdict, VERDICTS)
        self.assertEqual({r for r in rows if not r.startswith("N")}, set(matrix))

    def test_every_cited_test_exists(self):
        cited = cited_tests()
        self.assertGreater(len(cited), 60)
        for file, name in sorted(cited):
            with self.subTest(file=file, name=name):
                source = (ROOT / "tests" / file).read_text()
                self.assertRegex(source, rf"(?m)^\s*(class|def) {re.escape(name)}\b")

    def test_every_cited_path_exists(self):
        for token in re.findall(r"`([^`]+)`", DELTA):
            path = token.split(" ")[0].rstrip("/")
            if path.startswith(PATH_PREFIXES):
                with self.subTest(path=path):
                    self.assertTrue((ROOT / path).exists(), path)

    def test_new_rows_cover_delegation_and_step_09(self):
        rows = delta_rows()
        self.assertIn("F5", rows["N1"][0])
        self.assertIn("Step 09", rows["N3"][0])


if __name__ == "__main__":
    unittest.main()
