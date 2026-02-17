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
    assert "OPEN_ACTIVE" in content


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
