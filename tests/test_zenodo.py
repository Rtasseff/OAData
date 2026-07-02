"""Tests for the Zenodo module — pure logic with a fake client, no network."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from oa_tracker import zenodo
from oa_tracker.config import ZenodoSettings


# ── Fake client ──────────────────────────────────────────────────────

class FakeZenodo:
    """In-memory stand-in for ZenodoClient covering the endpoints we use."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.files: dict[str, dict[str, dict]] = {}   # record_id → key → entry
        self.published: set[str] = set()
        self.next_id = 100
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, json_body=None, data=None,
                content_type=None, content_length=None):
        self.calls.append((method, path))
        if method == "POST" and path == "/api/records":
            rid = str(self.next_id)
            self.next_id += 1
            self.records[rid] = json_body
            self.files[rid] = {}
            return 201, {"id": rid, "links": {"self_html": f"https://fake/uploads/{rid}"}}
        if method == "POST" and path.endswith("/draft/pids/doi"):
            rid = path.split("/")[3]
            return 201, {"pids": {"doi": {"identifier": f"10.5281/zenodo.{rid}"}}}
        if method == "GET" and path.endswith("/draft/files"):
            rid = path.split("/")[3]
            return 200, {"entries": list(self.files[rid].values())}
        if method == "POST" and path.endswith("/draft/files"):
            rid = path.split("/")[3]
            for entry in json_body:
                self.files[rid][entry["key"]] = {"key": entry["key"]}
            return 201, {}
        if method == "PUT" and path.endswith("/content"):
            rid = path.split("/")[3]
            key = path.split("/")[-2]
            import urllib.parse
            key = urllib.parse.unquote(key)
            content = data.read() if hasattr(data, "read") else data
            import hashlib
            self.files[rid][key]["_content"] = content
            self.files[rid][key]["_md5"] = hashlib.md5(content).hexdigest()
            return 200, {}
        if method == "POST" and path.endswith("/commit"):
            rid = path.split("/")[3]
            import urllib.parse
            key = urllib.parse.unquote(path.split("/")[-2])
            self.files[rid][key]["checksum"] = "md5:" + self.files[rid][key]["_md5"]
            return 200, {}
        if method == "DELETE" and "/draft/files/" in path:
            rid = path.split("/")[3]
            import urllib.parse
            key = urllib.parse.unquote(path.split("/")[-1])
            del self.files[rid][key]
            return 204, {}
        if method == "POST" and path.endswith("/actions/publish"):
            rid = path.split("/")[3]
            self.published.add(rid)
            return 202, {
                "pids": {"doi": {"identifier": f"10.5281/zenodo.{rid}"}},
                "links": {"self_html": f"https://fake/records/{rid}"},
            }
        raise AssertionError(f"unexpected call: {method} {path}")


@pytest.fixture
def settings(tmp_path):
    return ZenodoSettings(
        enabled=True, environment="sandbox",
        token_file=tmp_path / "zenodorc",
        manifest_dir=tmp_path / "uploads",
    )


def _archive(**over):
    base = {
        "publication_id": "3290",
        "pub_title": "A study of things",
        "pub_doi": "10.1000/j.thing.2026.01",
        "pub_journal": "Nature Things",
        "pub_year": 2026,
        "max_embargo_months": None,
        "corresponding_author_name": "Susana Carregal Romero",
        "data_contact_name": "Susana Carregal Romero",
        "data_contact_email": "scarregal@cicbiomagune.es",
    }
    base.update(over)
    return base


# ── Token loading ────────────────────────────────────────────────────

def test_load_token_missing_file(settings):
    with pytest.raises(zenodo.ZenodoError) as e:
        zenodo.load_token(settings)
    assert e.value.kind == "config"


def test_load_token_reads_environment_section(settings):
    settings.token_file.write_text(
        "[zenodo]\ntoken = prod-tok\n\n[zenodo-sandbox]\ntoken = sand-tok\n"
    )
    assert zenodo.load_token(settings) == "sand-tok"
    settings.environment = "production"
    assert zenodo.load_token(settings) == "prod-tok"


def test_load_token_missing_section(settings):
    settings.token_file.write_text("[zenodo]\ntoken = prod-tok\n")
    with pytest.raises(zenodo.ZenodoError) as e:
        zenodo.load_token(settings)  # sandbox section absent
    assert "zenodo-sandbox" in str(e.value)


# ── Author parsing ───────────────────────────────────────────────────

def test_parse_wos_authors():
    raw = ("Carregal-Romero, S (Carregal-Romero, Susana)[ 1,2 ] ; "
           "Smith, J (Smith, John)[ 3 ]")
    assert zenodo.parse_wos_authors(raw) == [
        ("Carregal-Romero", "Susana"), ("Smith", "John"),
    ]


def test_parse_wos_authors_short_form_fallback():
    assert zenodo.parse_wos_authors("Smith, J[ 1 ]") == [("Smith", "J")]


def test_parse_plain_authors():
    assert zenodo.parse_plain_authors("Smith, J.; Doe, Jane") == [
        ("Smith", "J."), ("Doe", "Jane"),
    ]


def test_build_creators_tags_biomagune_author():
    creators, used_fallback = zenodo.build_creators(
        "Carregal-Romero, S (Carregal-Romero, Susana)[ 1 ] ; Smith, J (Smith, John)[ 2 ]",
        None,
        ["Susana Carregal Romero"],
        "CIC biomaGUNE",
    )
    assert not used_fallback
    assert creators[0]["affiliations"] == [{"name": "CIC biomaGUNE"}]
    assert "affiliations" not in creators[1]
    assert creators[0]["person_or_org"]["family_name"] == "Carregal-Romero"


def test_build_creators_accent_insensitive_match():
    creators, _ = zenodo.build_creators(
        "Rodriguez, L (Rodriguez, Lara)", None,
        ["Lara Rodríguez Sánchez"], "CIC biomaGUNE",
    )
    assert creators[0].get("affiliations") == [{"name": "CIC biomaGUNE"}]


def test_build_creators_falls_back_to_plain_author():
    creators, used_fallback = zenodo.build_creators(
        "", "Smith, J.; Doe, Jane", [], "CIC biomaGUNE",
    )
    assert used_fallback
    assert len(creators) == 2


# ── Payload building ─────────────────────────────────────────────────

def test_payload_locked_fields(settings):
    payload = zenodo.build_record_payload(
        _archive(), settings,
        abstract="We did things.",
        author_with_affiliation="Carregal-Romero, S (Carregal-Romero, Susana)[ 1 ]",
        today=date(2026, 7, 2),
    )
    md = payload["metadata"]
    # Operator-locked rules (fixes to the IT script):
    assert md["resource_type"] == {"id": "dataset"}          # never publication
    assert md["rights"] == [{"id": "cc0-1.0"}]               # CC0 default
    assert payload["access"] == {"record": "public", "files": "public"}
    # Paper DOI ONLY as a related work, never as the record DOI:
    assert "doi" not in md and "pids" not in payload
    assert md["related_identifiers"] == [{
        "identifier": "10.1000/j.thing.2026.01",
        "scheme": "doi",
        "relation_type": {"id": "ispublishedin"},
        "resource_type": {"id": "publication-article"},
    }]
    assert md["publication_date"] == "2026-07-02"   # data date, not paper's
    assert md["version"] == "1.0.0"
    assert md["subjects"] == [{"subject": "CIC biomaGUNE"}]
    assert "We did things." in md["description"]
    assert md["contributors"][0]["role"] == {"id": "datacurator"}


def test_payload_embargo(settings):
    payload = zenodo.build_record_payload(
        _archive(max_embargo_months=6), settings, today=date(2026, 7, 2),
    )
    access = payload["access"]
    assert access["files"] == "restricted"
    assert access["record"] == "public"
    assert access["embargo"]["active"] is True
    assert access["embargo"]["until"] == "2027-01-02"


def test_payload_no_paper_doi_omits_related(settings):
    payload = zenodo.build_record_payload(_archive(pub_doi=None), settings)
    assert "related_identifiers" not in payload["metadata"]


def test_payload_creator_fallback_to_data_contact(settings):
    payload = zenodo.build_record_payload(_archive(), settings)  # no author data
    creators = payload["metadata"]["creators"]
    assert len(creators) == 1
    assert creators[0]["affiliations"] == [{"name": "CIC biomaGUNE"}]


def test_payload_title_fallback(settings):
    payload = zenodo.build_record_payload(_archive(pub_title=None), settings)
    assert payload["metadata"]["title"] == "Supporting data for publication 3290"


# ── Lifecycle ops against the fake client ────────────────────────────

def test_create_draft_reserves_doi(settings):
    fake = FakeZenodo()
    payload = zenodo.build_record_payload(_archive(), settings)
    draft = zenodo.create_draft(fake, payload)
    assert draft.record_id == "100"
    assert draft.doi == "10.5281/zenodo.100"
    assert fake.records["100"] == payload


def test_publish(settings):
    fake = FakeZenodo()
    draft = zenodo.create_draft(fake, zenodo.build_record_payload(_archive(), settings))
    out = zenodo.publish(fake, draft.record_id)
    assert out["doi"] == "10.5281/zenodo.100"
    assert draft.record_id in fake.published


# ── File discovery ───────────────────────────────────────────────────

def test_discover_files_package_mode(tmp_path):
    (tmp_path / "Datasets_articleDOI-x.zip").write_bytes(b"zipdata")
    (tmp_path / "README.txt").write_text("readme")
    (tmp_path / "postprint.pdf").write_bytes(b"pdf")
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    to_upload, skipped = zenodo.discover_files(tmp_path, "package")
    assert [p.name for p in to_upload] == ["Datasets_articleDOI-x.zip", "README.txt"]
    assert [p.name for p in skipped] == ["postprint.pdf"]


def test_discover_files_all_mode(tmp_path):
    (tmp_path / "data.zip").write_bytes(b"z")
    (tmp_path / "extra.csv").write_text("a,b")
    to_upload, skipped = zenodo.discover_files(tmp_path, "all")
    assert [p.name for p in to_upload] == ["data.zip", "extra.csv"]
    assert skipped == []


# ── Uploads (idempotent) ─────────────────────────────────────────────

def _folder_with_package(tmp_path):
    folder = tmp_path / "3290"
    folder.mkdir()
    (folder / "data.zip").write_bytes(b"zip-content")
    (folder / "README.txt").write_text("hello")
    return folder


def test_upload_files_uploads_and_manifests(tmp_path, settings):
    fake = FakeZenodo()
    fake.records["100"] = {}
    fake.files["100"] = {}
    folder = _folder_with_package(tmp_path)
    res = zenodo.upload_files(fake, "100", folder, settings)
    assert res.ok
    assert sorted(res.uploaded) == ["README.txt", "data.zip"]
    manifest = json.loads(
        (Path(settings.manifest_dir) / "100" / "manifest.json").read_text()
    )
    assert {e["key"] for e in manifest["files"]} == {"README.txt", "data.zip"}


def test_upload_files_idempotent_second_run(tmp_path, settings):
    fake = FakeZenodo()
    fake.records["100"] = {}
    fake.files["100"] = {}
    folder = _folder_with_package(tmp_path)
    zenodo.upload_files(fake, "100", folder, settings)
    res2 = zenodo.upload_files(fake, "100", folder, settings)
    assert res2.ok
    assert res2.uploaded == []
    assert sorted(res2.already_present) == ["README.txt", "data.zip"]


def test_upload_files_replaces_changed_file(tmp_path, settings):
    fake = FakeZenodo()
    fake.records["100"] = {}
    fake.files["100"] = {}
    folder = _folder_with_package(tmp_path)
    zenodo.upload_files(fake, "100", folder, settings)
    (folder / "data.zip").write_bytes(b"NEW-zip-content")
    res = zenodo.upload_files(fake, "100", folder, settings)
    assert res.ok
    assert res.replaced == ["data.zip"]
    assert res.uploaded == ["data.zip"]


def test_upload_files_empty_folder_errors(tmp_path, settings):
    fake = FakeZenodo()
    folder = tmp_path / "empty"
    folder.mkdir()
    res = zenodo.upload_files(fake, "100", folder, settings)
    assert not res.ok


def test_upload_reports_skipped_files(tmp_path, settings):
    fake = FakeZenodo()
    fake.records["100"] = {}
    fake.files["100"] = {}
    folder = _folder_with_package(tmp_path)
    (folder / "notes.docx").write_bytes(b"doc")
    res = zenodo.upload_files(fake, "100", folder, settings)
    assert res.ok
    assert res.skipped_local == ["notes.docx"]
    assert "notes.docx" in res.summary


def test_record_ui_url(settings):
    assert zenodo.record_ui_url(settings, "42") == "https://sandbox.zenodo.org/uploads/42"
    settings.environment = "production"
    assert zenodo.record_ui_url(settings, "42") == "https://zenodo.org/uploads/42"
