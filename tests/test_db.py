"""Tests for db module."""

import sqlite3

from oa_tracker.db import (
    _SCHEMA_VERSION,
    init_db,
    get_all_archives,
    get_archive,
    get_connection,
    get_open_archives,
    get_reminders_due,
    insert_event,
    upsert_archive,
    update_archive_status,
    get_recent_events,
)


def _columns(conn, table="archives"):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


_V3_COLUMNS = {"sharepoint_item_id", "sharepoint_synced_at", "corresponding_author_overridden"}


def test_init_creates_tables(tmp_db):
    with get_connection(tmp_db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "archives" in names
        assert "events" in names
        assert "schema_version" in names


def test_schema_version(tmp_db):
    with get_connection(tmp_db) as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == _SCHEMA_VERSION


def test_v3_columns_present_on_fresh_db(tmp_db):
    """A freshly-created DB carries the v3 SharePoint/CA columns, with the
    override flag defaulting to 0."""
    with get_connection(tmp_db) as conn:
        assert _V3_COLUMNS <= _columns(conn)
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_INACTIVE",
        )
        a = get_archive(conn, "PUB001")
        assert a["corresponding_author_overridden"] == 0
        assert a["sharepoint_item_id"] is None
        assert a["sharepoint_synced_at"] is None


def test_migrates_v2_to_v3(tmp_path):
    """A pre-existing v2 database (lacking the v3 columns) gains them when
    init_db runs the migration, and the recorded schema version advances."""
    db_path = tmp_path / "legacy.sqlite"
    # Stand up a minimal v2-era database: an archives table without the
    # v3 columns, and schema_version pinned at 2.
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE archives (
            publication_id TEXT PRIMARY KEY,
            status TEXT NOT NULL
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (2);
        """
    )
    conn.commit()
    conn.close()

    assert _V3_COLUMNS.isdisjoint(
        {r[1] for r in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(archives)")}
    )

    init_db(db_path)

    with get_connection(db_path) as conn:
        assert _V3_COLUMNS <= _columns(conn)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == _SCHEMA_VERSION


def test_upsert_insert_and_get(tmp_db):
    with get_connection(tmp_db) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_INACTIVE",
        )
        archive = get_archive(conn, "PUB001")
        assert archive is not None
        assert archive["status"] == "OPEN_INACTIVE"
        assert archive["folder_path"] == "/tmp/pub001"


def test_upsert_update(tmp_db):
    with get_connection(tmp_db) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_INACTIVE",
        )
        upsert_archive(
            conn,
            publication_id="PUB001",
            status="OPEN_ACTIVE",
            became_active_at="2026-01-05T00:00:00",
        )
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == "OPEN_ACTIVE"
        assert archive["became_active_at"] == "2026-01-05T00:00:00"


def test_update_archive_status(tmp_db):
    with get_connection(tmp_db) as conn:
        upsert_archive(
            conn,
            publication_id="PUB002",
            folder_path="/tmp/pub002",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_ACTIVE",
        )
        update_archive_status(
            conn, "PUB002", "OPEN_READY_FOR_ZENODO_DRAFT",
            final_pid="10.5281/zenodo.123",
        )
        archive = get_archive(conn, "PUB002")
        assert archive["status"] == "OPEN_READY_FOR_ZENODO_DRAFT"
        assert archive["final_pid"] == "10.5281/zenodo.123"


def test_get_all_archives(tmp_db):
    with get_connection(tmp_db) as conn:
        for i in range(3):
            upsert_archive(
                conn,
                publication_id=f"PUB{i:03d}",
                folder_path=f"/tmp/pub{i:03d}",
                first_seen_at="2026-01-01T00:00:00",
                last_seen_at="2026-01-01T00:00:00",
                status="OPEN_ACTIVE" if i % 2 == 0 else "OPEN_INACTIVE",
            )
        all_a = get_all_archives(conn)
        assert len(all_a) == 3

        active = get_all_archives(conn, status_filter="OPEN_ACTIVE")
        assert len(active) == 2


def test_get_open_archives(tmp_db):
    with get_connection(tmp_db) as conn:
        upsert_archive(
            conn,
            publication_id="OPEN1",
            folder_path="/tmp/open1",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_ACTIVE",
        )
        upsert_archive(
            conn,
            publication_id="CLOSED1",
            folder_path="/tmp/closed1",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="CLOSED_DATA_ARCHIVED",
        )
        opens = get_open_archives(conn)
        assert len(opens) == 1
        assert opens[0]["publication_id"] == "OPEN1"


def test_insert_and_get_events(tmp_db):
    with get_connection(tmp_db) as conn:
        insert_event(
            conn,
            publication_id="PUB001",
            action_code="qa_pass",
            old_status="OPEN_ACTIVE",
            new_status="OPEN_READY_FOR_ZENODO_DRAFT",
            source="action_sheet",
            note="Looks good",
        )
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert len(events) == 1
        assert events[0]["action_code"] == "qa_pass"
        assert events[0]["note"] == "Looks good"


def test_get_reminders_due(tmp_db):
    with get_connection(tmp_db) as conn:
        upsert_archive(
            conn,
            publication_id="REM1",
            folder_path="/tmp/rem1",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_ACTIVE",
            next_reminder_at="2025-01-01T00:00:00",  # in the past
        )
        upsert_archive(
            conn,
            publication_id="REM2",
            folder_path="/tmp/rem2",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-01T00:00:00",
            status="OPEN_ACTIVE",
            next_reminder_at="2099-01-01T00:00:00",  # in the future
        )
        due = get_reminders_due(conn, "2026-02-17T00:00:00")
        assert len(due) == 1
        assert due[0]["publication_id"] == "REM1"


_V4_COLUMNS = {
    "package_has_zip", "package_has_readme", "package_checked_at",
    "user_done_flag", "user_done_at", "zenodo_doi", "zenodo_env",
}


def test_fresh_db_has_v4_columns(tmp_db):
    with get_connection(tmp_db) as conn:
        assert _V4_COLUMNS <= _columns(conn)


def test_migrates_v3_to_v4(tmp_path):
    """A v3-era database gains the v4 automation columns on init_db."""
    db_path = tmp_path / "legacy_v3.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE archives (
            publication_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            sharepoint_item_id INTEGER,
            sharepoint_synced_at TEXT,
            corresponding_author_overridden INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (3);
        """
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    with get_connection(db_path) as conn:
        assert _V4_COLUMNS <= _columns(conn)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == _SCHEMA_VERSION


_V5_COLUMNS = {"package_has_manuscript"}


def test_fresh_db_has_v5_columns(tmp_db):
    with get_connection(tmp_db) as conn:
        assert _V5_COLUMNS <= _columns(conn)


def test_migrates_v4_to_v5(tmp_path):
    """A v4-era database gains the manuscript column on init_db."""
    db_path = tmp_path / "legacy_v4.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE archives (
            publication_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            package_has_zip INTEGER,
            package_has_readme INTEGER
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version (version) VALUES (4);
        """
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    with get_connection(db_path) as conn:
        assert _V5_COLUMNS <= _columns(conn)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        assert row["v"] == _SCHEMA_VERSION
