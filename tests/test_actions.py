"""Tests for actions module."""

import csv
from pathlib import Path

from oa_tracker.actions import (
    apply_actions, reset_data_contact, reset_zenodo_code,
    set_data_contact, set_zenodo_code,
    set_corresponding_author, reset_corresponding_author,
)
from oa_tracker.db import get_archive, get_connection, upsert_archive, get_recent_events
from oa_tracker.status import (
    OPEN_ACTIVE, OPEN_READY_FOR_ZENODO_DRAFT, OPEN_ZENODO_PUBLISHED,
    CLOSED_DATA_ARCHIVED, CLOSED_EXCEPTION,
    validate_transition,
)


def _write_sheet(path: Path, rows: list[dict]) -> Path:
    sheet = path / "action_sheet.tsv"
    cols = ["publication_id", "current_status", "task_code", "task_text", "first_seen_at", "next_reminder_at", "reminder_count", "done", "pid", "url", "note"]
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "0",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert result.skipped == 1


def test_apply_invalid_transition(test_config):
    """Invalid transition with no PID should still error."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "zenodo_published",
        "task_text": "Publish Zenodo record",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "0",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
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
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert len(result.errors) == 1


# ── Fast-track shortcuts ─────────────────────────────────────────────

def test_fast_track_pid_jumps_to_zenodo_published(test_config):
    """done=1 with PID should skip straight to OPEN_ZENODO_PUBLISHED."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "Review uploaded data and approve QA",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "10.5281/zenodo.123456",
        "url": "https://zenodo.org/record/123456",
        "note": "Already published externally",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ZENODO_PUBLISHED
        assert archive["final_pid"] == "10.5281/zenodo.123456"
        assert archive["final_url"] == "https://zenodo.org/record/123456"
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "fast_track_published" for e in events)


def test_fast_track_url_only_jumps_to_zenodo_published(test_config):
    """done=1 with URL (no PID) should also fast-track."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "Review",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "https://zenodo.org/record/999",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ZENODO_PUBLISHED


def test_fast_track_does_not_apply_to_remind_sent(test_config):
    """PID on a remind_sent row should NOT fast-track."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "remind_sent",
        "task_text": "Send reminder",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "0",
        "done": "1",
        "pid": "10.5281/zenodo.123456",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ACTIVE
        assert archive["reminder_count"] == 1


# ── done=2 full closure shortcuts ────────────────────────────────────

def test_done2_with_pid_closes_data_archived(test_config):
    """done=2 with PID should close as CLOSED_DATA_ARCHIVED."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "Review",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "2",
        "pid": "10.5281/zenodo.789",
        "url": "https://zenodo.org/record/789",
        "note": "All done",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_DATA_ARCHIVED
        assert archive["final_pid"] == "10.5281/zenodo.789"
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "full_closure" for e in events)


def test_done2_without_pid_closes_exception(test_config):
    """done=2 with no PID should close as CLOSED_EXCEPTION."""
    _insert_active_archive(test_config.database, "PUB001")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "qa_pass",
        "task_text": "Review",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "2",
        "pid": "",
        "url": "",
        "note": "No data to archive",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert len(result.warnings) == 1
    assert "CLOSED_EXCEPTION" in result.warnings[0]

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_EXCEPTION


def test_contact_pi_manual_no_pid_requeues_reminder(test_config):
    """contact_pi_manual + done=1 with no PID logs the contact and re-queues:
    status unchanged, reminder count +1, next reminder scheduled — the row
    keeps coming back until data arrives or the operator explicitly closes."""
    _insert_active_archive(test_config.database, "PUB001", "OPEN_INACTIVE")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB001", reminder_count=3)
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_INACTIVE",
        "task_code": "contact_pi_manual",
        "task_text": "MAX reminder reached; manually contact PI",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "3",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == "OPEN_INACTIVE"
        assert archive["reminder_count"] == 4
        assert archive["next_reminder_at"] is not None
        assert archive["next_reminder_at"] > archive["last_notified_at"]
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        evt = next(e for e in events if e["action_code"] == "contact_pi_manual")
        assert evt["new_status"] == "OPEN_INACTIVE"
        assert "re-queued" in (evt["note"] or "")


def test_contact_pi_manual_operator_note_recorded_still_open(test_config):
    """An operator note on the manual-contact row lands in the archive notes
    (a durable record of what the PI said) without closing anything."""
    _insert_active_archive(test_config.database, "PUB001", "OPEN_INACTIVE")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_INACTIVE",
        "task_code": "contact_pi_manual",
        "task_text": "MAX reminder reached; manually contact PI",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "3",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "Spoke to PI in person; promised upload this month.",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == "OPEN_INACTIVE"
        assert "promised upload" in (archive["notes"] or "")
        assert archive["next_reminder_at"] is not None
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        evt = next(e for e in events if e["action_code"] == "contact_pi_manual")
        assert "promised upload" in (evt["note"] or "")


def test_contact_pi_manual_skipped_when_status_no_longer_waiting(test_config):
    """Once the archive advanced past data collection there is no one to
    chase — contact_pi_manual is skipped with a warning, like remind_sent."""
    _insert_active_archive(
        test_config.database, "PUB001", "OPEN_READY_FOR_ZENODO_DRAFT")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_READY_FOR_ZENODO_DRAFT",
        "task_code": "contact_pi_manual",
        "task_text": "MAX reminder reached; manually contact PI",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "3",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert result.skipped == 1
    assert any("no longer waiting" in w for w in result.warnings)

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == "OPEN_READY_FOR_ZENODO_DRAFT"
        assert archive["reminder_count"] == 0


def test_contact_pi_manual_with_pid_fast_tracks(test_config):
    """contact_pi_manual + done=1 with PID should hit the fast-track path (OPEN_ZENODO_PUBLISHED)."""
    _insert_active_archive(test_config.database, "PUB001", "OPEN_INACTIVE")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_INACTIVE",
        "task_code": "contact_pi_manual",
        "task_text": "MAX reminder reached; manually contact PI",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "3",
        "done": "1",
        "pid": "10.5281/zenodo.42",
        "url": "",
        "note": "Data finally arrived.",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ZENODO_PUBLISHED
        assert archive["final_pid"] == "10.5281/zenodo.42"


def test_contact_pi_manual_done2_full_closure(test_config):
    """contact_pi_manual + done=2 with PID → CLOSED_DATA_ARCHIVED via full-closure shortcut."""
    _insert_active_archive(test_config.database, "PUB001", "OPEN_INACTIVE")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_INACTIVE",
        "task_code": "contact_pi_manual",
        "task_text": "MAX reminder reached; manually contact PI",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "2026-01-19T00:00:00",
        "reminder_count": "3",
        "done": "2",
        "pid": "10.5281/zenodo.77",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_DATA_ARCHIVED


def test_done2_uses_existing_pid_from_db(test_config):
    """done=2 with no PID in the row but a PID already in the DB should close normally."""
    _insert_active_archive(test_config.database, "PUB001", "OPEN_ZENODO_PUBLISHED")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB001", final_pid="10.5281/zenodo.555")

    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": "OPEN_ZENODO_PUBLISHED",
        "task_code": "db_updated",
        "task_text": "Update DB",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "2",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_DATA_ARCHIVED



# ── Stage 2: mandate_missing acknowledgment ──────────────────────────


def test_apply_mandate_missing_is_event_only_no_status_change(test_config):
    _insert_active_archive(test_config.database, "PUB300", OPEN_ACTIVE)
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB300",
        "current_status": OPEN_ACTIVE,
        "task_code": "mandate_missing",
        "task_text": "Confirm with PO/IT",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "asked Nerea",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB300")
        assert archive["status"] == OPEN_ACTIVE
        assert "asked Nerea" in (archive["notes"] or "")
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "mandate_missing" for e in events)


# ── Stage 2: data-contact / zenodo-code overrides ───────────────────


def test_set_data_contact_marks_overridden(test_config):
    _insert_active_archive(test_config.database, "PUB400")
    result = set_data_contact(
        test_config, "PUB400", email="ops@example.org", name="Ops Name",
    )
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB400")
    assert a["data_contact_email"] == "ops@example.org"
    assert a["data_contact_name"] == "Ops Name"
    assert a["data_contact_overridden"] == 1


def test_set_data_contact_requires_email(test_config):
    _insert_active_archive(test_config.database, "PUB401")
    result = set_data_contact(test_config, "PUB401", email="")
    assert result.applied == 0
    assert any("email" in e for e in result.errors)


def test_reset_data_contact_clears_override(test_config):
    _insert_active_archive(test_config.database, "PUB402")
    set_data_contact(test_config, "PUB402", email="ops@example.org")
    result = reset_data_contact(test_config, "PUB402")
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB402")
    assert a["data_contact_overridden"] == 0
    # Email value remains until next scan re-seeds; only the flag changes.
    assert a["data_contact_email"] == "ops@example.org"


def test_set_zenodo_code_marks_overridden(test_config):
    _insert_active_archive(test_config.database, "PUB403")
    result = set_zenodo_code(test_config, "PUB403", code="12345")
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB403")
    assert a["zenodo_code"] == "12345"
    assert a["zenodo_code_overridden"] == 1


def test_set_zenodo_code_requires_code(test_config):
    _insert_active_archive(test_config.database, "PUB404")
    result = set_zenodo_code(test_config, "PUB404", code="")
    assert result.applied == 0
    assert any("code" in e for e in result.errors)


def test_reset_zenodo_code_clears_override(test_config):
    _insert_active_archive(test_config.database, "PUB405")
    set_zenodo_code(test_config, "PUB405", code="abc")
    result = reset_zenodo_code(test_config, "PUB405")
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB405")
    assert a["zenodo_code_overridden"] == 0
    assert a["zenodo_code"] == "abc"


def test_overrides_unknown_publication_errors_cleanly(test_config):
    """No archive in DB → error, applied stays 0."""
    result = set_data_contact(test_config, "GHOST", email="x@y")
    assert result.applied == 0
    assert any("not in database" in e for e in result.errors)

    result = set_zenodo_code(test_config, "GHOST", code="9")
    assert result.applied == 0
    assert any("not in database" in e for e in result.errors)


def test_set_data_contact_logs_audit_event(test_config):
    _insert_active_archive(test_config.database, "PUB406")
    set_data_contact(test_config, "PUB406", email="x@y", name="N")

    with get_connection(test_config.database) as conn:
        events = get_recent_events(conn, "2000-01-01T00:00:00")
    relevant = [e for e in events if e["action_code"] == "set_data_contact"]
    assert len(relevant) == 1
    assert relevant[0]["source"] == "cli"
    assert "x@y" in relevant[0]["note"]


# ── Parallel track: close_archived_external ──────────────────────────


def test_close_archived_external_closes_data_archived(test_config):
    """The 'archived elsewhere' exemption closes as CLOSED_DATA_ARCHIVED
    with the external PID/URL recorded — not as an exception."""
    _insert_active_archive(test_config.database, "PUB500")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB500",
        "current_status": OPEN_ACTIVE,
        "task_code": "close_archived_external",
        "task_text": "Archived elsewhere",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "10.5061/dryad.abc123",
        "url": "https://datadryad.org/stash/dataset/doi:10.5061/dryad.abc123",
        "note": "Archived by external collaborators",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB500")
        assert a["status"] == CLOSED_DATA_ARCHIVED
        assert a["final_pid"] == "10.5061/dryad.abc123"
        assert a["final_url"].startswith("https://datadryad.org/")
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "close_archived_external" for e in events)


def test_close_archived_external_requires_pid_and_url(test_config):
    """Missing PID or URL is an error — we don't close 'archived' without evidence."""
    _insert_active_archive(test_config.database, "PUB501")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB501",
        "current_status": OPEN_ACTIVE,
        "task_code": "close_archived_external",
        "task_text": "Archived elsewhere",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "10.5061/dryad.abc123",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert any("requires both a PID and a URL" in e for e in result.errors)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB501")
        assert a["status"] == OPEN_ACTIVE  # unchanged


def test_close_archived_external_rejects_closed_status(test_config):
    """close_archived_external only applies to an OPEN archive."""
    _insert_active_archive(test_config.database, "PUB502", CLOSED_EXCEPTION)
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB502",
        "current_status": CLOSED_EXCEPTION,
        "task_code": "close_archived_external",
        "task_text": "Archived elsewhere",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "10.5061/dryad.x",
        "url": "https://datadryad.org/x",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert any("OPEN" in e for e in result.errors)


def test_close_archived_external_is_wildcard_transition():
    """Any OPEN status maps to CLOSED_DATA_ARCHIVED via validate_transition."""
    assert validate_transition(OPEN_ACTIVE, "close_archived_external") == CLOSED_DATA_ARCHIVED
    assert validate_transition("OPEN_INACTIVE", "close_archived_external") == CLOSED_DATA_ARCHIVED


# ── Parallel track: propose_* are acknowledgment-only for now ────────


def test_propose_done_is_ack_only(test_config):
    _insert_active_archive(test_config.database, "PUB510")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB510",
        "current_status": OPEN_ACTIVE,
        "task_code": "propose_done",
        "task_text": "User thinks it's done",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "data contact says complete",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB510")
        assert a["status"] == OPEN_ACTIVE  # no status change
        assert "complete" in (a["notes"] or "")
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "propose_done" for e in events)


def test_propose_exemption_and_data_contact_are_ack_only(test_config):
    _insert_active_archive(test_config.database, "PUB511")
    for code in ("propose_exemption", "propose_data_contact"):
        sheet = _write_sheet(test_config.output_dir, [{
            "publication_id": "PUB511",
            "current_status": OPEN_ACTIVE,
            "task_code": code,
            "task_text": code,
            "first_seen_at": "2026-01-01T00:00:00",
            "next_reminder_at": "",
            "reminder_count": "0",
            "done": "1",
            "pid": "",
            "url": "",
            "note": f"signal: {code}",
        }])
        result = apply_actions(sheet, test_config)
        assert result.applied == 1, code
        assert result.errors == [], code

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB511")
        assert a["status"] == OPEN_ACTIVE


def test_user_note_is_ack_only_and_records_note(test_config):
    _insert_active_archive(test_config.database, "PUB512")
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB512",
        "current_status": OPEN_ACTIVE,
        "task_code": "user_note",
        "task_text": "User note (awareness only — no action needed)",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "out next week if you need me",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB512")
        assert a["status"] == OPEN_ACTIVE                 # no status change
        assert "out next week" in (a["notes"] or "")      # durably recorded
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "user_note" for e in events)


# ── Parallel track: corresponding-author override ────────────────────


def test_set_corresponding_author_marks_overridden(test_config):
    _insert_active_archive(test_config.database, "PUB520")
    result = set_corresponding_author(
        test_config, "PUB520", email="pi@cicbiomagune.es", name="Effective PI",
    )
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB520")
    assert a["corresponding_author_email"] == "pi@cicbiomagune.es"
    assert a["corresponding_author_name"] == "Effective PI"
    assert a["corresponding_author_overridden"] == 1


def test_set_corresponding_author_requires_email(test_config):
    _insert_active_archive(test_config.database, "PUB521")
    result = set_corresponding_author(test_config, "PUB521", email="")
    assert result.applied == 0
    assert any("email" in e for e in result.errors)


def test_reset_corresponding_author_clears_override(test_config):
    _insert_active_archive(test_config.database, "PUB522")
    set_corresponding_author(test_config, "PUB522", email="pi@cicbiomagune.es")
    result = reset_corresponding_author(test_config, "PUB522")
    assert result.applied == 1

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB522")
    assert a["corresponding_author_overridden"] == 0
    # Value remains until the next scan re-seeds; only the flag changes.
    assert a["corresponding_author_email"] == "pi@cicbiomagune.es"


def test_set_corresponding_author_unknown_pub_errors(test_config):
    result = set_corresponding_author(test_config, "GHOST", email="x@y")
    assert result.applied == 0
    assert any("not in database" in e for e in result.errors)


def test_remind_sent_skipped_when_status_no_longer_waiting_for_data(test_config):
    """If qa_pass moved an archive past OPEN_ACTIVE earlier in the
    batch, a subsequent remind_sent on the same archive should be
    skipped with a warning — we don't tick the reminder counter on an
    archive that's no longer waiting for data."""
    from oa_tracker.db import update_archive_status
    _insert_active_archive(test_config.database, "PUB900")

    # Simulate qa_pass having moved it to OPEN_READY_FOR_ZENODO_DRAFT.
    with get_connection(test_config.database) as conn:
        update_archive_status(conn, "PUB900", OPEN_READY_FOR_ZENODO_DRAFT)

    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB900",
        "current_status": OPEN_READY_FOR_ZENODO_DRAFT,
        "task_code": "remind_sent",
        "task_text": "Send reminder",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "1",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 0
    assert result.skipped == 1
    assert any("no longer waiting for data" in w for w in result.warnings)

    # Reminder counter unchanged.
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB900")
    assert a["reminder_count"] == 0


# ── Data-contact handover (auto reassignment → operator-sent notice) ──

def test_set_data_contact_queue_handover_records_previous_name(test_config):
    """The auto-apply path records a data_contact_handover event whose note
    is the PREVIOUS contact's name — the handover email names who handed
    over, and get_pending_handover reports it until handover_sent."""
    from oa_tracker.db import get_pending_handover
    _insert_active_archive(test_config.database, "PUB001")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB001",
                       data_contact_name="Old Contact",
                       data_contact_email="old@biomagune.es")
    r = set_data_contact(test_config, "PUB001", email="new@biomagune.es",
                         name="New Contact", source="auto", queue_handover=True)
    assert r.applied == 1 and not r.errors

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["data_contact_name"] == "New Contact"
        assert archive["data_contact_email"] == "new@biomagune.es"
        pending = get_pending_handover(conn, "PUB001")
        assert pending is not None
        assert pending["note"] == "Old Contact"
        assert pending["source"] == "auto"


def test_set_data_contact_queues_handover_by_default(test_config):
    """Assigning a contact notifies them by default (operator decision
    2026-09-17); queue_handover=False (CLI --no-notify) opts out."""
    from oa_tracker.db import get_pending_handover
    _insert_active_archive(test_config.database, "PUB001")
    _insert_active_archive(test_config.database, "PUB002")
    set_data_contact(test_config, "PUB001", email="new@biomagune.es", name="New Contact")
    r = set_data_contact(test_config, "PUB002", email="new@biomagune.es",
                         name="New Contact", queue_handover=False)
    assert r.applied == 1

    with get_connection(test_config.database) as conn:
        assert get_pending_handover(conn, "PUB001") is not None
        assert get_pending_handover(conn, "PUB002") is None


def test_handover_sent_row_clears_pending(test_config):
    """done=1 on the handover_sent sheet row records the send and stops the
    row/draft from regenerating; status is untouched."""
    from oa_tracker.db import get_pending_handover
    _insert_active_archive(test_config.database, "PUB001")
    set_data_contact(test_config, "PUB001", email="new@biomagune.es",
                     name="New Contact", source="auto", queue_handover=True)
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ACTIVE,
        "task_code": "handover_sent",
        "task_text": "Send handover notice to the new data contact",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ACTIVE
        assert get_pending_handover(conn, "PUB001") is None
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "handover_sent" for e in events)


def test_completion_sent_row_records_send(test_config):
    """done=1 on the completion_sent row logs a completion_sent event and
    leaves status untouched (mirrors handover_sent)."""
    _insert_active_archive(
        test_config.database, "PUB001", status=OPEN_ZENODO_PUBLISHED
    )
    sheet = _write_sheet(test_config.output_dir, [{
        "publication_id": "PUB001",
        "current_status": OPEN_ZENODO_PUBLISHED,
        "task_code": "completion_sent",
        "task_text": "Send completion email to the data contact (data archived)",
        "first_seen_at": "2026-01-01T00:00:00",
        "next_reminder_at": "",
        "reminder_count": "0",
        "done": "1",
        "pid": "",
        "url": "",
        "note": "",
    }])
    result = apply_actions(sheet, test_config)
    assert result.applied == 1
    assert result.errors == []

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ZENODO_PUBLISHED  # untouched
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(e["action_code"] == "completion_sent" for e in events)


# ── Rejected Tracker 'done' ticks (reject_done / reject_done_sent) ────

def _insert_done_ticked_empty(db_path, pub_id="PUB300", **over):
    row = dict(
        publication_id=pub_id, folder_path=f"/tmp/{pub_id}",
        first_seen_at="2026-01-01T00:00:00", last_seen_at="2026-01-15T00:00:00",
        status="OPEN_INACTIVE", user_done_flag=1, user_done_at="2026-07-13T17:41:37",
        oa_data_required=1, oa_mandate_missing=0,
        pub_db_last_refreshed_at="2026-01-15T00:00:00",
        data_contact_name="Lucía Cardo", data_contact_email="lcardo@cicbiomagune.es",
    )
    row.update(over)
    with get_connection(db_path) as conn:
        upsert_archive(conn, **row)


def test_reject_done_clears_tick_and_queues_notice(test_config):
    from oa_tracker.actions import apply_single
    from oa_tracker.db import get_pending_reject_notice, get_pending_untick
    _insert_done_ticked_empty(test_config.database)
    r, old_s, new_s = apply_single(test_config, "PUB300", "reject_done")
    assert r.applied == 1 and not r.errors
    assert old_s == new_s == "OPEN_INACTIVE"
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB300")
        assert a["user_done_flag"] == 0 and a["user_done_at"] is None
        assert "folder is still empty" in a["notes"]
        notice = get_pending_reject_notice(conn, "PUB300")
        assert notice is not None and "folder is still empty" in notice["note"]
        assert get_pending_untick(conn, "PUB300") is not None


def test_reject_done_skipped_when_no_rule_applies(test_config):
    """Stale row: files arrived since the sheet was made — no rejection."""
    from oa_tracker.actions import apply_single
    _insert_done_ticked_empty(test_config.database, status=OPEN_ACTIVE)
    r, _, _ = apply_single(test_config, "PUB300", "reject_done")
    assert r.applied == 0 and r.skipped == 1 and not r.errors
    assert any("no rejection rule applies" in w for w in r.warnings)
    with get_connection(test_config.database) as conn:
        assert get_archive(conn, "PUB300")["user_done_flag"] == 1


def test_reject_done_refused_past_author_owned_status(test_config):
    from oa_tracker.actions import apply_single
    _insert_done_ticked_empty(test_config.database, status=OPEN_READY_FOR_ZENODO_DRAFT)
    r, _, _ = apply_single(test_config, "PUB300", "reject_done")
    assert r.applied == 0
    assert any("reject_done needs" in e for e in r.errors)


def test_reject_done_ignores_stray_pid(test_config):
    """A PID on a reject_done row must not fast-track the archive."""
    from oa_tracker.actions import apply_single
    _insert_done_ticked_empty(test_config.database)
    r, _, new_s = apply_single(test_config, "PUB300", "reject_done", pid="10.5281/zenodo.1")
    assert r.applied == 1 and new_s == "OPEN_INACTIVE"


def test_reject_done_sent_clears_pending_notice(test_config):
    from oa_tracker.actions import apply_single
    from oa_tracker.db import get_pending_reject_notice
    _insert_done_ticked_empty(test_config.database)
    apply_single(test_config, "PUB300", "reject_done")
    r, _, new_s = apply_single(test_config, "PUB300", "reject_done_sent")
    assert r.applied == 1 and new_s == "OPEN_INACTIVE"
    with get_connection(test_config.database) as conn:
        assert get_pending_reject_notice(conn, "PUB300") is None


# ── Close-out for closed archives whose folder remained ───────────────

def test_closed_folder_removed_skipped_while_folder_exists(test_config, tmp_path):
    from oa_tracker.actions import apply_single
    folder = tmp_path / "PUB400"
    folder.mkdir()
    _insert_active_archive(test_config.database, "PUB400", status="CLOSED_PUBLICATION_ONLY")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB400", folder_path=str(folder))
    r, _, _ = apply_single(test_config, "PUB400", "closed_folder_removed")
    assert r.applied == 0 and r.skipped == 1
    assert any("still exists" in w for w in r.warnings)


def test_closed_folder_removed_records_close_out(test_config, tmp_path):
    from oa_tracker.actions import apply_single
    from oa_tracker.db import get_pending_folder_cleanup, insert_event
    _insert_active_archive(test_config.database, "PUB400", status="CLOSED_PUBLICATION_ONLY")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB400", folder_path=str(tmp_path / "gone"))
        insert_event(conn, "PUB400", "closed_folder_present",
                     "CLOSED_PUBLICATION_ONLY", "CLOSED_PUBLICATION_ONLY", "scanner")
    r, old_s, new_s = apply_single(test_config, "PUB400", "closed_folder_removed")
    assert r.applied == 1 and old_s == new_s == "CLOSED_PUBLICATION_ONLY"
    with get_connection(test_config.database) as conn:
        assert get_pending_folder_cleanup(conn, "PUB400") is None


def test_closed_folder_removed_refused_on_open_archive(test_config):
    from oa_tracker.actions import apply_single
    _insert_active_archive(test_config.database, "PUB400")
    r, _, _ = apply_single(test_config, "PUB400", "closed_folder_removed")
    assert any("needs a CLOSED status" in e for e in r.errors)


# ── First data-contact assignment (replacing the 'TBD' placeholder) ───

def test_first_assignment_restarts_reminder_clock_and_blanks_previous(test_config):
    from datetime import datetime
    from oa_tracker.db import get_pending_handover
    _insert_active_archive(test_config.database, "PUB500", status="OPEN_INACTIVE")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB500", data_contact_email="TBD",
                       data_contact_name="Placeholder Name", reminder_count=1,
                       next_reminder_at="2026-08-19T07:00:01")
    r = set_data_contact(test_config, "PUB500", email="flopez@cicbiomagune.es",
                         name="Fernando López Gallego", queue_handover=True)
    assert r.applied == 1
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB500")
        assert a["reminder_count"] == 0
        due = datetime.fromisoformat(a["next_reminder_at"])
        days = (due - datetime.now()).days
        assert test_config.reminders.first_reminder_days - 1 <= days <= test_config.reminders.first_reminder_days
        assert get_pending_handover(conn, "PUB500")["note"] == ""


def test_reassignment_keeps_reminder_clock(test_config):
    _insert_active_archive(test_config.database, "PUB501")
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="PUB501", data_contact_email="old@x.es",
                       reminder_count=2)
    set_data_contact(test_config, "PUB501", email="new@x.es", name="New")
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB501")
        assert a["reminder_count"] == 2
        assert a["next_reminder_at"] == "2026-01-19T00:00:00"
