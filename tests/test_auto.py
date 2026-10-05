"""Tests for the automation engine (auto.py) and the API-backed Zenodo
task codes in actions.py. All network is faked; DB is the tmp fixture."""

from __future__ import annotations

import pytest

from oa_tracker import auto, db, status as st, zenodo
from oa_tracker.actions import apply_single
from oa_tracker.config import ZenodoSettings

from tests.test_zenodo import FakeZenodo


NOW = "2026-07-02T10:00:00"


def _seed(config, pub_id="3290", status=st.OPEN_ACTIVE, **over):
    row = {
        "publication_id": pub_id,
        "folder_path": str(config.sharepoint_root / pub_id),
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "status": status,
        "pub_title": "A study of things",
        "pub_doi": "10.1000/j.thing.2026.01",
        "pub_journal": "Nature Things",
        "pub_year": 2026,
        "oa_data_required": 1,
        "oa_paper_required": 1,
        "oa_mandate_missing": 0,
        "pub_db_last_refreshed_at": NOW,
        "data_contact_name": "Susana Carregal Romero",
        "data_contact_email": "scarregal@cicbiomagune.es",
    }
    row.update(over)
    with db.get_connection(config.database) as conn:
        db.upsert_archive(conn, **row)
    return row


@pytest.fixture
def zen_config(test_config, tmp_path, monkeypatch):
    """test_config with Zenodo enabled against a FakeZenodo client."""
    test_config.zenodo = ZenodoSettings(
        enabled=True, environment="sandbox",
        token_file=tmp_path / "zenodorc",
        manifest_dir=tmp_path / "uploads",
    )
    test_config.automation.enabled = True
    fake = FakeZenodo()
    monkeypatch.setattr(zenodo, "get_client", lambda settings: fake)
    monkeypatch.setattr(
        zenodo, "fetch_publication_extras",
        lambda pub_id: {
            "abstract": "We did things.",
            "author": "Carregal Romero, Susana",
            "author_with_affiliation":
                "Carregal-Romero, S (Carregal-Romero, Susana)[ 1 ]",
            "first_author_name": "Susana Carregal Romero",
        },
    )
    test_config._fake_zenodo = fake
    return test_config


def _folder_with_package(config, pub_id="3290"):
    folder = config.sharepoint_root / pub_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.zip").write_bytes(b"zip-content")
    (folder / "README.txt").write_text("hello")
    return folder


# ── actions.py: API-backed Zenodo codes ──────────────────────────────

def test_zenodo_create_draft_applies(zen_config):
    _seed(zen_config, status=st.OPEN_READY_FOR_ZENODO_DRAFT)
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_create_draft")
    assert result.applied == 1 and not result.errors
    assert (old_s, new_s) == (st.OPEN_READY_FOR_ZENODO_DRAFT, st.OPEN_ZENODO_DRAFT_CREATED)
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "3290")
    assert a["zenodo_code"] == "100"
    assert a["zenodo_doi"] == "10.5281/zenodo.100"
    assert a["zenodo_env"] == "sandbox"
    assert a["zenodo_code_overridden"] == 1   # scan must not overwrite it
    # Payload actually landed on the (fake) API with the locked fields.
    payload = zen_config._fake_zenodo.records["100"]
    assert payload["metadata"]["resource_type"] == {"id": "dataset"}
    assert payload["metadata"]["related_identifiers"][0]["relation_type"] == {"id": "ispublishedin"}


def test_zenodo_create_draft_refuses_existing_code(zen_config):
    _seed(zen_config, status=st.OPEN_READY_FOR_ZENODO_DRAFT, zenodo_code="999")
    result, _, _ = apply_single(zen_config, "3290", "zenodo_create_draft")
    assert result.errors and result.applied == 0
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_READY_FOR_ZENODO_DRAFT


def test_zenodo_disabled_is_an_error(zen_config):
    zen_config.zenodo.enabled = False
    _seed(zen_config, status=st.OPEN_READY_FOR_ZENODO_DRAFT)
    result, _, _ = apply_single(zen_config, "3290", "zenodo_create_draft")
    assert result.errors and result.applied == 0


def test_zenodo_upload_files_applies(zen_config):
    _folder_with_package(zen_config)
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code="100", zenodo_env="sandbox")
    fake = zen_config._fake_zenodo
    fake.records["100"] = {}
    fake.files["100"] = {}
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_upload_files")
    assert result.applied == 1 and not result.errors
    assert old_s == new_s == st.OPEN_ZENODO_DRAFT_CREATED  # upload ≠ validation
    assert set(fake.files["100"]) == {"data.zip", "README.txt"}


def test_zenodo_publish_records_doi(zen_config):
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_VALIDATED,
          zenodo_code="100", zenodo_env="sandbox")
    fake = zen_config._fake_zenodo
    fake.records["100"] = {}
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_publish")
    assert result.applied == 1 and not result.errors
    assert new_s == st.OPEN_ZENODO_PUBLISHED
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "3290")
    assert a["final_pid"] == "10.5281/zenodo.100"
    assert a["final_url"] == "https://fake/records/100"
    assert "100" in fake.published


def test_zenodo_env_mismatch_refused(zen_config):
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_VALIDATED,
          zenodo_code="100", zenodo_env="production")   # config says sandbox
    result, _, _ = apply_single(zen_config, "3290", "zenodo_publish")
    assert result.errors and result.applied == 0
    assert zen_config._fake_zenodo.published == set()


# ── zenodo_validated: confirm an operator-published, system-made draft ──

def test_zenodo_validated_confirms_operator_published(zen_config):
    """System-created draft the operator reviewed + published in the UI.
    done=1 verifies it's published and auto-records the DOI + /records/ URL
    with NO hand entry and NO API re-publish."""
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code="100", zenodo_env="sandbox", zenodo_doi="10.5281/zenodo.100")
    zen_config._fake_zenodo.published.add("100")   # operator clicked Publish
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_validated")
    assert result.applied == 1 and not result.errors
    assert (old_s, new_s) == (st.OPEN_ZENODO_DRAFT_CREATED, st.OPEN_ZENODO_PUBLISHED)
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "3290")
    assert a["final_pid"] == "10.5281/zenodo.100"
    assert a["final_url"] == "https://sandbox.zenodo.org/records/100"
    assert a["zenodo_doi"] == "10.5281/zenodo.100"
    # It must NOT have called the publish action (operator did it in the UI).
    assert ("POST", "/api/records/100/draft/actions/publish") \
        not in zen_config._fake_zenodo.calls


def test_zenodo_validated_refuses_when_not_yet_published(zen_config):
    """done=1 but the operator hasn't actually clicked Publish → the
    /records/ view 404s → refuse, no status change, no data invented."""
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code="100", zenodo_env="sandbox")   # not in fake.published
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_validated")
    assert result.applied == 0 and result.errors
    assert "not published yet" in result.errors[0]
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ZENODO_DRAFT_CREATED


def test_zenodo_validated_handmade_draft_uses_manual_path(zen_config):
    """A draft the system did NOT create (no zenodo_code) falls through to
    the normal transition to OPEN_ZENODO_DRAFT_VALIDATED — where pid/url
    are entered by hand at the publish step. No API call."""
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED)   # no zenodo_code
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_validated")
    assert result.applied == 1 and not result.errors
    assert (old_s, new_s) == (st.OPEN_ZENODO_DRAFT_CREATED, st.OPEN_ZENODO_DRAFT_VALIDATED)
    assert zen_config._fake_zenodo.calls == []   # never touched the API


# ── auto engine: advance stage ───────────────────────────────────────

def test_auto_qc_advances_through_draft_and_upload(zen_config):
    """The headline flow: Tracker 'done' + package → qa_pass → draft +
    reserved DOI + upload, stopping at DRAFT_CREATED for validation."""
    _folder_with_package(zen_config)
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert not result.errors
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "3290")
    assert a["status"] == st.OPEN_ZENODO_DRAFT_CREATED   # stopped pre-validation
    assert a["zenodo_code"] == "100"
    assert a["zenodo_doi"] == "10.5281/zenodo.100"
    fake = zen_config._fake_zenodo
    assert set(fake.files["100"]) == {"data.zip", "README.txt"}
    assert fake.published == set()                        # never auto-published
    # Operator worklist points at the draft to validate.
    assert any("validate the Zenodo draft" in w for w in result.awaiting_operator)


def test_auto_qc_mismatch_done_without_package(zen_config):
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=0)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("missing README.txt" in m for m in result.mismatches)


def test_auto_qc_mismatch_package_without_done(zen_config):
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=0,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("no Tracker 'done' tick" in m for m in result.mismatches)


def test_auto_qc_requires_data_required_mandate(zen_config):
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=1,
          oa_data_required=0, oa_paper_required=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("isn't data-required" in m for m in result.mismatches)


def test_auto_qc_gate_off_does_nothing(zen_config):
    zen_config.automation.auto_qa_pass = False
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE


def test_auto_close_on_folder_removed(zen_config):
    _seed(zen_config, status=st.OPEN_DB_UPDATED,
          unexpected_missing_folder=1, final_pid="10.5281/zenodo.100")
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "3290")
    assert a["status"] == st.CLOSED_DATA_ARCHIVED
    assert a["final_pid"] == "10.5281/zenodo.100"


def test_no_auto_close_without_pid(zen_config):
    _seed(zen_config, status=st.OPEN_DB_UPDATED, unexpected_missing_folder=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_DB_UPDATED


def test_placeholder_never_gets_auto_draft(zen_config):
    _seed(zen_config, pub_id="SMN-1", status=st.OPEN_READY_FOR_ZENODO_DRAFT)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        a = db.get_archive(conn, "SMN-1")
    assert a["status"] == st.OPEN_READY_FOR_ZENODO_DRAFT
    assert a["zenodo_code"] is None
    assert any("placeholder" in w for w in result.awaiting_operator)


def test_upload_retry_for_created_draft_without_upload(zen_config):
    """A draft that was created (event on record) but whose upload never
    succeeded gets retried on the next run — and only then."""
    _folder_with_package(zen_config)
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code="100", zenodo_env="sandbox")
    fake = zen_config._fake_zenodo
    fake.records["100"] = {}
    fake.files["100"] = {}
    with db.get_connection(zen_config.database) as conn:
        db.insert_event(conn, "3290", "zenodo_create_draft",
                        st.OPEN_READY_FOR_ZENODO_DRAFT, st.OPEN_ZENODO_DRAFT_CREATED,
                        "auto")
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert set(fake.files["100"]) == {"data.zip", "README.txt"}
    # Second run: upload event now exists → no duplicate upload calls.
    calls_before = len(fake.calls)
    auto._advance(zen_config, auto.AutoRunResult(started_at=NOW))
    upload_calls = [c for c in fake.calls[calls_before:] if c[1].endswith("/content")]
    assert upload_calls == []


def test_hand_made_draft_not_auto_uploaded(zen_config):
    """No zenodo_create_draft event → the draft was made by hand; the
    engine leaves uploads to the operator."""
    _folder_with_package(zen_config)
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code="777", zenodo_env="sandbox")
    fake = zen_config._fake_zenodo
    fake.records["777"] = {}
    fake.files["777"] = {}
    auto._advance(zen_config, auto.AutoRunResult(started_at=NOW))
    assert fake.files["777"] == {}


def test_digest_written(zen_config):
    result = auto.AutoRunResult(started_at=NOW)
    result.auto_applied.append("3290: qa_pass")
    result.errors.append("something failed")
    path = auto.write_digest(zen_config, result)
    text = path.read_text()
    assert "3290: qa_pass" in text
    assert "something failed" in text
    assert (zen_config.output_dir / "auto_log.txt").exists()


def test_package_complete_helper():
    assert auto.package_complete({"package_has_zip": 1, "package_has_readme": 1,
                                  "package_has_manuscript": 1})
    assert not auto.package_complete({"package_has_zip": 1, "package_has_readme": 0,
                                      "package_has_manuscript": 1})
    # Rule update 2026-07-15: no manuscript beside the zip → not complete.
    assert not auto.package_complete({"package_has_zip": 1, "package_has_readme": 1})
    assert not auto.package_complete({})


def test_auto_qc_mismatch_missing_manuscript(zen_config):
    """zip + README but no manuscript beside the zip → auto-QC holds and
    the digest names the missing manuscript."""
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=0)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("manuscript (.doc/.docx/.pdf)" in m for m in result.mismatches)


# ── Rejected Tracker 'done' ticks ────────────────────────────────────

def test_rejected_done_routes_to_sheet_when_gate_off(zen_config):
    assert zen_config.automation.auto_reject_done is False  # validation-phase default
    _seed(zen_config, pub_id="3259", status=st.OPEN_INACTIVE, user_done_flag=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert any(m.startswith("3259: user says done but the publication folder is still empty")
               for m in result.mismatches)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3259")["user_done_flag"] == 1
        assert db.get_last_event(conn, "3259", "reject_done") is None


def test_rejected_done_applied_when_gate_on(zen_config):
    zen_config.automation.auto_reject_done = True
    _seed(zen_config, pub_id="3259", status=st.OPEN_INACTIVE, user_done_flag=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert not result.errors
    assert any("3259: 'done' tick rejected" in m for m in result.auto_applied)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3259")["user_done_flag"] == 0
        ev = db.get_last_event(conn, "3259", "reject_done")
        assert ev["source"] == "auto"
    assert any("reject_done_3259.eml" in w for w in result.awaiting_operator)
    # Next run: tick already cleared → nothing more to reject.
    again = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, again)
    assert not any("rejected" in m for m in again.auto_applied)


def test_worklist_flags_missing_contact_and_closed_folder(zen_config):
    _seed(zen_config, pub_id="3316", status=st.OPEN_INACTIVE, data_contact_email="TBD")
    _seed(zen_config, pub_id="3296", status=st.CLOSED_PUBLICATION_ONLY)
    with db.get_connection(zen_config.database) as conn:
        db.insert_event(conn, "3296", "closed_folder_present",
                        st.CLOSED_PUBLICATION_ONLY, st.CLOSED_PUBLICATION_ONLY, "scanner")
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert any(w.startswith("3316: no data contact on record") for w in result.awaiting_operator)
    assert any(w.startswith("3296: closed (CLOSED_PUBLICATION_ONLY) but its SharePoint folder")
               for w in result.awaiting_operator)


def test_untick_rejected_clears_list_tick_once(zen_config):
    from oa_tracker import sharepoint as sp_mod
    from oa_tracker.config import SharePointSettings
    from tests.test_sharepoint import SID, FakeGraph

    g = FakeGraph()
    lid, _, name_for = sp_mod.ensure_list(g, SID, SharePointSettings())
    done_col = name_for[sp_mod.D_PDONE]
    g.lists[lid]["items"]["I1"] = {"id": "I1", "fields": {
        name_for[sp_mod.D_PUBID]: "3259", done_col: True,
    }}
    _seed(zen_config, pub_id="3259", status=st.OPEN_INACTIVE)
    with db.get_connection(zen_config.database) as conn:
        db.insert_event(conn, "3259", "reject_done", st.OPEN_INACTIVE,
                        st.OPEN_INACTIVE, "cli", note="the folder is empty")

    items = sp_mod.fetch_items(g, SID, lid, name_for[sp_mod.D_PUBID])
    unticked, errors = auto.untick_rejected(zen_config, g, SID, lid, name_for, items)
    assert unticked == ["3259"] and errors == []
    fields = g.lists[lid]["items"]["I1"]["fields"]
    assert fields[done_col] is False
    assert fields[name_for[sp_mod.D_REQSTATUS]] == sp_mod.REQUEST_STATUS_NOT_DONE
    # Our own edit doesn't read back as a user change on the next pull.
    assert sp_mod.pull_proposals([g.lists[lid]["items"]["I1"]], name_for) == []
    with db.get_connection(zen_config.database) as conn:
        assert db.get_pending_untick(conn, "3259") is None

    # Nothing pending any more → no second PATCH.
    items = sp_mod.fetch_items(g, SID, lid, name_for[sp_mod.D_PUBID])
    assert auto.untick_rejected(zen_config, g, SID, lid, name_for, items) == ([], [])


def test_untick_rejected_records_when_row_already_clear(zen_config):
    """A pending untick whose List row isn't ticked (or isn't on the List)
    is closed out without a PATCH so it doesn't retry forever."""
    _seed(zen_config, pub_id="3259", status=st.OPEN_INACTIVE)
    with db.get_connection(zen_config.database) as conn:
        db.insert_event(conn, "3259", "reject_done", st.OPEN_INACTIVE,
                        st.OPEN_INACTIVE, "cli", note="x")

    class NoCalls:
        def request(self, *a, **k):
            raise AssertionError("no Graph call expected")

    unticked, errors = auto.untick_rejected(zen_config, NoCalls(), "S", "L", {}, {})
    assert unticked == [] and errors == []
    with db.get_connection(zen_config.database) as conn:
        assert db.get_pending_untick(conn, "3259") is None


# ── Upload step: hand uploads, and packages the system can't send ────

def _sparse(path, size):
    with open(path, "wb") as f:
        f.truncate(size)


def _system_draft(zen_config, pub_id="3290", code="100"):
    """A draft the system created (create event on record), no upload yet."""
    _seed(zen_config, pub_id, status=st.OPEN_ZENODO_DRAFT_CREATED,
          zenodo_code=code, zenodo_env="sandbox")
    zen_config._fake_zenodo.records[code] = {}
    zen_config._fake_zenodo.files[code] = {}
    with db.get_connection(zen_config.database) as conn:
        db.insert_event(conn, pub_id, "zenodo_create_draft",
                        st.OPEN_READY_FOR_ZENODO_DRAFT, st.OPEN_ZENODO_DRAFT_CREATED, "auto")


def _hand_uploaded(fake, code, **sizes):
    for key, size in sizes.items():
        fake.files[code][key] = {"key": key, "status": "completed", "size": size,
                                 "checksum": "md5:whatever"}


def test_upload_pending_until_an_upload_is_recorded(zen_config):
    _folder_with_package(zen_config)
    _system_draft(zen_config)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_pending_upload(conn, "3290") is not None
    apply_single(zen_config, "3290", "zenodo_upload_files")
    with db.get_connection(zen_config.database) as conn:
        assert db.get_pending_upload(conn, "3290") is None


def test_hand_made_draft_never_pending_upload(zen_config):
    _seed(zen_config, status=st.OPEN_ZENODO_DRAFT_CREATED, zenodo_code="777")
    with db.get_connection(zen_config.database) as conn:
        assert db.get_pending_upload(conn, "3290") is None


def test_files_uploaded_refused_while_draft_is_empty(zen_config):
    _folder_with_package(zen_config)
    _system_draft(zen_config)
    result, _, _ = apply_single(zen_config, "3290", "zenodo_files_uploaded")
    assert result.applied == 0 and "holds no files" in result.errors[0]


def test_files_uploaded_refused_while_a_file_is_still_uploading(zen_config):
    _folder_with_package(zen_config)
    _system_draft(zen_config)
    zen_config._fake_zenodo.files["100"]["data.zip"] = {"key": "data.zip", "status": "pending"}
    result, _, _ = apply_single(zen_config, "3290", "zenodo_files_uploaded")
    assert result.applied == 0 and "still uploading" in result.errors[0]


def test_files_uploaded_records_a_hand_upload_of_any_size(zen_config):
    folder = _folder_with_package(zen_config)
    _sparse(folder / "data.zip", 70_000_000_000)        # over the 50 GB quota
    _system_draft(zen_config)
    fake = zen_config._fake_zenodo
    _hand_uploaded(fake, "100", **{"data.zip": 70_000_000_000, "README.txt": 5})
    result, old_s, new_s = apply_single(zen_config, "3290", "zenodo_files_uploaded",
                                        source="web:alice")
    assert result.applied == 1 and not result.errors and not result.warnings
    assert old_s == new_s == st.OPEN_ZENODO_DRAFT_CREATED
    with db.get_connection(zen_config.database) as conn:
        ev = db.get_last_event(conn, "3290", "zenodo_files_uploaded")
        assert db.get_pending_upload(conn, "3290") is None
    assert ev["source"] == "web:alice" and "70.0 GB" in ev["note"]
    assert not any(c[0] == "PUT" for c in fake.calls)   # nothing re-sent


def test_files_uploaded_warns_about_package_files_missing_on_the_draft(zen_config):
    _folder_with_package(zen_config)
    _system_draft(zen_config)
    _hand_uploaded(zen_config._fake_zenodo, "100", **{"README.txt": 5})
    result, _, _ = apply_single(zen_config, "3290", "zenodo_files_uploaded")
    assert result.applied == 1
    assert any("data.zip" in w for w in result.warnings)


def test_files_uploaded_without_zenodo_records_with_a_warning(zen_config):
    _folder_with_package(zen_config)
    _system_draft(zen_config)
    zen_config.zenodo.enabled = False
    result, _, _ = apply_single(zen_config, "3290", "zenodo_files_uploaded")
    assert result.applied == 1 and "without checking Zenodo" in result.warnings[0]


def test_files_uploaded_only_at_draft_created(zen_config):
    _seed(zen_config, status=st.OPEN_ACTIVE)
    result, _, _ = apply_single(zen_config, "3290", "zenodo_files_uploaded")
    assert result.applied == 0 and result.errors


def test_auto_leaves_a_too_big_package_for_hand_upload(zen_config):
    """Draft created this run; the zip is above the unattended limit, so
    no upload is attempted and the worklist says what to do by hand."""
    folder = _folder_with_package(zen_config)
    _sparse(folder / "data.zip", 6 * 1024**3)
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1, package_has_manuscript=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert not result.errors
    fake = zen_config._fake_zenodo
    assert fake.files["100"] == {}                     # draft made, nothing uploaded
    assert any("MANUAL UPLOAD" in w and "data.zip" in w for w in result.awaiting_operator)
    assert not any("validate the Zenodo draft" in w for w in result.awaiting_operator)


def test_auto_retry_skips_over_quota_package_every_run(zen_config):
    folder = _folder_with_package(zen_config)
    _sparse(folder / "data.zip", 60_000_000_000)
    _system_draft(zen_config)
    for _ in range(2):
        result = auto.AutoRunResult(started_at=NOW)
        auto._advance(zen_config, result)
        assert not result.errors
        assert any("MANUAL UPLOAD" in w and "Manage storage" in w
                   for w in result.awaiting_operator)
    assert zen_config._fake_zenodo.calls == []


def test_auto_after_hand_upload_points_at_review(zen_config):
    folder = _folder_with_package(zen_config)
    _sparse(folder / "data.zip", 6 * 1024**3)
    _system_draft(zen_config)
    _hand_uploaded(zen_config._fake_zenodo, "100", **{"data.zip": 6 * 1024**3, "README.txt": 5})
    apply_single(zen_config, "3290", "zenodo_files_uploaded")
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    assert any("validate the Zenodo draft" in w for w in result.awaiting_operator)
    assert not any("MANUAL UPLOAD" in w for w in result.awaiting_operator)
