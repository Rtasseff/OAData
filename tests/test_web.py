"""Tests for the web UI (oa_web). Skipped when the [web] extras aren't installed."""

import csv
import os
import tempfile

import pytest

pytest.importorskip("django")
pytest.importorskip("markdown")
pytest.importorskip("whitenoise")

# Django settings are read once at import: keep its own tables, the
# secret key and the config lookup away from the real project.
_TMP = tempfile.mkdtemp(prefix="oa_web_test_")
os.environ["DJANGO_SETTINGS_MODULE"] = "oa_web.settings"
os.environ["OA_CONFIG"] = os.path.join(_TMP, "config.toml")
os.environ["OA_WEB_DB"] = os.path.join(_TMP, "oa_web.sqlite")
os.environ["OA_WEB_SECRET_KEY"] = "test-only"

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402

from oa_tracker.db import get_connection, insert_event, upsert_archive  # noqa: E402
from oa_tracker.sheet import generate_sheet  # noqa: E402
from oa_tracker.status import (  # noqa: E402
    OPEN_ACTIVE, OPEN_INACTIVE, OPEN_READY_FOR_ZENODO_DRAFT, OPEN_ZENODO_PUBLISHED,
)
from oa_web import tracker  # noqa: E402


def _insert(db_path, pub_id, status, **kwargs):
    kwargs.setdefault("data_contact_email", "dc@cicbiomagune.es")
    with get_connection(db_path) as conn:
        upsert_archive(
            conn, publication_id=pub_id, folder_path=f"/tmp/oa-web-test/{pub_id}",
            first_seen_at="2026-01-01T00:00:00", last_seen_at="2026-01-15T00:00:00",
            status=status, **kwargs,
        )


def _status(config, pub_id):
    return tracker.get_archive(config, pub_id)["status"]


def _tsv(path):
    with open(path) as f:
        return list(csv.DictReader(f, delimiter="\t"))


# ── Service layer ─────────────────────────────────────────────────────

def test_qa_pass_advances_and_audits_the_user(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "pass", OPEN_ACTIVE)
    assert out.ok, out.errors
    assert _status(test_config, "100") == OPEN_READY_FOR_ZENODO_DRAFT
    event = tracker.events_for(test_config, "100")[0]
    assert (event["action_code"], event["source"]) == ("qa_pass", "web:alice")


def test_qa_fail_needs_a_note_and_holds(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "fail", OPEN_ACTIVE)
    assert not out.ok and "note" in out.errors[0]
    assert tracker.events_for(test_config, "100") == []

    out = tracker.perform(test_config, "alice", "100", "qa_pass", "fail", OPEN_ACTIVE,
                          note="README missing")
    assert out.ok
    assert _status(test_config, "100") == OPEN_ACTIVE
    assert tracker.events_for(test_config, "100")[0]["action_code"] == "qa_hold"
    # QA is offered again afterwards.
    assert [r["task_code"] for r in tracker.pending_by_pub(test_config)["100"]] == ["qa_pass"]


def test_stray_pid_cannot_fast_track(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "pass", OPEN_ACTIVE,
                          pid="10.5281/zenodo.1", url="https://zenodo.org/records/1")
    assert out.ok
    assert _status(test_config, "100") == OPEN_READY_FOR_ZENODO_DRAFT


def test_stale_page_is_refused(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "pass", OPEN_INACTIVE)
    assert not out.ok
    assert _status(test_config, "100") == OPEN_ACTIVE


def test_action_not_pending_is_refused(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "folder_removed", "done", OPEN_ACTIVE)
    assert not out.ok
    assert _status(test_config, "100") == OPEN_ACTIVE


def test_email_sent_cannot_be_recorded_twice(test_config):
    _insert(test_config.database, "100", OPEN_INACTIVE)
    with get_connection(test_config.database) as conn:
        insert_event(conn, "100", "data_contact_handover", OPEN_INACTIVE, OPEN_INACTIVE, "auto")
    assert tracker.perform(test_config, "alice", "100", "handover_sent", "done", OPEN_INACTIVE).ok
    assert not tracker.perform(test_config, "alice", "100", "handover_sent", "done", OPEN_INACTIVE).ok
    sent = [e for e in tracker.events_for(test_config, "100") if e["action_code"] == "handover_sent"]
    assert len(sent) == 1


def test_close_exception_needs_a_note(test_config):
    _insert(test_config.database, "100", OPEN_INACTIVE)
    assert not tracker.perform(test_config, "alice", "100", "close_exception", "done",
                               OPEN_INACTIVE, other=True).ok
    assert tracker.perform(test_config, "alice", "100", "close_exception", "done",
                           OPEN_INACTIVE, note="by directive", other=True).ok
    assert _status(test_config, "100") == "CLOSED_EXCEPTION"


def test_sheet_files_stay_in_step_with_the_cli(test_config):
    """A web action retires the matching action_sheet.tsv row into
    action_history.tsv, so a later `oa apply` can't record it again."""
    _insert(test_config.database, "100", OPEN_ZENODO_PUBLISHED,
            final_pid="10.5281/zenodo.1", final_url="https://zenodo.org/records/1")
    _insert(test_config.database, "200", OPEN_ACTIVE)
    sheet_path = generate_sheet(test_config)
    assert ("100", "db_updated") in {(r["publication_id"], r["task_code"]) for r in _tsv(sheet_path)}

    assert tracker.perform(test_config, "alice", "100", "db_updated", "done",
                           OPEN_ZENODO_PUBLISHED).ok
    remaining = _tsv(sheet_path)
    assert {r["publication_id"] for r in remaining} == {"200"}   # 100's rows dropped
    history = _tsv(test_config.output_dir / "action_history.tsv")
    assert [(r["publication_id"], r["task_code"], r["done"]) for r in history] == \
        [("100", "db_updated", "1")]


def test_draft_download_is_confined_to_the_paper(test_config):
    (test_config.email_drafts_dir / "handover_100.eml").write_text("x")
    assert tracker.draft_path(test_config, "100", "handover_100.eml") is not None
    assert tracker.draft_path(test_config, "200", "handover_100.eml") is None
    assert tracker.draft_path(test_config, "100", "../handover_100.eml") is None
    assert tracker.draft_path(test_config, "100", "handover_100.sqlite") is None


# ── Views ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def web_user():
    call_command("migrate", interactive=False, verbosity=0)
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user("alice", password="pw-for-tests")


@pytest.fixture
def client(web_user, test_config, monkeypatch):
    monkeypatch.setattr(tracker, "get_config", lambda: test_config)
    c = Client(HTTP_HOST="localhost")
    c.force_login(web_user)
    return c


def test_pages_require_login(test_config, monkeypatch):
    monkeypatch.setattr(tracker, "get_config", lambda: test_config)
    r = Client(HTTP_HOST="localhost").get("/")
    assert r.status_code == 302 and r["Location"].startswith("/login/")


def test_pages_render(client, test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE, pub_title="A paper about things")
    for url in ("/", "/?show=todo", "/?show=all&q=things", "/actions/", "/report/",
                "/history/", "/paper/100/"):
        r = client.get(url)
        assert r.status_code == 200, url
    assert b"A paper about things" in client.get("/").content
    assert client.get("/paper/nope/").status_code == 404


def test_button_returns_to_the_paper_showing_the_next_step(client, test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    r = client.post("/paper/100/do/", {
        "task_code": "qa_pass", "choice": "pass", "expected_status": OPEN_ACTIVE,
    })
    assert r.status_code == 302 and r["Location"] == "/paper/100/"
    page = client.get("/paper/100/").content.decode()
    assert "Recorded: QA pass" in page
    assert 'id="zenodo_draft_created"' in page
    assert tracker.events_for(test_config, "100")[0]["source"] == "web:alice"
