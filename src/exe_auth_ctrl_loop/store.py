"""
Intent: Hold every piece of authority state in one database, so a restart or a second
        process sees exactly what the first one decided (docs/v3/spec.md §2)
Context: The substrate for v3. The evidence store, ledger, shadow log, lifecycle, and risk
        budgets keep their v0.3.1 semantics and read and write through this module.
Pattern: Every read-then-write is one `BEGIN IMMEDIATE` transaction, which takes SQLite's
        write lock at the start rather than at the first write, so two writers are
        serialized instead of racing to upgrade a read lock. History tables are
        append-only by database trigger, not by the absence of a method.
Future: One file on one host. Nothing here claims anything across hosts (spec §1).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

# constraint: the tables whose rows are history. UPDATE and DELETE on them are refused by
# triggers (spec §2 rule 3). `lifecycle` is deliberately absent: it is the current state
# of a state machine, and every change to it is a ledger event.
_V1_APPEND_ONLY = (
    "ledger",
    "ledger_anchor",
    "evidence_snapshots",
    "trials",
    "labels",
    "epochs",
    "lineage",
    "risk_charges",
    "risk_history",
)

_SCHEMA_V1 = (
    """CREATE TABLE ledger (
        sequence INTEGER PRIMARY KEY,
        event_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        event_hash TEXT NOT NULL
    )""",
    """CREATE TABLE ledger_anchor (
        anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence INTEGER NOT NULL,
        event_hash TEXT NOT NULL,
        anchored_at TEXT NOT NULL
    )""",
    """CREATE TABLE evidence_snapshots (
        partition TEXT NOT NULL,
        version INTEGER NOT NULL,
        evidence_id TEXT NOT NULL,
        successes INTEGER NOT NULL,
        failures INTEGER NOT NULL,
        collected_from TEXT NOT NULL,
        collected_until TEXT NOT NULL,
        valid INTEGER NOT NULL,
        suspended INTEGER NOT NULL,
        invalidation_reason TEXT,
        PRIMARY KEY (partition, version)
    )""",
    """CREATE TABLE trials (
        trial_id TEXT PRIMARY KEY,
        provenance TEXT NOT NULL,
        partition TEXT NOT NULL,
        proposal_digest TEXT NOT NULL,
        frozen_at TEXT NOT NULL,
        frozen_us INTEGER NOT NULL,
        frozen_sequence INTEGER NOT NULL
    )""",
    # constraint: one row per trial, so a trial is labelled or censored at most once by
    # the primary key itself. `label` is NULL exactly when the trial was censored.
    """CREATE TABLE labels (
        trial_id TEXT PRIMARY KEY REFERENCES trials (trial_id),
        label TEXT,
        censored INTEGER NOT NULL,
        resolved_at TEXT NOT NULL,
        resolved_us INTEGER NOT NULL,
        CHECK ((censored = 1) = (label IS NULL))
    )""",
    """CREATE TABLE epochs (
        partition TEXT NOT NULL,
        floor_sequence INTEGER NOT NULL,
        PRIMARY KEY (partition, floor_sequence)
    )""",
    """CREATE TABLE lifecycle (
        partition TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        epoch INTEGER NOT NULL,
        retired INTEGER NOT NULL,
        lineage_suspended INTEGER NOT NULL,
        suspension_reason TEXT
    )""",
    """CREATE TABLE lineage (
        predecessor TEXT NOT NULL,
        successor TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (predecessor, successor)
    )""",
    """CREATE TABLE risk_charges (
        charge_id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_version TEXT NOT NULL,
        budget_id TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        amount REAL NOT NULL,
        token_id TEXT NOT NULL
    )""",
    """CREATE TABLE risk_history (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_version TEXT NOT NULL,
        partition TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        token_id TEXT NOT NULL
    )""",
    # constraint: charges are keyed by budget and scope, not policy version; they survive
    # RiskPolicy.update() exactly as the v0.3.1 in-memory totals did
    "CREATE INDEX risk_charges_scope ON risk_charges (budget_id, scope_key)",
    "CREATE INDEX risk_history_partition ON risk_history (partition)",
    "CREATE INDEX trials_partition ON trials (partition, frozen_sequence)",
    *(
        statement
        for table in _V1_APPEND_ONLY
        for statement in (
            f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
            f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
        )
    ),
)

# v3 Step 03: capabilities (docs/v3/spec.md §3). A token row is current state, not history,
# so it is not append-only; instead its legal transitions are enforced here, and every
# column but the state and the revocation reason is fixed at issue.
TOKEN_STATES = (
    "issued", "revoked", "consumed", "handler_started", "handler_completed", "handler_failed",
)
_TOKEN_TRANSITIONS = (
    ("issued", "revoked"),
    ("issued", "consumed"),
    ("consumed", "handler_started"),
    ("handler_started", "handler_completed"),
    ("handler_started", "handler_failed"),
)
_SCHEMA_V2 = (
    f"""CREATE TABLE tokens (
        token_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        proposal_digest TEXT NOT NULL,
        allowed_tool TEXT NOT NULL,
        allowed_effects TEXT NOT NULL,
        partition TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        evidence_id TEXT,
        evidence_version INTEGER,
        expires_at TEXT NOT NULL,
        human_approved INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN ({", ".join(f"'{s}'" for s in TOKEN_STATES)})),
        revocation_reason TEXT
    )""",
    "CREATE INDEX tokens_state ON tokens (state)",
    # constraint: a same-state update is also refused, so an UPDATE that loses its
    # `WHERE state = 'issued'` condition fails loudly instead of re-consuming a token
    f"""CREATE TRIGGER tokens_transition BEFORE UPDATE OF state ON tokens
        WHEN NOT ({" OR ".join(
            f"(OLD.state = '{a}' AND NEW.state = '{b}')" for a, b in _TOKEN_TRANSITIONS
        )})
        BEGIN SELECT RAISE(ABORT, 'illegal token transition'); END""",
    """CREATE TRIGGER tokens_fixed BEFORE UPDATE ON tokens
        WHEN NEW.token_id IS NOT OLD.token_id
          OR NEW.decision_id IS NOT OLD.decision_id
          OR NEW.proposal_digest IS NOT OLD.proposal_digest
          OR NEW.allowed_tool IS NOT OLD.allowed_tool
          OR NEW.allowed_effects IS NOT OLD.allowed_effects
          OR NEW.partition IS NOT OLD.partition
          OR NEW.policy_version IS NOT OLD.policy_version
          OR NEW.evidence_id IS NOT OLD.evidence_id
          OR NEW.evidence_version IS NOT OLD.evidence_version
          OR NEW.expires_at IS NOT OLD.expires_at
          OR NEW.human_approved IS NOT OLD.human_approved
          OR (NEW.revocation_reason IS NOT OLD.revocation_reason
              AND NOT (OLD.state = 'issued' AND NEW.state = 'revoked'))
        BEGIN SELECT RAISE(ABORT, 'token fields are fixed at issue'); END""",
    """CREATE TRIGGER tokens_no_delete BEFORE DELETE ON tokens
        BEGIN SELECT RAISE(ABORT, 'tokens are never deleted'); END""",
)

# v3 Step 04: committed decisions (docs/v3/spec.md §3 rule 1). Written only by
# EventLedger.commit_decision, in the same transaction as the ledger event, and read by
# ExecutionGateway.issue(), which refuses any decision without a matching row.
_SCHEMA_V3 = (
    """CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY,
        proposal_digest TEXT NOT NULL,
        route TEXT NOT NULL,
        ledger_sequence INTEGER NOT NULL
    )""",
    """CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
        BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END""",
    """CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
        BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END""",
)

# constraint: every table whose rows are history, across all schema versions
APPEND_ONLY_TABLES = (*_V1_APPEND_ONLY, "decisions")

# constraint: one entry per schema version, applied in order and never edited once
# released. A later step adds a version; it does not change an earlier one.
MIGRATIONS: tuple[tuple[str, ...], ...] = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3)


class StoreError(RuntimeError):
    """The database cannot be used: wrong path kind, newer schema, or closed."""


def time_text(value: datetime) -> str:
    """Exact text form of a timestamp; parsed back with datetime.fromisoformat."""
    return value.isoformat()


def time_us(value: datetime) -> int:
    """
    intent: An integer that orders timestamps correctly in SQL
    constraint: ISO strings with different UTC offsets do not sort by instant, so every
                ordering query uses this column instead. A naive datetime is read as UTC,
                which is how every clock in this package reports a naive time, if any.
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return delta_us(aware - datetime(1970, 1, 1, tzinfo=timezone.utc))


def delta_us(delta: timedelta) -> int:
    """A duration in whole microseconds, exactly (no float rounding)."""
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def canonical_json(value: Any) -> str:
    """The encoding digest() hashes: sorted keys, no whitespace, str() for the rest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def partition_text(key: Any) -> str:
    """
    intent: One stable text form of a partition key, for use as a column value
    constraint: Canonical JSON of the dataclass fields, so equal keys always compare equal
                in SQL and the form never depends on field order or Python's repr. Takes
                any dataclass instance so this module never imports authority.py, which
                will import it.
    """
    if not is_dataclass(key) or isinstance(key, type):
        raise TypeError("partition_text needs a dataclass instance")
    return json.dumps(asdict(key), sort_keys=True, separators=(",", ":"))


class Store:
    """
    intent: Own the database file, its schema, and the transaction discipline every
            caller uses
    constraint: Requires a real file. `:memory:` and the empty path are refused, because
                each connection to them is a separate database: threads would not share
                state and the durability claims would be false (spec §2 rule 5, O-7).
    constraint: One connection per thread, created on first use. sqlite3 connections are
                not shared across threads, and a per-thread connection is also what makes
                the in-process concurrency tests mean the same thing as cross-process ones.
    """

    def __init__(self, path: str | os.PathLike[str], *, timeout: float = 30.0) -> None:
        text = os.fspath(path)
        if text in ("", ":memory:") or text.startswith("file::memory:"):
            raise StoreError("a database file is required; in-memory databases are not durable")
        self.path = text
        self.timeout = timeout
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._registry_lock = threading.Lock()
        self._closed = False
        self._migrate()

    def connection(self) -> sqlite3.Connection:
        """
        intent: This thread's connection, for reads and for work inside transaction()
        effect: Outside a transaction each statement reads a consistent WAL snapshot of
                its own; a read that must agree with a later write belongs in transaction()
        """
        if self._closed:
            raise StoreError("store is closed")
        conn: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if conn is None:
            # constraint: isolation_level=None turns off the sqlite3 module's implicit
            # BEGIN (DEFERRED), so the only transactions are the explicit IMMEDIATE ones
            # opened below. Works the same on Python 3.10 and 3.12+.
            # constraint: check_same_thread=False only so close() can close connections
            # opened by other threads; each connection is still used by one thread only.
            conn = sqlite3.connect(
                self.path, timeout=self.timeout, isolation_level=None, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            # effect: FULL syncs the WAL on every commit, so a committed consume or
            # suspension survives power loss, which NORMAL does not promise
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            # constraint: registered and re-checked under the lock close() takes, so a
            # connection opened while close() runs is closed, never left behind it
            with self._registry_lock:
                if self._closed:
                    conn.close()
                    raise StoreError("store is closed")
                self._connections.append(conn)
            self._local.connection = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """
        intent: Run a read-then-write as one indivisible step (spec §2 rule 2)
        method: `BEGIN IMMEDIATE` takes the database write lock before the first read, so
                no other connection, in this process or another, can commit between this
                transaction's reads and its writes. Commit on success, roll back on any
                exception.
        effect: Re-entrant on one thread: a nested call joins the outer transaction and
                neither begins nor commits, so a helper that writes (a scoped revoke, a
                ledger append) can run inside its caller's transaction. An exception
                anywhere rolls back the whole outer transaction.
        """
        conn = self.connection()
        if self._local.depth:
            self._local.depth += 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            # effect: also reached when COMMIT itself fails (e.g. busy), so a failed
            # commit never leaves the transaction open for this thread's next caller
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            self._local.depth = 0

    @property
    def schema_version(self) -> int:
        return int(self.connection().execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        """Close every connection this store opened, from any thread."""
        with self._registry_lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()
            self._closed = True

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        """
        intent: Bring the file to the current schema, once, whoever opens it first
        constraint: Runs inside BEGIN IMMEDIATE, so two processes opening a new file at
                    once apply the migrations exactly once. Statements are executed one by
                    one: executescript() would commit the open transaction first.
        effect: A file written by newer code (a higher user_version) is refused rather
                than read with a schema this code does not know
        """
        with self.transaction() as conn:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current > len(MIGRATIONS):
                raise StoreError(
                    f"database schema version {current} is newer than this code "
                    f"({len(MIGRATIONS)})"
                )
            for version in range(current, len(MIGRATIONS)):
                for statement in MIGRATIONS[version]:
                    conn.execute(statement)
                # constraint: PRAGMA does not take parameters; the value is an int we own
                conn.execute(f"PRAGMA user_version = {version + 1}")
