"""The durable substrate (v3 Step 02, phase 1): schema, transactions, append-only history."""

import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

from exe_auth_ctrl_loop.authority import PartitionKey
from exe_auth_ctrl_loop.store import (
    APPEND_ONLY_TABLES,
    MIGRATIONS,
    Store,
    StoreError,
    partition_text,
)

KEY = PartitionKey(
    "openai", "gpt", "p1", "anthropic", "claude", "e1", "t1", "pol-1", "env", "pay", "high", "low"
)

# One minimal valid row per append-only table, so UPDATE and DELETE have a row to hit.
SEED = (
    "INSERT INTO ledger VALUES (0, 'e', 'a', 'controller', 't', '{}', 'GENESIS', 'h')",
    "INSERT INTO ledger_anchor (sequence, event_hash, anchored_at) VALUES (0, 'h', 't')",
    "INSERT INTO evidence_snapshots VALUES ('k', 1, 'ev', 1, 0, 't', 't', 1, 0, NULL)",
    "INSERT INTO trials VALUES ('tr', 'shadow', 'k', 'd', 't', 0, 0)",
    "INSERT INTO labels VALUES ('tr', 'positive', 0, 't', 0)",
    "INSERT INTO epochs VALUES ('k', 3)",
    "INSERT INTO lineage VALUES ('k', 'k2', 'KEY_FIELD_CHURN', 't')",
    "INSERT INTO risk_charges (policy_version, budget_id, scope_key, amount, token_id)"
    " VALUES ('r1', 'b', 's', 1.0, 'tok')",
    "INSERT INTO risk_history (policy_version, partition, tool_name, token_id)"
    " VALUES ('r1', 'k', 'pay', 'tok')",
    "INSERT INTO decisions VALUES ('dec', 'digest', 'autonomous', 0)",
)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "authority.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()


class OpeningTests(StoreTestCase):
    def test_in_memory_paths_are_refused(self):
        for path in ("", ":memory:", "file::memory:?cache=shared"):
            with self.subTest(path=path), self.assertRaises(StoreError):
                Store(path)

    def test_schema_is_current_and_journal_is_wal(self):
        self.assertEqual(self.store.schema_version, len(MIGRATIONS))
        mode = self.store.connection().execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")
        self.assertEqual(
            self.store.connection().execute("PRAGMA synchronous").fetchone()[0], 2  # FULL
        )

    def test_reopening_keeps_rows_and_applies_nothing_twice(self):
        with self.store.transaction() as conn:
            conn.execute(SEED[0])
        self.store.close()
        with Store(self.path) as reopened:
            self.assertEqual(reopened.schema_version, len(MIGRATIONS))
            rows = reopened.connection().execute("SELECT sequence FROM ledger").fetchall()
            self.assertEqual(rows, [(0,)])

    def test_a_newer_schema_is_refused(self):
        self.store.connection().execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
        self.store.close()
        with self.assertRaisesRegex(StoreError, "newer than this code"):
            Store(self.path)

    def test_closed_store_refuses_use(self):
        self.store.close()
        with self.assertRaises(StoreError):
            self.store.connection()


class AppendOnlyTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        with self.store.transaction() as conn:
            for statement in SEED:
                conn.execute(statement)

    def test_every_history_table_refuses_update_and_delete_at_the_database(self):
        # Raw SQL on purpose: the guarantee is the trigger, not the absence of a method.
        conn = self.store.connection()
        for table in APPEND_ONLY_TABLES:
            for statement in (f"UPDATE {table} SET rowid = rowid", f"DELETE FROM {table}"):
                with self.subTest(statement=statement):
                    with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                        conn.execute(statement)
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 1, table)

    def test_seed_covers_every_append_only_table(self):
        seeded = {s.split()[2] for s in SEED}
        self.assertEqual(seeded, set(APPEND_ONLY_TABLES))

    def test_a_trial_resolves_at_most_once(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                conn.execute("INSERT INTO labels VALUES ('tr', NULL, 1, 't2', 0)")

    def test_label_and_censored_are_exclusive(self):
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO trials VALUES ('tr2', 'shadow', 'k', 'd', 't', 0, 1)")
        for row in ("('tr2', 'positive', 1, 't', 0)", "('tr2', NULL, 0, 't', 0)"):
            with self.subTest(row=row), self.assertRaises(sqlite3.IntegrityError):
                with self.store.transaction() as conn:
                    conn.execute(f"INSERT INTO labels VALUES {row}")

    def test_lifecycle_is_current_state_and_mutable(self):
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO lifecycle VALUES ('k', 'QUALIFYING', 0, 0, 0, NULL)")
            conn.execute("UPDATE lifecycle SET state = 'AUTONOMOUS' WHERE partition = 'k'")
        state = self.store.connection().execute("SELECT state FROM lifecycle").fetchone()[0]
        self.assertEqual(state, "AUTONOMOUS")


class TransactionTests(StoreTestCase):
    def count(self) -> int:
        return self.store.connection().execute("SELECT COUNT(*) FROM epochs").fetchone()[0]

    def test_exception_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as conn:
                conn.execute("INSERT INTO epochs VALUES ('k', 1)")
                raise RuntimeError("boom")
        self.assertEqual(self.count(), 0)

    def test_nested_transaction_joins_the_outer_one(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as outer:
                outer.execute("INSERT INTO epochs VALUES ('k', 1)")
                with self.store.transaction() as inner:
                    self.assertIs(inner, outer)
                    inner.execute("INSERT INTO epochs VALUES ('k', 2)")
                raise RuntimeError("after the inner block committed nothing on its own")
        self.assertEqual(self.count(), 0)

    def test_nested_success_commits_once_at_the_outer_end(self):
        with self.store.transaction() as outer:
            with self.store.transaction() as inner:
                inner.execute("INSERT INTO epochs VALUES ('k', 1)")
            self.assertTrue(outer.in_transaction)
        self.assertFalse(self.store.connection().in_transaction)
        self.assertEqual(self.count(), 1)

    def test_a_failed_commit_leaves_no_open_transaction(self):
        # A deferred foreign-key violation passes the INSERT and fails at COMMIT.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as conn:
                conn.execute("PRAGMA defer_foreign_keys = ON")
                conn.execute("INSERT INTO labels VALUES ('no-such-trial', 'positive', 0, 't', 0)")
        conn = self.store.connection()
        self.assertFalse(conn.in_transaction)
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO epochs VALUES ('k', 1)")
        self.assertEqual(self.count(), 1)

    def test_connections_are_per_thread(self):
        seen = []
        thread = threading.Thread(target=lambda: seen.append(self.store.connection()))
        thread.start()
        thread.join()
        self.assertIsNot(seen[0], self.store.connection())
        self.assertIs(self.store.connection(), self.store.connection())

    def test_an_open_transaction_blocks_another_writer(self):
        other = Store(self.path, timeout=0.2)
        errors = []

        def write():
            try:
                with other.transaction() as conn:
                    conn.execute("INSERT INTO epochs VALUES ('k', 2)")
            except sqlite3.OperationalError as exc:
                errors.append(str(exc))

        try:
            with self.store.transaction() as conn:
                conn.execute("INSERT INTO epochs VALUES ('k', 1)")
                thread = threading.Thread(target=write)
                thread.start()
                thread.join()
            self.assertEqual(len(errors), 1)
            self.assertIn("locked", errors[0])
            self.assertEqual(self.count(), 1)
        finally:
            other.close()


class CrossProcessTests(StoreTestCase):
    WORKER = textwrap.dedent("""
        import sys
        from exe_auth_ctrl_loop.store import Store
        store = Store(sys.argv[1])
        for _ in range(int(sys.argv[2])):
            with store.transaction() as conn:
                (n,) = conn.execute("SELECT n FROM counter").fetchone()
                conn.execute("UPDATE counter SET n = ?", (n + 1,))
        store.close()
    """)

    def test_two_processes_serialize_read_modify_write(self):
        # With a deferred BEGIN the read takes no write lock, so one worker's upgrade to a
        # write fails (or, without WAL, an increment is lost); either way this fails.
        rounds = 200
        with self.store.transaction() as conn:
            conn.execute("CREATE TABLE counter (n INTEGER NOT NULL)")
            conn.execute("INSERT INTO counter VALUES (0)")
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", self.WORKER, str(self.path), str(rounds)],
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        for worker in workers:
            _, err = worker.communicate(timeout=120)
            self.assertEqual(worker.returncode, 0, err)
        (n,) = self.store.connection().execute("SELECT n FROM counter").fetchone()
        self.assertEqual(n, 2 * rounds)


class PartitionTextTests(unittest.TestCase):
    def test_equal_keys_give_equal_text_independent_of_order(self):
        text = partition_text(KEY)
        self.assertEqual(text, partition_text(PartitionKey(**vars(KEY))))
        self.assertTrue(text.startswith('{"confidence_bin":'))

    def test_non_dataclass_is_refused(self):
        for value in ({"a": 1}, PartitionKey):
            with self.subTest(value=value), self.assertRaises(TypeError):
                partition_text(value)


if __name__ == "__main__":
    unittest.main()
