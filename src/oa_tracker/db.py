"""SQLite database: schema creation, connection helper, query/update functions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

_SCHEMA_VERSION = 4

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
    missing_folder_detected_at TEXT,
    -- v2: pub-DB cached fields (auto-refreshed every scan)
    pub_title                TEXT,
    pub_doi                  TEXT,
    pub_journal              TEXT,
    pub_year                 INTEGER,
    oa_paper_required        INTEGER,
    oa_data_required         INTEGER,
    max_embargo_months       INTEGER,
    oa_mandate_source        TEXT,
    oa_mandate_missing       INTEGER,
    corresponding_author_name  TEXT,
    corresponding_author_email TEXT,
    central_repository       TEXT,
    central_repository_code  TEXT,
    pub_db_last_refreshed_at TEXT,
    -- v2: operator-managed fields (preserved across scans when *_overridden=1)
    data_contact_name        TEXT,
    data_contact_email       TEXT,
    data_contact_overridden  INTEGER NOT NULL DEFAULT 0,
    zenodo_code              TEXT,
    zenodo_code_overridden   INTEGER NOT NULL DEFAULT 0,
    -- v3: SharePoint sync bookkeeping + corresponding-author override
    sharepoint_item_id       INTEGER,
    sharepoint_synced_at     TEXT,
    corresponding_author_overridden INTEGER NOT NULL DEFAULT 0,
    -- v4: automation — package detection (scanner), the user's Tracker
    -- "done" flag (SharePoint pull), and the Zenodo draft's reserved DOI
    -- + environment (so a sandbox draft is never mistaken for production)
    package_has_zip          INTEGER,
    package_has_readme       INTEGER,
    package_checked_at       TEXT,
    user_done_flag           INTEGER NOT NULL DEFAULT 0,
    user_done_at             TEXT,
    zenodo_doi               TEXT,
    zenodo_env               TEXT
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

# v1 → v2: ALTER TABLE adds for existing databases. Order matches the
# CREATE TABLE block above so the column list stays consistent.
_V1_TO_V2_ALTERS = [
    "ALTER TABLE archives ADD COLUMN pub_title TEXT",
    "ALTER TABLE archives ADD COLUMN pub_doi TEXT",
    "ALTER TABLE archives ADD COLUMN pub_journal TEXT",
    "ALTER TABLE archives ADD COLUMN pub_year INTEGER",
    "ALTER TABLE archives ADD COLUMN oa_paper_required INTEGER",
    "ALTER TABLE archives ADD COLUMN oa_data_required INTEGER",
    "ALTER TABLE archives ADD COLUMN max_embargo_months INTEGER",
    "ALTER TABLE archives ADD COLUMN oa_mandate_source TEXT",
    "ALTER TABLE archives ADD COLUMN oa_mandate_missing INTEGER",
    "ALTER TABLE archives ADD COLUMN corresponding_author_name TEXT",
    "ALTER TABLE archives ADD COLUMN corresponding_author_email TEXT",
    "ALTER TABLE archives ADD COLUMN central_repository TEXT",
    "ALTER TABLE archives ADD COLUMN central_repository_code TEXT",
    "ALTER TABLE archives ADD COLUMN pub_db_last_refreshed_at TEXT",
    "ALTER TABLE archives ADD COLUMN data_contact_name TEXT",
    "ALTER TABLE archives ADD COLUMN data_contact_email TEXT",
    "ALTER TABLE archives ADD COLUMN data_contact_overridden INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE archives ADD COLUMN zenodo_code TEXT",
    "ALTER TABLE archives ADD COLUMN zenodo_code_overridden INTEGER NOT NULL DEFAULT 0",
]

# v2 → v3: SharePoint List parallel track. Adds sync bookkeeping columns
# and the corresponding-author override flag (mirrors data_contact_overridden).
_V2_TO_V3_ALTERS = [
    "ALTER TABLE archives ADD COLUMN sharepoint_item_id INTEGER",
    "ALTER TABLE archives ADD COLUMN sharepoint_synced_at TEXT",
    "ALTER TABLE archives ADD COLUMN corresponding_author_overridden INTEGER NOT NULL DEFAULT 0",
]

# v3 → v4: automation. Package detection is refreshed by the scanner;
# user_done_* is set by the SharePoint pull; zenodo_doi/zenodo_env are
# written when a draft is created via the API (reserved DOI + which
# Zenodo instance it lives on).
_V3_TO_V4_ALTERS = [
    "ALTER TABLE archives ADD COLUMN package_has_zip INTEGER",
    "ALTER TABLE archives ADD COLUMN package_has_readme INTEGER",
    "ALTER TABLE archives ADD COLUMN package_checked_at TEXT",
    "ALTER TABLE archives ADD COLUMN user_done_flag INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE archives ADD COLUMN user_done_at TEXT",
    "ALTER TABLE archives ADD COLUMN zenodo_doi TEXT",
    "ALTER TABLE archives ADD COLUMN zenodo_env TEXT",
]


def init_db(path: Path) -> None:
    """Create the database and tables; run any pending migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        conn.executescript(_SCHEMA_SQL)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] if row and row[0] is not None else 0
        if current == 0:
            # Fresh database — CREATE TABLE already produced v2 schema.
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
            return
        if current < _SCHEMA_VERSION:
            _migrate(conn, current)


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Apply migrations from ``from_version`` up to ``_SCHEMA_VERSION``."""
    if from_version < 2:
        for stmt in _V1_TO_V2_ALTERS:
            conn.execute(stmt)
    if from_version < 3:
        for stmt in _V2_TO_V3_ALTERS:
            conn.execute(stmt)
    if from_version < 4:
        for stmt in _V3_TO_V4_ALTERS:
            conn.execute(stmt)
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


def get_pending_handover(
    conn: sqlite3.Connection, publication_id: str
) -> dict[str, Any] | None:
    """The operative ``data_contact_handover`` event, if the handover notice
    has not been sent yet.

    A handover is pending when a ``data_contact_handover`` event exists and
    no ``handover_sent`` event was recorded after it. The event's ``note``
    carries the PREVIOUS contact's name (may be empty for a first
    assignment) — the handover email names who handed over.
    """
    handover = get_last_event(conn, publication_id, "data_contact_handover")
    if handover is None:
        return None
    sent = get_last_event(conn, publication_id, "handover_sent")
    if sent is not None and sent["event_id"] > handover["event_id"]:
        return None
    return handover


def get_last_event(
    conn: sqlite3.Connection, publication_id: str, action_code: str
) -> dict[str, Any] | None:
    """Most recent event of a given action for one archive, or None."""
    row = conn.execute(
        "SELECT * FROM events WHERE publication_id = ? AND action_code = ? "
        "ORDER BY event_id DESC LIMIT 1",
        (publication_id, action_code),
    ).fetchone()
    return dict(row) if row else None


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
