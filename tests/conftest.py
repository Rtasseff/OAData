"""Shared fixtures for tests."""

import pytest
from pathlib import Path

from oa_tracker.config import Config, ReminderSettings
from oa_tracker.db import init_db


@pytest.fixture
def tmp_db(tmp_path):
    """Create and initialize a temporary database."""
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_sharepoint(tmp_path):
    """Create a temporary SharePoint-like folder structure."""
    root = tmp_path / "publications"
    root.mkdir()
    return root


@pytest.fixture
def tmp_templates(tmp_path):
    """Create temporary email templates."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "reminder.txt").write_text(
        "Reminder #${reminder_number} for ${publication_id}. "
        "Status: ${current_status}. Active since: ${became_active_at}."
    )
    (tpl_dir / "completion.txt").write_text(
        "Completed: ${publication_id}. PID: ${final_pid}. URL: ${final_url}."
    )
    return tpl_dir


@pytest.fixture
def test_config(tmp_path, tmp_db, tmp_sharepoint, tmp_templates):
    """Create a test Config pointing to temporary paths."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    email_dir = output_dir / "email_drafts"
    email_dir.mkdir()
    return Config(
        project_root=tmp_path,
        sharepoint_root=tmp_sharepoint,
        database=tmp_db,
        output_dir=output_dir,
        email_drafts_dir=email_dir,
        template_dir=tmp_templates,
        reminders=ReminderSettings(
            first_reminder_days=14,
            reminder_interval_days=7,
            max_reminders=5,
        ),
    )
