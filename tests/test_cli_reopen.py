"""Tests for the `oa reopen` CLI command."""

from typer.testing import CliRunner

from oa_tracker.cli import app
from oa_tracker.db import get_archive, get_connection, get_recent_events, upsert_archive
from oa_tracker.status import (
    CLOSED_DATA_ARCHIVED,
    CLOSED_EXCEPTION,
    OPEN_ACTIVE,
    OPEN_INACTIVE,
)

runner = CliRunner()


def _write_config(tmp_path, db_path, sharepoint_root, output_dir, email_dir, tpl_dir):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[paths]\n'
        f'sharepoint_root = "{sharepoint_root}"\n'
        f'database = "{db_path}"\n'
        f'output_dir = "{output_dir}"\n'
        f'email_drafts_dir = "{email_dir}"\n'
        f'template_dir = "{tpl_dir}"\n'
        f'\n[reminders]\n'
        f'first_reminder_days = 14\n'
        f'reminder_interval_days = 7\n'
        f'max_reminders = 4\n'
    )
    return cfg


def _insert_closed(db_path, pub_id, folder_path, status=CLOSED_EXCEPTION):
    with get_connection(db_path) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=str(folder_path),
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            status=status,
        )


def test_reopen_closed_exception_empty_folder_goes_inactive(test_config, tmp_path):
    # Empty folder in sharepoint root
    pub_folder = test_config.sharepoint_root / "PUB001"
    pub_folder.mkdir()
    _insert_closed(test_config.database, "PUB001", pub_folder, CLOSED_EXCEPTION)

    cfg_file = _write_config(
        tmp_path, test_config.database, test_config.sharepoint_root,
        test_config.output_dir, test_config.email_drafts_dir, test_config.template_dir,
    )

    result = runner.invoke(
        app,
        ["reopen", "PUB001", "--reason", "PI finally responded", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    assert "PUB001" in result.stdout
    assert "OPEN_INACTIVE" in result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_INACTIVE
        assert archive["reminder_count"] == 0
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        evt = next(e for e in events if e["action_code"] == "reopened")
        assert evt["old_status"] == CLOSED_EXCEPTION
        assert evt["new_status"] == OPEN_INACTIVE
        assert "responded" in (evt["note"] or "")


def test_reopen_closed_with_files_goes_active(test_config, tmp_path):
    pub_folder = test_config.sharepoint_root / "PUB002"
    pub_folder.mkdir()
    (pub_folder / "data.csv").write_text("x,y\n1,2\n")
    _insert_closed(test_config.database, "PUB002", pub_folder, CLOSED_EXCEPTION)

    cfg_file = _write_config(
        tmp_path, test_config.database, test_config.sharepoint_root,
        test_config.output_dir, test_config.email_drafts_dir, test_config.template_dir,
    )
    result = runner.invoke(
        app,
        ["reopen", "PUB002", "--reason", "Data arrived", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB002")
        assert archive["status"] == OPEN_ACTIVE
        assert archive["next_reminder_at"] is not None  # fresh cadence scheduled
        assert archive["reminder_count"] == 0


def test_reopen_rejects_open_archive(test_config, tmp_path):
    pub_folder = test_config.sharepoint_root / "PUB003"
    pub_folder.mkdir()
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB003",
            folder_path=str(pub_folder),
            first_seen_at="2026-01-01T00:00:00",
            last_seen_at="2026-01-15T00:00:00",
            status=OPEN_ACTIVE,
        )

    cfg_file = _write_config(
        tmp_path, test_config.database, test_config.sharepoint_root,
        test_config.output_dir, test_config.email_drafts_dir, test_config.template_dir,
    )
    result = runner.invoke(
        app,
        ["reopen", "PUB003", "--reason", "nope", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "not CLOSED" in result.stdout


def test_reopen_to_explicit_status(test_config, tmp_path):
    pub_folder = test_config.sharepoint_root / "PUB004"
    pub_folder.mkdir()
    (pub_folder / "f.txt").write_text("x")
    _insert_closed(test_config.database, "PUB004", pub_folder, CLOSED_DATA_ARCHIVED)

    cfg_file = _write_config(
        tmp_path, test_config.database, test_config.sharepoint_root,
        test_config.output_dir, test_config.email_drafts_dir, test_config.template_dir,
    )
    # Folder has files (auto would pick OPEN_ACTIVE), but force INACTIVE via --to.
    result = runner.invoke(
        app,
        ["reopen", "PUB004", "--reason", "reset", "--to", "OPEN_INACTIVE",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB004")
        assert archive["status"] == OPEN_INACTIVE


def test_reopen_rejects_unknown_pub(test_config, tmp_path):
    cfg_file = _write_config(
        tmp_path, test_config.database, test_config.sharepoint_root,
        test_config.output_dir, test_config.email_drafts_dir, test_config.template_dir,
    )
    result = runner.invoke(
        app,
        ["reopen", "NOPE", "--reason", "x", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "No archive found" in result.stdout
