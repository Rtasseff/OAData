"""Tests for report generation module."""

from datetime import datetime, timedelta

from oa_tracker.db import get_connection, upsert_archive, insert_event
from oa_tracker.report import generate_report
from oa_tracker.status import OPEN_ACTIVE, OPEN_INACTIVE, CLOSED_DATA_ARCHIVED


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).isoformat(timespec="seconds")


def test_empty_report(test_config):
    path = generate_report(test_config)
    assert path.exists()
    content = path.read_text()
    assert "Weekly Report" in content
    assert "Total open: 0" in content


def test_report_new_this_week(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at=_now_iso(),
            last_seen_at=_now_iso(),
            status=OPEN_INACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB001" in content
    assert "New This Week" in content


def test_report_newly_active(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB002",
            folder_path="/tmp/pub002",
            first_seen_at=_days_ago(10),
            last_seen_at=_now_iso(),
            became_active_at=_now_iso(),
            status=OPEN_ACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB002" in content
    assert "Newly Active" in content


def test_report_stuck(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB003",
            folder_path="/tmp/pub003",
            first_seen_at=_days_ago(60),
            last_seen_at=_now_iso(),
            became_active_at=_days_ago(45),
            status=OPEN_ACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB003" in content
    assert "Stuck" in content


def test_report_missing_folder(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB004",
            folder_path="/tmp/pub004",
            first_seen_at=_days_ago(10),
            last_seen_at=_days_ago(2),
            status=OPEN_ACTIVE,
            unexpected_missing_folder=1,
            missing_folder_detected_at=_days_ago(2),
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB004" in content
    assert "Integrity Warnings" in content


def test_report_recently_closed(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB005",
            folder_path="/tmp/pub005",
            first_seen_at=_days_ago(30),
            last_seen_at=_days_ago(1),
            status=CLOSED_DATA_ARCHIVED,
            final_pid="10.5281/zenodo.999",
        )
        insert_event(
            conn,
            publication_id="PUB005",
            action_code="folder_removed",
            old_status="OPEN_DB_UPDATED",
            new_status=CLOSED_DATA_ARCHIVED,
            source="action_sheet",
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB005" in content
    assert "Recently Closed" in content


def test_report_pipeline_view(test_config):
    with get_connection(test_config.database) as conn:
        for i in range(3):
            upsert_archive(
                conn,
                publication_id=f"OPEN{i}",
                folder_path=f"/tmp/open{i}",
                first_seen_at=_days_ago(10),
                last_seen_at=_now_iso(),
                status=OPEN_ACTIVE,
            )
    path = generate_report(test_config)
    content = path.read_text()
    assert "OPEN_ACTIVE: 3" in content
