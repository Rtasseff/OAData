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


def test_final_slot_generates_contact_pi_manual(test_config):
    """At reminder_count == max_reminders - 1, the row should be contact_pi_manual, not remind_sent."""
    max_rem = test_config.reminders.max_reminders
    _insert(
        test_config.database, "PUB001", OPEN_INACTIVE,
        next_reminder_at="2020-01-01T00:00:00",  # due
        reminder_count=max_rem - 1,
    )
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    reminder_rows = [r for r in rows if r["publication_id"] == "PUB001"]
    assert len(reminder_rows) == 1
    assert reminder_rows[0]["task_code"] == "contact_pi_manual"
    assert "manually contact PI" in reminder_rows[0]["task_text"]


def test_below_final_slot_still_generates_remind_sent(test_config):
    max_rem = test_config.reminders.max_reminders
    _insert(
        test_config.database, "PUB001", OPEN_INACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        reminder_count=max_rem - 2,  # one slot short of manual
    )
    path = generate_sheet(test_config)
    rows = _read_sheet(path)
    reminder_rows = [r for r in rows if r["publication_id"] == "PUB001"]
    assert len(reminder_rows) == 1
    assert reminder_rows[0]["task_code"] == "remind_sent"


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


# ── Stage 2: mandate-aware sheet generation ──────────────────────────


def _enriched_insert(db_path, pub_id, status, **enrichment):
    """Insert an archive with pub_db_last_refreshed_at set so it counts as classified."""
    enrichment.setdefault("pub_db_last_refreshed_at", "2026-05-07T00:00:00")
    _insert(db_path, pub_id, status, **enrichment)


def test_mandate_missing_archive_emits_only_mandate_missing_row(test_config):
    _enriched_insert(
        test_config.database, "PUB200", OPEN_ACTIVE,
        oa_mandate_missing=1, oa_data_required=None, oa_paper_required=None,
        next_reminder_at="2020-01-01T00:00:00",  # would be due — but suppressed
    )
    rows = _read_sheet(generate_sheet(test_config))
    assert len(rows) == 1
    r = rows[0]
    assert r["task_code"] == "mandate_missing"
    assert "investigate before closing" in r["note"]
    # No qa_pass or remind_sent rows
    assert all(rr["task_code"] == "mandate_missing" for rr in rows)


def test_no_oa_archive_emits_close_publication_only_row(test_config):
    _enriched_insert(
        test_config.database, "PUB201", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=0, oa_paper_required=0,
        next_reminder_at="2020-01-01T00:00:00",  # suppressed for no-OA
    )
    rows = _read_sheet(generate_sheet(test_config))
    assert len(rows) == 1
    r = rows[0]
    assert r["task_code"] == "close_publication_only"
    assert "No OA mandate" in r["note"]


def test_paper_only_archive_keeps_pipeline_with_note(test_config):
    _enriched_insert(
        test_config.database, "PUB202", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=0, oa_paper_required=1,
    )
    rows = _read_sheet(generate_sheet(test_config))
    # qa_pass row still emitted (OPEN_ACTIVE → qa_pass), with paper-only note
    assert len(rows) == 1
    r = rows[0]
    assert r["task_code"] == "qa_pass"
    assert "PAPER ONLY" in r["note"]


def test_paper_only_with_unknown_signals_treated_as_paper_only(test_config):
    """data_req=NULL + paper_req=1 (paper-only with some unknowns)."""
    _enriched_insert(
        test_config.database, "PUB203", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=None, oa_paper_required=1,
    )
    rows = _read_sheet(generate_sheet(test_config))
    assert len(rows) == 1
    assert rows[0]["task_code"] == "qa_pass"
    assert "PAPER ONLY" in rows[0]["note"]


def test_paper_only_archive_suppresses_reminders(test_config):
    _enriched_insert(
        test_config.database, "PUB204", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=0, oa_paper_required=1,
        next_reminder_at="2020-01-01T00:00:00",  # would normally be due
        reminder_count=0,
    )
    rows = _read_sheet(generate_sheet(test_config))
    task_codes = [r["task_code"] for r in rows]
    assert "remind_sent" not in task_codes
    assert "qa_pass" in task_codes  # pipeline still emitted


def test_data_required_archive_emits_standard_workflow(test_config):
    _enriched_insert(
        test_config.database, "PUB205", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=1, oa_paper_required=1,
        next_reminder_at="2020-01-01T00:00:00",
        reminder_count=1,
    )
    rows = _read_sheet(generate_sheet(test_config))
    task_codes = [r["task_code"] for r in rows]
    assert "remind_sent" in task_codes
    assert "qa_pass" in task_codes
    qa_row = [r for r in rows if r["task_code"] == "qa_pass"][0]
    assert qa_row["note"] == ""  # no auto-note for plain data-required


def test_unclassified_archive_uses_legacy_behavior(test_config):
    """Archives without pub_db_last_refreshed_at (no enrichment ever) keep old flow."""
    # Note: _insert (not _enriched_insert) — no refreshed timestamp
    _insert(
        test_config.database, "PUB206", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        reminder_count=0,
    )
    rows = _read_sheet(generate_sheet(test_config))
    task_codes = [r["task_code"] for r in rows]
    # Both reminder and pipeline rows emitted (legacy behavior)
    assert "remind_sent" in task_codes
    assert "qa_pass" in task_codes
