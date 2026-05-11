"""Tests for email draft generation."""

from oa_tracker.db import get_connection, upsert_archive
from oa_tracker.emails import generate_emails
from oa_tracker.status import OPEN_ACTIVE, OPEN_ZENODO_PUBLISHED


def test_no_emails_when_nothing_due(test_config):
    paths = generate_emails(test_config)
    assert paths == []


def test_reminder_email_generated(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=OPEN_ACTIVE,
            next_reminder_at="2020-01-01T00:00:00",  # past → due
        )

    paths = generate_emails(test_config)
    assert len(paths) == 1
    assert "reminder_PUB001_1" in paths[0].name
    content = paths[0].read_text()
    assert "PUB001" in content
    # Friendly name rendered via ${oa_status}; raw status via ${current_status}
    assert "OPEN_ACTIVE" in content
    assert "Active (files uploaded, awaiting QA)" in content


def test_no_reminder_email_at_manual_contact_stage(test_config):
    """At reminder_count >= max_reminders - 1, no automated reminder draft is generated."""
    max_rem = test_config.reminders.max_reminders
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=OPEN_ACTIVE,
            next_reminder_at="2020-01-01T00:00:00",  # due
            reminder_count=max_rem - 1,
        )

    paths = generate_emails(test_config)
    assert paths == []


def test_completion_email_generated(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB002",
            folder_path="/tmp/pub002",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            status=OPEN_ZENODO_PUBLISHED,
            final_pid="10.5281/zenodo.123",
            final_url="https://zenodo.org/record/123",
        )

    paths = generate_emails(test_config)
    assert len(paths) == 1
    assert "completion_PUB002" in paths[0].name
    content = paths[0].read_text()
    assert "10.5281/zenodo.123" in content
    assert "https://zenodo.org/record/123" in content


def test_both_reminder_and_completion(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="REM1",
            folder_path="/tmp/rem1",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=OPEN_ACTIVE,
            next_reminder_at="2020-01-01T00:00:00",
        )
        upsert_archive(
            conn,
            publication_id="COMP1",
            folder_path="/tmp/comp1",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            status=OPEN_ZENODO_PUBLISHED,
            final_pid="10.5281/zenodo.456",
            final_url="https://zenodo.org/record/456",
        )

    paths = generate_emails(test_config)
    assert len(paths) == 2
    names = {p.name for p in paths}
    assert any("reminder" in n for n in names)
    assert any("completion" in n for n in names)


# ── Stage 2: mandate-aware email behavior ─────────────────────────────


def _enriched_archive(db_path, pub_id, status, **enrichment):
    enrichment.setdefault("pub_db_last_refreshed_at", "2026-05-07T00:00:00")
    with get_connection(db_path) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=f"/tmp/{pub_id}",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=status,
            **enrichment,
        )


def test_reminder_suppressed_for_paper_only_archive(test_config):
    _enriched_archive(
        test_config.database, "PUB600", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        oa_data_required=0, oa_paper_required=1, oa_mandate_missing=0,
    )
    paths = generate_emails(test_config)
    assert all("reminder_PUB600" not in p.name for p in paths)


def test_reminder_suppressed_for_no_oa_archive(test_config):
    _enriched_archive(
        test_config.database, "PUB601", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        oa_data_required=0, oa_paper_required=0, oa_mandate_missing=0,
    )
    paths = generate_emails(test_config)
    assert all("reminder_PUB601" not in p.name for p in paths)


def test_reminder_suppressed_for_missing_mandate(test_config):
    _enriched_archive(
        test_config.database, "PUB602", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        oa_mandate_missing=1, oa_data_required=None, oa_paper_required=None,
    )
    paths = generate_emails(test_config)
    assert all("reminder_PUB602" not in p.name for p in paths)


def test_reminder_sent_for_data_required_archive(test_config):
    _enriched_archive(
        test_config.database, "PUB603", OPEN_ACTIVE,
        next_reminder_at="2020-01-01T00:00:00",
        oa_data_required=1, oa_paper_required=1, oa_mandate_missing=0,
        pub_title="Real Paper Title", data_contact_name="Contact A",
        data_contact_email="contact@example.org", max_embargo_months=0,
    )
    paths = generate_emails(test_config)
    reminder = [p for p in paths if p.name.startswith("reminder_PUB603")]
    assert len(reminder) == 1
    content = reminder[0].read_text()
    assert "PUB603" in content
    assert "Real Paper Title" in content
    assert "Contact A" in content
    assert "Open Data Required" in content


def test_legacy_archive_still_reminded(test_config):
    """Archive without pub_db_last_refreshed_at falls back to old behavior."""
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="LEGACY1",
            folder_path="/tmp/legacy",
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            became_active_at="2026-01-05T00:00:00",
            status=OPEN_ACTIVE,
            next_reminder_at="2020-01-01T00:00:00",
        )
    paths = generate_emails(test_config)
    assert any("reminder_LEGACY1" in p.name for p in paths)


def test_cheat_sheet_generated_for_ready_archive(test_config):
    from oa_tracker.status import OPEN_READY_FOR_ZENODO_DRAFT
    _enriched_archive(
        test_config.database, "PUB700", OPEN_READY_FOR_ZENODO_DRAFT,
        pub_title="My Paper", pub_doi="10.1/x", pub_journal="J", pub_year=2025,
        data_contact_email="c@example.org", data_contact_name="C",
        oa_paper_required=1, oa_data_required=1, max_embargo_months=0,
        oa_mandate_source="proj=1:data(0mo)",
        central_repository="Zenodo", central_repository_code="999",
        zenodo_code="999",
    )
    paths = generate_emails(test_config)
    cheat = [p for p in paths if p.parent.name == "zenodo_cheat" and p.name == "PUB700.txt"]
    assert len(cheat) == 1
    content = cheat[0].read_text()
    assert "PUB700" in content
    assert "My Paper" in content
    assert "10.1/x" in content
    assert "Zenodo" in content
    assert "999" in content


def test_cheat_sheet_not_generated_for_other_statuses(test_config):
    """Only ready/draft/validated statuses get cheat sheets."""
    _enriched_archive(test_config.database, "PUB701", OPEN_ACTIVE)
    paths = generate_emails(test_config)
    assert not any(p.parent.name == "zenodo_cheat" for p in paths)
