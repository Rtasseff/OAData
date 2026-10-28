"""Tests for oa_tracker.mailer — draft parsing and the Graph send, offline."""

import io
import json
import urllib.error
from dataclasses import replace

import pytest

from oa_tracker import mailer
from oa_tracker.config import EmailSettings, SharePointSettings
from oa_tracker.emails import _render_eml

RENDERED = (
    "To: Zuriñe Baz Maldonado <zbaz@cicbiomagune.es>\n"
    "Cc: Jordi Llop <jllop@cicbiomagune.es>\n"
    "Subject: PAST DUE - Reminder: research data upload pending for publication 3243\n"
    "\n"
    "Dear Zuriñe,\n\nPlease upload the data.\n"
)


@pytest.fixture
def office(test_config):
    test_config.email = EmailSettings(sender_email="office@cicbiomagune.es",
                                      send_from_web=True, draft_format="eml")
    test_config.sharepoint = SharePointSettings(client_id="test-client")
    return test_config


def test_reads_an_eml_draft_as_written_by_oa_emails(test_config, tmp_path):
    path = tmp_path / "reminder_3243_5_PASTDUE.eml"
    path.write_bytes(_render_eml(RENDERED, test_config))
    d = mailer.read_draft(path)
    assert d.to == (("Zuriñe Baz Maldonado", "zbaz@cicbiomagune.es"),)
    assert d.cc == (("Jordi Llop", "jllop@cicbiomagune.es"),)
    # Long enough to be folded in the file; read back without the fold.
    assert d.subject == "PAST DUE - Reminder: research data upload pending for publication 3243"
    assert d.body == "Dear Zuriñe,\n\nPlease upload the data.\n"
    assert d.problem() == ""


def test_reads_a_txt_draft(tmp_path):
    path = tmp_path / "handover_1.txt"
    path.write_text(RENDERED)
    d = mailer.read_draft(path)
    assert d.to_text == "Zuriñe Baz Maldonado <zbaz@cicbiomagune.es>"
    assert d.body.startswith("Dear Zuriñe")


@pytest.mark.parametrize("header, problem", [
    ("To: data contact <TBD>\n", "incomplete address"),
    ("", "no To address"),
])
def test_incomplete_drafts_are_not_sendable(tmp_path, header, problem):
    path = tmp_path / "d.txt"
    path.write_text(f"{header}Subject: s\n\nbody\n")
    assert problem in mailer.read_draft(path).problem()


def test_graph_message_sends_as_the_office_address(tmp_path):
    path = tmp_path / "d.txt"
    path.write_text(RENDERED)
    payload = mailer.graph_message(mailer.read_draft(path), "office@cicbiomagune.es")
    msg = payload["message"]
    assert msg["from"] == {"emailAddress": {"address": "office@cicbiomagune.es"}}
    assert msg["toRecipients"] == [{"emailAddress": {
        "address": "zbaz@cicbiomagune.es", "name": "Zuriñe Baz Maldonado"}}]
    assert msg["ccRecipients"][0]["emailAddress"]["address"] == "jllop@cicbiomagune.es"
    assert msg["body"]["contentType"] == "Text"
    assert payload["saveToSentItems"] is True


def test_unavailable_reason_names_the_missing_setting(office):
    assert mailer.unavailable_reason(office) == ""
    assert "send_from_web" in mailer.unavailable_reason(
        replace(office, email=replace(office.email, send_from_web=False)))
    assert "sender_email" in mailer.unavailable_reason(
        replace(office, email=replace(office.email, sender_email="")))


def test_an_incomplete_draft_never_reaches_microsoft(office, monkeypatch):
    monkeypatch.setattr(mailer, "_post", lambda *a: pytest.fail("must not send"))
    draft = mailer.Draft(to=(("", "TBD"),), cc=(), subject="s", body="b")
    with pytest.raises(mailer.SendError, match="incomplete address"):
        mailer.send_draft(office, draft)


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(outcome):
    def fake(req, timeout):
        fake.request = req
        if isinstance(outcome, BaseException):
            raise outcome
        return _Resp(outcome)
    return fake


def test_post_accepts_202(monkeypatch):
    fake = _urlopen(202)
    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake)
    mailer._post("tok", {"message": {}})
    assert fake.request.get_header("Authorization") == "Bearer tok"
    assert fake.request.full_url == mailer.SEND_URL


def test_post_refusal_is_not_sent(monkeypatch):
    body = json.dumps({"error": {"code": "ErrorSendAsDenied", "message": "no right"}}).encode()
    err = urllib.error.HTTPError(mailer.SEND_URL, 403, "Forbidden", {}, io.BytesIO(body))
    monkeypatch.setattr(mailer.urllib.request, "urlopen", _urlopen(err))
    with pytest.raises(mailer.SendError) as e:
        mailer._post("tok", {})
    assert "ErrorSendAsDenied" in str(e.value) and not e.value.maybe_sent


def test_post_timeout_may_have_sent(monkeypatch):
    monkeypatch.setattr(mailer.urllib.request, "urlopen", _urlopen(TimeoutError()))
    with pytest.raises(mailer.SendError) as e:
        mailer._post("tok", {})
    assert e.value.maybe_sent
