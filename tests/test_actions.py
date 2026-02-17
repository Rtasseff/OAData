"""Tests for actions module."""

import csv
from pathlib import Path

from oa_tracker.actions import apply_actions
from oa_tracker.db import get_archive, get_connection, upsert_archive, get_recent_events
from oa_tracker.status import OPEN_ACTIVE, OPEN_READY_FOR_ZENODO_DRAFT, CLOSED_EXCEPTION


def _write_sheet(path: Path, rows: list[dict]) -> Path:
    sheet = path / "action_sheet.tsv"
    cols = ["publication_id", "current_status", "task_code", "task_text", "due_date", "done", "pid", "url", "note"]
    with open(sheet, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return sheet


def _insert_active_archive(db_path: Path, pub_id: str, status: str = OPEN_ACTIVE):
    with get_connection(db_path) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=f"/tmp/{pub_id}",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=status,
            next_reminder_at="2026-01-19T00:00:00",
        )


def test_apply_qa_pass(test_config):
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "QA complete",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "Looks good",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_READY_FOR_ZENODO_DRAFT
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "qa_pass" for e in events)


def test_apply_skips_undone(test_config):
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "QA complete",
        "due_date": "2026-02-21",
        "done": "0",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert result.skipped == 1


def test_apply_invalid_transition(test_config):
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "zenodo_published",
        "task_text": "Publish Zenodo record",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "10.5281/zenodo.123",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert len(result.errors) == 1


def test_apply_remind_sent(test_config):
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "remind_sent",
        "task_text": "Send reminder",
        "due_date": "2026-02-17",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["reminder_count"] == 1
        assert archive["last_notified_at"] is not None
        # Status should not change
        assert archive["status"] == OPEN_ACTIVE


def test_apply_zenodo_published_warns_paper_doi(test_config):
    _insert_active_archive(test_config.database, "PUB001", "OPEN_ZENODO_DRAFT_VALIDATED")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_ZENODO_DRAFT_VALIDATED",
        "task_code": "zenodo_published",
        "task_text": "Publish",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "10.1234/journal.abc.999",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert len(result.warnings) == 1
    assert "paper DOI" in result.warnings[0]


def test_apply_folder_removed_no_pid_closes_exception(test_config):
    _insert_active_archive(test_config.database, "PUB001", "OPEN_DB_UPDATED")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_DB_UPDATED",
        "task_code": "folder_removed",
        "task_text": "Remove folder",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert len(result.warnings) == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_EXCEPTION


def test_apply_moves_to_history(test_config):
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "QA complete",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    apply_actions(sheet, test_config)

    history = test_config.output_dir / "action_history.tsv"
    assert history.exists()
    with open(history) as f:
        reader = csv.DictReader(f, delimiter="\t")
        hist_rows = list(reader)
    assert len(hist_rows) == 1
    assert hist_rows[0]["applied_at"] != ""

    # Sheet should be empty (only header)
    with open(sheet) as f:
        reader = csv.DictReader(f, delimiter="\t")
        remaining = list(reader)
    assert len(remaining) == 0


def test_apply_unknown_publication(test_config):
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "MISSING",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "QA",
        "due_date": "2026-02-21",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert len(result.errors) == 1
