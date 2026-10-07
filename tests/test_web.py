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
    kwargs.setdefault("folder_path", f"/tmp/oa-web-test/{pub_id}")
    with get_connection(db_path) as conn:
        upsert_archive(
            conn, publication_id=pub_id,
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


def test_deposited_elsewhere_at_qa_records_external_deposit(test_config):
    """The 'deposited elsewhere' button applies archived_external: straight
    to OPEN_ZENODO_PUBLISHED with the DB-update and folder steps still to
    come — no Zenodo draft is created."""
    _insert(test_config.database, "100", OPEN_ACTIVE)
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "elsewhere", OPEN_ACTIVE)
    assert not out.ok and "DOI" in out.errors[0]
    out = tracker.perform(test_config, "alice", "100", "qa_pass", "elsewhere", OPEN_ACTIVE,
                          pid="10.1234/other.repo.1", url="https://repo.example.org/1")
    assert out.ok, out.errors
    a = tracker.get_archive(test_config, "100")
    assert a["status"] == OPEN_ZENODO_PUBLISHED
    assert (a["final_pid"], a["final_url"]) == ("10.1234/other.repo.1", "https://repo.example.org/1")
    assert a["zenodo_code"] is None
    assert tracker.events_for(test_config, "100")[0]["action_code"] == "archived_external"
    assert [r["task_code"] for r in tracker.pending_by_pub(test_config)["100"]] == \
        ["db_updated", "completion_sent"]


def test_deposited_elsewhere_at_the_draft_step_skips_the_api(test_config):
    _insert(test_config.database, "100", OPEN_READY_FOR_ZENODO_DRAFT)
    row_code = tracker.pending_by_pub(test_config)["100"][0]["task_code"]
    out = tracker.perform(test_config, "alice", "100", row_code, "elsewhere",
                          OPEN_READY_FOR_ZENODO_DRAFT,
                          pid="10.5281/zenodo.99", url="https://zenodo.org/records/99")
    assert out.ok, out.errors
    assert _status(test_config, "100") == OPEN_ZENODO_PUBLISHED


def test_system_draft_shows_the_identifiers_it_will_record(client, test_config):
    _insert(test_config.database, "100", "OPEN_ZENODO_DRAFT_CREATED",
            zenodo_code="4242", zenodo_doi="10.5281/zenodo.4242")
    page = client.get("/paper/100/").content.decode()
    assert "10.5281/zenodo.4242" in page
    assert "/records/4242" in page
    card = page.split('id="zenodo_validated"')[1].split("</form>")[0]
    assert "DOI to record" in card
    assert 'name="pid"' not in card          # nothing to type at the confirm step
    # ...but they can be corrected in the fold-out, which starts pre-filled
    alt = page.split('id="zenodo_validated"')[1].split("</details>")[0]
    assert "<summary>Different DOI or URL on Zenodo?" in alt
    assert 'value="10.5281/zenodo.4242"' in alt
    assert '/records/4242"' in alt


def test_hand_made_draft_has_no_correction_fold_out(client, test_config):
    _insert(test_config.database, "100", "OPEN_ZENODO_DRAFT_CREATED")
    page = client.get("/paper/100/").content.decode()
    assert "Different DOI or URL on Zenodo?" not in page
    assert 'value="correct"' not in page


def test_corrected_identifiers_are_recorded_as_entered(test_config):
    _insert(test_config.database, "100", "OPEN_ZENODO_DRAFT_CREATED",
            zenodo_code="4242", zenodo_doi="10.5281/zenodo.4242")
    row_code = tracker.pending_by_pub(test_config)["100"][0]["task_code"]
    assert row_code == "zenodo_validated"
    out = tracker.perform(test_config, "alice", "100", row_code, "correct",
                          "OPEN_ZENODO_DRAFT_CREATED", note="Zenodo shows a new version DOI",
                          pid="10.5281/zenodo.4243", url="https://zenodo.org/records/4243")
    assert out.ok, out.errors
    a = tracker.get_archive(test_config, "100")
    assert a["status"] == OPEN_ZENODO_PUBLISHED
    assert (a["final_pid"], a["final_url"]) == \
        ("10.5281/zenodo.4243", "https://zenodo.org/records/4243")
    ev = tracker.events_for(test_config, "100")[0]
    assert (ev["action_code"], ev["source"], ev["pid"]) == \
        ("fast_track_published", "web:alice", "10.5281/zenodo.4243")
    # both fields are required on the correction
    _insert(test_config.database, "101", "OPEN_ZENODO_DRAFT_CREATED", zenodo_code="5")
    out = tracker.perform(test_config, "alice", "101", "zenodo_validated", "correct",
                          "OPEN_ZENODO_DRAFT_CREATED", pid="10.5281/zenodo.5")
    assert not out.ok and _status(test_config, "101") == "OPEN_ZENODO_DRAFT_CREATED"


def test_button_press_pushes_that_row_to_sharepoint(test_config, monkeypatch):
    """The push runs in the background, scoped to the changed paper, and a
    failure never undoes the recorded action."""
    from django.conf import settings as dj
    from oa_tracker import auto
    from oa_tracker.config import SharePointSettings

    test_config.sharepoint = SharePointSettings(enabled=True)
    monkeypatch.setattr(dj, "OA_SHAREPOINT_PUSH", True)
    pushed = []
    monkeypatch.setattr(auto, "push_one", lambda cfg, pub: pushed.append(pub) or "row updated")
    threads = []
    import threading
    real_thread = threading.Thread
    monkeypatch.setattr(tracker.threading, "Thread",
                        lambda **kw: threads.append(real_thread(**kw)) or threads[-1])

    _insert(test_config.database, "100", OPEN_ACTIVE)
    assert tracker.perform(test_config, "alice", "100", "qa_pass", "pass", OPEN_ACTIVE).ok
    for t in threads:
        t.join(5)
    assert pushed == ["100"]
    assert tracker.LAST_PUSH["100"]["ok"] is True

    def boom(cfg, pub):
        raise RuntimeError("token expired")
    monkeypatch.setattr(auto, "push_one", boom)
    assert tracker.perform(test_config, "alice", "100", "zenodo_draft_created", "done",
                           OPEN_READY_FOR_ZENODO_DRAFT, zenodo_code="4242").ok
    for t in threads:
        t.join(5)
    assert tracker.LAST_PUSH["100"]["ok"] is False
    assert "token expired" in tracker.LAST_PUSH["100"]["text"]
    assert _status(test_config, "100") == "OPEN_ZENODO_DRAFT_CREATED"

    # "Try again" (after re-authenticating) re-runs the push and clears the warning.
    monkeypatch.setattr(auto, "push_one", lambda cfg, pub: pushed.append(pub) or "row updated")
    assert tracker.retry_push(test_config, "100").ok
    for t in threads:
        t.join(5)
    assert pushed == ["100", "100"]
    assert tracker.LAST_PUSH["100"]["ok"] is True

    monkeypatch.setattr(dj, "OA_SHAREPOINT_PUSH", False)
    assert not tracker.retry_push(test_config, "100").ok


# ── Exemptions (same list and routing as the Tracker List) ────────────

def test_exemptions_mirror_the_tracker_list():
    from oa_tracker import sharepoint as sp
    from oa_web import guide
    assert [e.text for e in guide.EXEMPTIONS] == sp.EXEMPTION_CHOICES
    for e in guide.EXEMPTIONS:
        if e.text != sp.EXEMPTION_OTHER:
            assert e.apply_code == sp.EXEMPTION_ROUTING[e.text][0]


@pytest.mark.parametrize("key,status", [
    ("no_share", "CLOSED_EXCEPTION"),
    ("no_data", "CLOSED_PUBLICATION_ONLY"),
    ("consult", "CLOSED_EXCEPTION"),
])
def test_true_exemptions_close_with_the_category_on_record(test_config, key, status):
    _insert(test_config.database, "100", OPEN_INACTIVE)
    out = tracker.apply_exemption(test_config, "alice", "100", key, OPEN_INACTIVE,
                                  note="PI replied by email",
                                  pid="10.5281/zenodo.1", url="https://zenodo.org/records/1")
    assert out.ok, out.errors
    a = tracker.get_archive(test_config, "100")
    assert a["status"] == status
    assert a["final_pid"] is None                 # a stray PID never rides along
    ev = tracker.events_for(test_config, "100")[0]
    assert ev["source"] == "web:alice"
    assert ev["note"].startswith("Exemption: ") and ev["note"].endswith("PI replied by email")


def test_external_exemption_needs_pid_and_url_and_keeps_the_later_steps(test_config):
    _insert(test_config.database, "100", OPEN_INACTIVE)
    out = tracker.apply_exemption(test_config, "alice", "100", "external", OPEN_INACTIVE,
                                  pid="10.1234/collab.9")
    assert not out.ok and _status(test_config, "100") == OPEN_INACTIVE

    out = tracker.apply_exemption(test_config, "alice", "100", "external", OPEN_INACTIVE,
                                  pid="10.1234/collab.9", url="https://repo.example.org/9")
    assert out.ok, out.errors
    a = tracker.get_archive(test_config, "100")
    assert a["status"] == OPEN_ZENODO_PUBLISHED
    assert (a["final_pid"], a["final_url"]) == ("10.1234/collab.9", "https://repo.example.org/9")
    assert tracker.events_for(test_config, "100")[0]["action_code"] == "archived_external"
    assert [r["task_code"] for r in tracker.pending_by_pub(test_config)["100"]] == \
        ["db_updated", "completion_sent"]


def test_other_exemption_needs_an_explanation(test_config):
    _insert(test_config.database, "100", OPEN_ACTIVE)
    assert not tracker.apply_exemption(test_config, "alice", "100", "other", OPEN_ACTIVE).ok
    assert tracker.events_for(test_config, "100") == []
    assert tracker.apply_exemption(test_config, "alice", "100", "other", OPEN_ACTIVE,
                                   note="data under a court order").ok
    assert _status(test_config, "100") == "CLOSED_EXCEPTION"


def test_exemption_refused_after_the_deposit_or_on_a_stale_page(test_config):
    _insert(test_config.database, "100", OPEN_ZENODO_PUBLISHED,
            final_pid="10.5281/zenodo.1", final_url="https://zenodo.org/records/1")
    assert not tracker.apply_exemption(test_config, "alice", "100", "no_data",
                                       OPEN_ZENODO_PUBLISHED).ok
    _insert(test_config.database, "200", OPEN_ACTIVE)
    assert not tracker.apply_exemption(test_config, "alice", "200", "no_data", OPEN_INACTIVE).ok
    assert not tracker.apply_exemption(test_config, "alice", "200", "bogus", OPEN_ACTIVE).ok
    assert _status(test_config, "200") == OPEN_ACTIVE


def test_list_offers_exemption_where_there_is_no_action(client, test_config):
    _insert(test_config.database, "100", OPEN_INACTIVE)            # waiting, nothing due
    _insert(test_config.database, "200", "CLOSED_EXCEPTION")
    page = client.get("/?show=all").content.decode()
    assert page.count("Apply exemption") == 1
    assert 'href="/paper/100/#exemption"' in page

    paper = client.get("/paper/100/").content.decode()
    assert '<details class="exempt" open>' in paper
    assert "Collaborative consultation only" in paper
    assert 'id="exemption"' not in client.get("/paper/200/").content.decode()

    r = client.post("/paper/100/do/", {
        "form": "exemption", "exemption": "no_data", "expected_status": OPEN_INACTIVE,
    })
    assert r.status_code == 302 and r["Location"] == "/paper/100/"
    assert _status(test_config, "100") == "CLOSED_PUBLICATION_ONLY"
    assert 'id="exemption"' not in client.get("/paper/100/").content.decode()


# ── Zenodo upload step + background jobs ──────────────────────────────

from oa_tracker import zenodo  # noqa: E402
from oa_tracker.config import ZenodoSettings  # noqa: E402
from tests.test_zenodo import FakeZenodo  # noqa: E402


class _Inline:
    """Stand-in for threading.Thread: runs the job at start()."""
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def jobs(monkeypatch):
    monkeypatch.setattr(tracker.threading, "Thread", _Inline)
    tracker.UPLOADS.clear()
    tracker.SYNC.clear()
    yield
    tracker.UPLOADS.clear()
    tracker.SYNC.clear()


@pytest.fixture
def zen(test_config, tmp_path, monkeypatch):
    test_config.zenodo = ZenodoSettings(enabled=True, environment="sandbox",
                                        token_file=tmp_path / "zenodorc",
                                        manifest_dir=tmp_path / "uploads")
    fake = FakeZenodo()
    fake.records["4242"] = {}
    fake.files["4242"] = {}
    monkeypatch.setattr(zenodo, "get_client", lambda settings: fake)
    return fake


def _system_draft(config, tmp_path, zip_size=None):
    folder = tmp_path / "pub100"
    folder.mkdir()
    if zip_size is None:
        (folder / "data.zip").write_bytes(b"zip")
    else:
        with open(folder / "data.zip", "wb") as f:
            f.truncate(zip_size)          # sparse
    (folder / "README.txt").write_text("readme")
    _insert(config.database, "100", "OPEN_ZENODO_DRAFT_CREATED", folder_path=str(folder),
            zenodo_code="4242", zenodo_env="sandbox", zenodo_doi="10.5281/zenodo.4242")
    with get_connection(config.database) as conn:
        insert_event(conn, "100", "zenodo_create_draft", OPEN_READY_FOR_ZENODO_DRAFT,
                     "OPEN_ZENODO_DRAFT_CREATED", "web:alice")


def _codes(config, pub_id="100"):
    return [r["task_code"] for r in tracker.pending_by_pub(config).get(pub_id, [])]


def test_new_draft_asks_for_the_upload_before_the_review(client, test_config, tmp_path, zen):
    _system_draft(test_config, tmp_path)
    assert _codes(test_config) == ["zenodo_upload_files"]
    page = client.get("/paper/100/").content.decode()
    card = page.split('id="zenodo_upload_files"')[1].split("</form>")[0]
    assert "Upload the data to the Zenodo draft" in card
    assert "The system can upload this package itself" in card
    assert 'value="now">' in card                     # enabled
    assert 'id="zenodo_validated"' not in page


def test_upload_now_runs_and_moves_on_to_the_review(test_config, tmp_path, zen, jobs):
    _system_draft(test_config, tmp_path)
    out = tracker.perform(test_config, "alice", "100", "zenodo_upload_files", "now",
                          "OPEN_ZENODO_DRAFT_CREATED")
    assert out.ok, out.errors
    assert set(zen.files["4242"]) == {"data.zip", "README.txt"}
    assert tracker.UPLOADS["100"]["state"] == "ok"
    ev = tracker.events_for(test_config, "100")[0]
    assert (ev["action_code"], ev["source"]) == ("zenodo_upload_files", "web:alice")
    assert _codes(test_config) == ["zenodo_validated"]


def test_upload_now_greyed_out_for_a_package_over_the_auto_limit(client, test_config,
                                                                tmp_path, zen, jobs):
    _system_draft(test_config, tmp_path, zip_size=6 * 1024**3)
    assert _codes(test_config) == ["zenodo_files_uploaded"]
    page = client.get("/paper/100/").content.decode()
    card = page.split('id="zenodo_files_uploaded"')[1].split("</form>")[0]
    assert "Manual upload needed." in card and "data.zip" in card
    assert 'value="now" disabled' in card
    out = tracker.perform(test_config, "alice", "100", "zenodo_files_uploaded", "now",
                          "OPEN_ZENODO_DRAFT_CREATED")
    assert not out.ok and "cannot upload" in out.errors[0]
    assert zen.calls == []


def test_over_quota_package_shows_the_policy_advice(client, test_config, tmp_path, zen):
    _system_draft(test_config, tmp_path, zip_size=70_000_000_000)
    page = client.get("/paper/100/").content.decode()
    assert "Over Zenodo&#x27;s 50 GB limit" in page or "Over Zenodo's 50 GB limit" in page
    assert "Manage storage" in page and "CIC biomaGUNE policy" in page
    assert "70.0 GB" in page                          # decimal, as Zenodo counts


def test_uploaded_by_hand_is_checked_and_recorded(test_config, tmp_path, zen, jobs):
    _system_draft(test_config, tmp_path, zip_size=6 * 1024**3)
    out = tracker.perform(test_config, "alice", "100", "zenodo_files_uploaded", "manual",
                          "OPEN_ZENODO_DRAFT_CREATED")
    assert not out.ok and "holds no files" in out.errors[0]      # nothing there yet
    zen.files["4242"]["data.zip"] = {"key": "data.zip", "status": "completed",
                                     "size": 6 * 1024**3}
    zen.files["4242"]["README.txt"] = {"key": "README.txt", "status": "completed", "size": 6}
    out = tracker.perform(test_config, "alice", "100", "zenodo_files_uploaded", "manual",
                          "OPEN_ZENODO_DRAFT_CREATED")
    assert out.ok, out.errors
    assert tracker.events_for(test_config, "100")[0]["action_code"] == "zenodo_files_uploaded"
    assert _codes(test_config) == ["zenodo_validated"]


def test_upload_refused_while_another_run_holds_the_lock(test_config, tmp_path, zen, jobs):
    _system_draft(test_config, tmp_path)
    fd = tracker._take_run_lock(test_config)            # e.g. the scheduled oa auto
    try:
        out = tracker.perform(test_config, "alice", "100", "zenodo_upload_files", "now",
                              "OPEN_ZENODO_DRAFT_CREATED")
    finally:
        tracker._release_run_lock(fd)
    assert not out.ok and "already running" in out.errors[0]
    assert zen.files["4242"] == {}


def test_run_the_automatic_update_now(client, test_config, jobs, monkeypatch):
    from oa_tracker import auto
    test_config.automation.enabled = True
    seen = []

    def fake_cycle(config):
        seen.append(config)
        return auto.AutoRunResult(started_at="2026-10-05T12:00:00"), \
            config.output_dir / "auto_digest.md"

    monkeypatch.setattr(auto, "run_cycle", fake_cycle)
    r = client.post("/sync/", {"next": "/actions/"})
    assert r.status_code == 302 and r["Location"] == "/actions/"
    assert seen == [test_config]
    assert tracker.SYNC["state"] == "ok" and tracker.SYNC["by"] == "alice"
    log = (test_config.output_dir / "auto_cron.log").read_text()
    assert "oa auto (web:alice)" in log and "=== exit 0 ===" in log
    assert "Automatic update finished" in client.get("/actions/").content.decode()


def test_automatic_update_refused_when_automation_is_off(test_config, jobs):
    test_config.automation.enabled = False
    out = tracker.start_sync(test_config, "alice")
    assert not out.ok and "switched off" in out.errors[0]
    assert tracker.SYNC == {}


def test_sync_redirect_stays_on_this_site(client, test_config, jobs):
    test_config.automation.enabled = False
    r = client.post("/sync/", {"next": "//evil.example.org/"})
    assert r["Location"] == "/"


def test_large_file_upload_now_stays_available_with_a_warning(client, test_config,
                                                              tmp_path, zen):
    test_config.zenodo.single_put_max_mb = 51200
    _system_draft(test_config, tmp_path, zip_size=20_000_000_000)
    page = client.get("/paper/100/").content.decode()
    card = page.split('id="zenodo_upload_files"')[1].split("</form>")[0]
    assert 'value="now">' in card                       # enabled
    assert "Large file: data.zip (20.0 GB)" in card
    assert "up to an hour" in card and "3 times" in card


def test_small_package_has_no_large_file_warning(client, test_config, tmp_path, zen):
    _system_draft(test_config, tmp_path)
    assert "Large file:" not in client.get("/paper/100/").content.decode()
