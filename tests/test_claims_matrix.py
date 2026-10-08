"""
Step 09: every artifact the claims matrix points at exists, so a claim cannot outlive its
evidence without this failing. Commit SHAs are not checked (CI checkouts are shallow).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = (ROOT / "docs" / "paper-v2" / "claims-matrix.md").read_text()
KNOWN_FILES = {
    "authority.py", "shadow.py", "lifecycle.py", "sequential.py", "risk.py", "tools.py",
    "executor.py", "ledger.py", "pipeline.py",
}


def referenced_paths() -> set[str]:
    paths = set()
    for match in re.findall(r"`([^`]+)`", MATRIX):
        token = match.split("::")[0].split(" ")[0]
        if token.startswith(("results/", "docs/", "experiments/", "tests/", "SECURITY")):
            paths.add(token.rstrip("/"))
        elif token in KNOWN_FILES:
            paths.add(f"src/exe_auth_ctrl_loop/{token}")
    return paths


def referenced_tests() -> set[tuple[str, str]]:
    found = set()
    for match in re.findall(r"`([^`]+)`", MATRIX):
        if "::" not in match:
            continue
        file_part, *names = match.split("::")
        if file_part.startswith("test_") and file_part.endswith(".py"):
            for name in names:
                found.add((file_part, name))
    # bare `::name` references inherit the file from the previous full reference in the row
    for row in MATRIX.splitlines():
        current = None
        for match in re.findall(r"`([^`]+)`", row):
            if match.startswith("test_") and "::" in match:
                current = match.split("::")[0]
            elif match.startswith("::") and current:
                found.add((current, match[2:]))
    return found


class ClaimsMatrixTests(unittest.TestCase):
    def test_every_referenced_path_exists(self):
        missing = sorted(p for p in referenced_paths() if not (ROOT / p).exists())
        self.assertEqual(missing, [])
        self.assertGreater(len(referenced_paths()), 20)

    def test_every_referenced_test_exists(self):
        missing = []
        for file_name, name in sorted(referenced_tests()):
            source = (ROOT / "tests" / file_name).read_text()
            if not re.search(rf"\b(def|class) {re.escape(name)}\b", source):
                missing.append(f"{file_name}::{name}")
        self.assertEqual(missing, [])
        self.assertGreater(len(referenced_tests()), 30)

    def test_canonical_hash_in_matrix_matches_manifest(self):
        import json

        manifest = json.loads((ROOT / "results" / "manifest.json").read_text())
        self.assertIn(manifest["canonical_hash"], MATRIX)


if __name__ == "__main__":
    unittest.main()
