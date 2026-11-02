"""Test support: temporary durable stores, and tampering as a writer of the file can."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Sequence

from exe_auth_ctrl_loop.ledger import EventLedger, LedgerEvent
from exe_auth_ctrl_loop.store import Store, canonical_json, time_text

# Every store opened through scratch_store(), closed after each test by conftest.py.
OPEN: list[tuple[Store, tempfile.TemporaryDirectory[str]]] = []


def scratch_store() -> Store:
    """A fresh database file in its own temporary directory, removed after the test."""
    directory = tempfile.TemporaryDirectory()
    store = Store(Path(directory.name) / "authority.db")
    OPEN.append((store, directory))
    return store


def close_all() -> None:
    while OPEN:
        store, directory = OPEN.pop()
        store.close()
        directory.cleanup()


def rewrite_events(ledger: EventLedger, events: Sequence[LedgerEvent]) -> None:
    """
    Replace the ledger's rows with `events`, as someone with write access to the database
    file can: drop the append-only triggers, rewrite, and put the triggers back. This is
    outside the trust boundary on purpose; the chain-limit tests (claims matrix C22) pin
    what verify() can and cannot see after such a rewrite.
    """
    with ledger.store.transaction() as conn:
        triggers = [
            row[0] for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'ledger'"
            )
        ]
        for name in ("ledger_no_update", "ledger_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DELETE FROM ledger")
        conn.executemany(
            "INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (e.sequence, e.event_type, e.aggregate_id, e.actor, time_text(e.occurred_at),
                 canonical_json(dict(e.payload)), e.previous_hash, e.event_hash)
                for e in events
            ],
        )
        for sql in triggers:
            conn.execute(sql)


def forge_token(store: Store, token_id: str, **columns: object) -> None:
    """
    Overwrite fields of a stored token that are fixed at issue, as someone with write
    access to the database file can: drop the `tokens_fixed` trigger, update, restore it.
    Values are given as stored (e.g. allowed_effects as a JSON list).
    """
    with store.transaction() as conn:
        known = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
        unknown = set(columns) - known
        if unknown:
            raise ValueError(f"not token columns: {sorted(unknown)}")
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'tokens_fixed'"
        ).fetchone()
        conn.execute("DROP TRIGGER tokens_fixed")
        assignments = ", ".join(f'"{name}" = ?' for name in columns)
        conn.execute(
            f"UPDATE tokens SET {assignments} WHERE token_id = ?", (*columns.values(), token_id)
        )
        conn.execute(sql)
