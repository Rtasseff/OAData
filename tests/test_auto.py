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


# ── auto engine: advance stage ───────────────────────────────────────

def test_auto_qc_advances_through_draft_and_upload(zen_config):
    """The headline flow: Tracker 'done' + package → qa_pass → draft +
    reserved DOI + upload, stopping at DRAFT_CREATED for validation."""
    _folder_with_package(zen_config)
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1)
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
          package_has_zip=1, package_has_readme=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("no Tracker 'done' tick" in m for m in result.mismatches)


def test_auto_qc_requires_data_required_mandate(zen_config):
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1,
          oa_data_required=0, oa_paper_required=1)
    result = auto.AutoRunResult(started_at=NOW)
    auto._advance(zen_config, result)
    with db.get_connection(zen_config.database) as conn:
        assert db.get_archive(conn, "3290")["status"] == st.OPEN_ACTIVE
    assert any("isn't data-required" in m for m in result.mismatches)


def test_auto_qc_gate_off_does_nothing(zen_config):
    zen_config.automation.auto_qa_pass = False
    _seed(zen_config, status=st.OPEN_ACTIVE, user_done_flag=1,
          package_has_zip=1, package_has_readme=1)
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
    assert auto.package_complete({"package_has_zip": 1, "package_has_readme": 1})
    assert not auto.package_complete({"package_has_zip": 1, "package_has_readme": 0})
    assert not auto.package_complete({})
