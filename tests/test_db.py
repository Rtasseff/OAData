"""Tests for db module."""

from oa_tracker.db import (
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
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == 1


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
