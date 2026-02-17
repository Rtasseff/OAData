"""Tests for sheet generation module."""

import csv

from oa_tracker.db import get_connection, upsert_archive
from oa_tracker.sheet import generate_sheet
from oa_tracker.status import (
    OPEN_ACTIVE,
    OPEN_INACTIVE,
    OPEN_READY_FOR_ZENODO_DRAFT,
    OPEN_ZENODO_DRAFT_CREATED,
    OPEN_ZENODO_DRAFT_VALIDATED,
    OPEN_ZENODO_PUBLISHED,
    OPEN_DB_UPDATED,
)


def _insert(db_path, pub_id, status, **kwargs):
    with get_connection(db_path) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=f"/tmp/{pub_id}",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            status=status,
            **kwargs,
        )


def _read_sheet(path):
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_empty_db_generates_empty_sheet(test_config):
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    assert len(rows) == 0


def test_generates_qa_task_for_active(test_config):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    assert len(rows) == 1
    assert rows[0]["task_code"] == "qa_pass"
    assert rows[0]["done"] == "0"


def test_no_task_for_inactive(test_config):
    _insert(test_config.database, "PUB001", OPEN_INACTIVE)
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    # OPEN_INACTIVE has no next_task (no pipeline task)
    assert len(rows) == 0


def test_generates_correct_tasks_per_status(test_config):
    statuses_and_expected = [
        (OPEN_ACTIVE, "qa_pass"),
        (OPEN_READY_FOR_ZENODO_DRAFT, "zenodo_draft_created"),
        (OPEN_ZENODO_DRAFT_CREATED, "zenodo_validated"),
        (OPEN_ZENODO_DRAFT_VALIDATED, "zenodo_published"),
        (OPEN_ZENODO_PUBLISHED, "db_updated"),
        (OPEN_DB_UPDATED, "folder_removed"),
    ]
    for i, (status, expected_task) in enumerate(statuses_and_expected):
        _insert(test_config.database, f"PUB{i:03d}", status)

    path = generate_sheet(test_config)
    rows = _read_sheet(path)

    task_codes = {r["publication_id"]: r["task_code"] for r in rows}
    for i, (status, expected_task) in enumerate(statuses_and_expected):
        pub_id = f"PUB{i:03d}"
        assert task_codes[pub_id] == expected_task, f"{pub_id} expected {expected_task}, got {task_codes.get(pub_id)}"


def test_generates_reminder_task_when_due(test_config):
    _insert(
        test_config.database, "PUB001", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",  # in the past
        reminder_count=2,
    )
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    task_codes = [r["task_code"] for r in rows if r["publication_id"] == "PUB001"]
    assert "remind_sent" in task_codes
    assert "qa_pass" in task_codes  # also gets the pipeline task
    # Verify info columns are populated
    remind_row = [r for r in rows if r["task_code"] == "remind_sent"][0]
    assert remind_row["first_seen_at"] == "2026-01-01T00:00:00"
    assert remind_row["next_reminder_at"] == "2020-01-01T00:00:00"
    assert remind_row["reminder_count"] == "2"
