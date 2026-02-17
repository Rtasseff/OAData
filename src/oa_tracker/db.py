"""SQLite database: schema creation, connection helper, query/update functions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS archives (
    publication_id          TEXT PRIMARY KEY,
    folder_path             TEXT NOT NULL,
    first_seen_at           TEXT NOT NULL,
    became_active_at        TEXT,
    last_seen_at            TEXT NOT NULL,
    last_changed_at         TEXT,
    status                  TEXT NOT NULL,
    final_pid               TEXT,
    final_url               TEXT,
    notes                   TEXT,
    last_notified_at        TEXT,
    reminder_count          INTEGER NOT NULL DEFAULT 0,
    next_reminder_at        TEXT,
    unexpected_missing_folder INTEGER NOT NULL DEFAULT 0,
    missing_folder_detected_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    publication_id  TEXT NOT NULL,
    action_code     TEXT NOT NULL,
    old_status      TEXT,
    new_status      TEXT,
    pid             TEXT,
    url             TEXT,
    note            TEXT,
    source          TEXT NOT NULL
);
"""


def init_db(path: Path) -> None:
    """Create the database and tables if they don't exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        conn.executescript(_SCHEMA_SQL)
        # Set schema version if empty
        row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row[0] == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))


@contextmanager
def get_connection(path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Query helpers ─────────────────────────────────────────────────────

def get_archive(conn: sqlite3.Connection, pub_id: str) -> dict[str, Any] | None:
    """Return a single archive row as a dict, or None."""
    row = conn.execute(
        "SELECT * FROM archives WHERE publication_id = ?", (pub_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_archives(conn: sqlite3.Connection, status_filter: str | None = None) -> list[dict[str, Any]]:
    """Return all archives, optionally filtered by status."""
    if status_filter:
        rows = conn.execute(
            "SELECT * FROM archives WHERE status = ? ORDER BY publication_id",
            (status_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM archives ORDER BY publication_id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_archives_by_status(conn: sqlite3.Connection, statuses: set[str]) -> list[dict[str, Any]]:
    """Return archives matching any of the given statuses."""
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT * FROM archives WHERE status IN ({placeholders}) ORDER BY publication_id",
        tuple(statuses),
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_archives(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all archives with OPEN status."""
    rows = conn.execute(
        "SELECT * FROM archives WHERE status LIKE 'OPEN_%' ORDER BY publication_id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_reminders_due(conn: sqlite3.Connection, now: str | None = None) -> list[dict[str, Any]]:
    """Return archives where a reminder is due."""
    now = now or _now()
    rows = conn.execute(
        "SELECT * FROM archives WHERE next_reminder_at IS NOT NULL AND next_reminder_at <= ? "
        "AND status LIKE 'OPEN_%' ORDER BY next_reminder_at",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_events(conn: sqlite3.Connection, since: str) -> list[dict[str, Any]]:
    """Return events since a given ISO timestamp."""
    rows = conn.execute(
        "SELECT * FROM events WHERE ts >= ? ORDER BY ts DESC",
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Mutation helpers ──────────────────────────────────────────────────

def upsert_archive(conn: sqlite3.Connection, **kwargs: Any) -> None:
    """Insert or update an archive row. kwargs must include publication_id."""
    pub_id = kwargs["publication_id"]
    existing = get_archive(conn, pub_id)

    if existing is None:
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(
            f"INSERT INTO archives ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
    else:
        sets = ", ".join(f"{k} = ?" for k in kwargs if k != "publication_id")
        vals = [v for k, v in kwargs.items() if k != "publication_id"]
        vals.append(pub_id)
        conn.execute(
            f"UPDATE archives SET {sets} WHERE publication_id = ?",
            vals,
        )


def update_archive_status(
    conn: sqlite3.Connection,
    pub_id: str,
    new_status: str,
    **extra: Any,
) -> None:
    """Update the status (and any extra fields) for an archive."""
    sets = ["status = ?"]
    vals: list[Any] = [new_status]
    for k, v in extra.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(pub_id)
    conn.execute(
        f"UPDATE archives SET {', '.join(sets)} WHERE publication_id = ?",
        vals,
    )


def insert_event(
    conn: sqlite3.Connection,
    publication_id: str,
    action_code: str,
    old_status: str | None,
    new_status: str | None,
    source: str,
    pid: str | None = None,
    url: str | None = None,
    note: str | None = None,
) -> None:
    """Insert an audit event."""
    conn.execute(
        "INSERT INTO events (ts, publication_id, action_code, old_status, new_status, pid, url, note, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), publication_id, action_code, old_status, new_status, pid, url, note, source),
    )
