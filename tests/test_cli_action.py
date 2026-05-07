"""Tests for the `oa action` CLI command."""

from typer.testing import CliRunner

from oa_tracker.cli import app
from oa_tracker.db import get_archive, get_connection, get_recent_events, upsert_archive
from oa_tracker.status import (
    CLOSED_DATA_ARCHIVED,
    CLOSED_EXCEPTION,
    OPEN_ACTIVE,
    OPEN_INACTIVE,
    OPEN_READY_FOR_ZENODO_DRAFT,
    OPEN_ZENODO_PUBLISHED,
)

runner = CliRunner()


def _write_config(tmp_path, cfg_from):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[paths]\n'
        f'sharepoint_root = "{cfg_from.sharepoint_root}"\n'
        f'database = "{cfg_from.database}"\n'
        f'output_dir = "{cfg_from.output_dir}"\n'
        f'email_drafts_dir = "{cfg_from.email_drafts_dir}"\n'
        f'template_dir = "{cfg_from.template_dir}"\n'
        f'\n[reminders]\n'
        f'first_reminder_days = 14\n'
        f'reminder_interval_days = 7\n'
        f'max_reminders = 4\n'
    )
    return cfg


def _insert(db_path, pub_id, status, **kwargs):
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
            **kwargs,
        )


def test_action_qa_pass_advances_status(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "qa_pass", "--note", "looks good", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    assert "OPEN_ACTIVE → OPEN_READY_FOR_ZENODO_DRAFT" in result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_READY_FOR_ZENODO_DRAFT
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        evt = next(e for e in events if e["action_code"] == "qa_pass")
        assert evt["source"] == "cli"
        assert evt["note"] == "looks good"


def test_action_qa_hold_keeps_status_and_appends_note(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "qa_hold", "--note", "paper only, waiting on data",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    assert "status unchanged: OPEN_ACTIVE" in result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ACTIVE
        assert "waiting on data" in (archive["notes"] or "")


def test_action_close_exception(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_INACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "close_exception",
         "--note", "Directive from leadership to skip",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_EXCEPTION
        assert "leadership" in (archive["notes"] or "")


def test_action_fast_track_with_pid(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "qa_pass",
         "--pid", "10.5281/zenodo.123",
         "--url", "https://zenodo.org/record/123",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == OPEN_ZENODO_PUBLISHED
        assert archive["final_pid"] == "10.5281/zenodo.123"
        events = get_recent_events(conn, "2000-01-01T00:00:00")
        assert any(
            e["action_code"] == "fast_track_published" and e["source"] == "cli"
            for e in events
        )


def test_action_done2_full_closure(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "qa_pass",
         "--done", "2",
         "--pid", "10.5281/zenodo.999",
         "--note", "All done externally",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == CLOSED_DATA_ARCHIVED
        assert archive["final_pid"] == "10.5281/zenodo.999"


def test_action_rejects_unknown_task_code(test_config, tmp_path):
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "not_a_real_code", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "Unknown task_code" in result.stdout


def test_action_rejects_invalid_transition(test_config, tmp_path):
    # zenodo_published from OPEN_ACTIVE with no PID isn't a valid transition
    _insert(test_config.database, "PUB001", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB001", "zenodo_published", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "Invalid transition" in result.stdout or "Error" in result.stdout


def test_action_unknown_publication(test_config, tmp_path):
    cfg_file = _write_config(tmp_path, test_config)
    result = runner.invoke(
        app,
        ["action", "NOPE", "qa_pass", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "not in database" in result.stdout


def test_action_rejects_invalid_done_value(test_config, tmp_path):
    cfg_file = _write_config(tmp_path, test_config)
    result = runner.invoke(
        app,
        ["action", "PUB001", "qa_pass", "--done", "7", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "--done must be 1 or 2" in result.stdout


# ── Stage 2: override task codes via `oa action` ─────────────────────


def test_action_set_data_contact_via_cli(test_config, tmp_path):
    _insert(test_config.database, "PUB500", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB500", "set_data_contact",
         "--email", "ops@example.org", "--name", "Ops Team",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB500")
    assert a["data_contact_email"] == "ops@example.org"
    assert a["data_contact_name"] == "Ops Team"
    assert a["data_contact_overridden"] == 1


def test_action_reset_data_contact_via_cli(test_config, tmp_path):
    _insert(test_config.database, "PUB501", OPEN_ACTIVE,
            data_contact_email="x@y", data_contact_overridden=1)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB501", "reset_data_contact", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB501")
    assert a["data_contact_overridden"] == 0


def test_action_set_zenodo_code_via_cli(test_config, tmp_path):
    _insert(test_config.database, "PUB502", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB502", "set_zenodo_code",
         "--code", "98765", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB502")
    assert a["zenodo_code"] == "98765"
    assert a["zenodo_code_overridden"] == 1


def test_action_reset_zenodo_code_via_cli(test_config, tmp_path):
    _insert(test_config.database, "PUB503", OPEN_ACTIVE,
            zenodo_code="abc", zenodo_code_overridden=1)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB503", "reset_zenodo_code", "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB503")
    assert a["zenodo_code_overridden"] == 0


def test_action_set_data_contact_missing_email_errors(test_config, tmp_path):
    _insert(test_config.database, "PUB504", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB504", "set_data_contact", "--config", str(cfg_file)],
    )
    assert result.exit_code != 0
    assert "email" in result.stdout.lower()


def test_action_mandate_missing_acknowledges_with_note(test_config, tmp_path):
    _insert(test_config.database, "PUB505", OPEN_ACTIVE)
    cfg_file = _write_config(tmp_path, test_config)

    result = runner.invoke(
        app,
        ["action", "PUB505", "mandate_missing",
         "--note", "investigated, will close next week",
         "--config", str(cfg_file)],
    )
    assert result.exit_code == 0, result.stdout
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "PUB505")
    assert a["status"] == OPEN_ACTIVE  # unchanged
    assert "investigated" in (a["notes"] or "")
