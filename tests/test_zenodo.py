"""Tests for the Zenodo module — pure logic with a fake client, no network."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import zipfile
from datetime import date
from pathlib import Path

import pytest

from oa_tracker import zenodo
from oa_tracker.config import ZenodoSettings


# ── Fake client ──────────────────────────────────────────────────────

class FakeZenodo:
    """In-memory stand-in for ZenodoClient covering the endpoints we use.

    Multipart knobs: ``multipart_supported=False`` rejects a type-M init
    with a 400-style ZenodoError (feature-detect fallback path);
    ``report_md5=False`` commits without an md5 checksum (S3-style
    backend), leaving only status+size to match on; ``corrupt_on_commit``
    garbles assembled multipart content (verification path).
    """

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.files: dict[str, dict[str, dict]] = {}   # record_id → key → entry
        self.published: set[str] = set()
        self.next_id = 100
        self.calls: list[tuple[str, str]] = []
        self.multipart_supported = True
        self.report_md5 = True
        self.corrupt_on_commit = False

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
            entries = []
            for spec in json_body:
                key = spec["key"]
                transfer = spec.get("transfer") or {}
                if transfer.get("type") == "M":
                    if not self.multipart_supported:
                        raise zenodo.ZenodoError(
                            "data",
                            "HTTP 400 from Zenodo: unsupported transfer type M",
                            400,
                        )
                    entry = {
                        "key": key, "status": "pending", "checksum": None,
                        "size": spec.get("size"), "_parts": {},
                        "links": {"parts": [
                            {"part": n,
                             "url": f"/api/records/{rid}/draft/files/{key}/content/{n}"}
                            for n in range(1, transfer["parts"] + 1)
                        ]},
                    }
                else:
                    entry = {"key": key, "status": "pending", "checksum": None}
                self.files[rid][key] = entry
                entries.append(entry)
            return 201, {"entries": entries}
        if method == "PUT" and "/content/" in path:
            rid = path.split("/")[3]
            key = urllib.parse.unquote(path.split("/")[-3])
            part = int(path.split("/")[-1])
            content = data.read() if hasattr(data, "read") else data
            self.files[rid][key]["_parts"][part] = content
            return 200, {}
        if method == "PUT" and path.endswith("/content"):
            rid = path.split("/")[3]
            key = urllib.parse.unquote(path.split("/")[-2])
            content = data.read() if hasattr(data, "read") else data
            self.files[rid][key]["_content"] = content
            return 200, {}
        if method == "POST" and path.endswith("/commit"):
            rid = path.split("/")[3]
            key = urllib.parse.unquote(path.split("/")[-2])
            entry = self.files[rid][key]
            if entry.get("_parts"):
                assembled = b"".join(
                    entry["_parts"][n] for n in sorted(entry["_parts"])
                )
                if self.corrupt_on_commit:
                    assembled += b"CORRUPT"
                entry["_content"] = assembled
            entry["status"] = "completed"
            entry["size"] = len(entry.get("_content", b""))
            entry["_md5"] = hashlib.md5(entry.get("_content", b"")).hexdigest()
            entry["checksum"] = ("md5:" + entry["_md5"]) if self.report_md5 else None
            return 200, {}
        if method == "DELETE" and "/draft/files/" in path:
            rid = path.split("/")[3]
            key = urllib.parse.unquote(path.split("/")[-1])
            if key not in self.files[rid]:
                raise zenodo.ZenodoError("data", "HTTP 404 from Zenodo: no such file", 404)
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
    assert md["publisher"] == "Zenodo"   # UI auto-fills this; the API doesn't
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


# ── Multipart uploads (large files) ──────────────────────────────────

def _big_folder(tmp_path, size=2 * 1024 * 1024 + 512 * 1024):
    """A folder whose data.zip exceeds a 1 MB multipart threshold."""
    folder = tmp_path / "big"
    folder.mkdir()
    (folder / "data.zip").write_bytes(b"x" * size)
    return folder


def _mp(settings, threshold_mb=1, part_mb=1):
    settings.multipart_threshold_mb = threshold_mb
    settings.multipart_part_size_mb = part_mb
    return settings


def test_multipart_used_above_threshold(tmp_path, settings):
    fake = FakeZenodo()
    fake.files["100"] = {}
    folder = _big_folder(tmp_path)          # 2.5 MB → 3 parts at 1 MB
    res = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res.ok
    assert res.uploaded == ["data.zip"]
    entry = fake.files["100"]["data.zip"]
    assert sorted(entry["_parts"]) == [1, 2, 3]
    assert entry["_content"] == (folder / "data.zip").read_bytes()
    manifest = json.loads(
        (Path(settings.manifest_dir) / "100" / "manifest.json").read_text()
    )
    assert manifest["files"][0]["multipart"] is True


def test_small_file_still_single_put(tmp_path, settings):
    fake = FakeZenodo()
    fake.files["100"] = {}
    folder = tmp_path / "small"
    folder.mkdir()
    (folder / "data.zip").write_bytes(b"tiny")
    res = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res.ok and res.uploaded == ["data.zip"]
    assert "_parts" not in fake.files["100"]["data.zip"]


def test_multipart_idempotent_without_md5_checksum(tmp_path, settings):
    # S3-style backends may not report a whole-file md5. Idempotency must
    # fall back to completed-status + size — otherwise every future run
    # deletes and re-sends the entire large file.
    fake = FakeZenodo()
    fake.report_md5 = False
    fake.files["100"] = {}
    folder = _big_folder(tmp_path)
    res1 = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res1.ok and res1.uploaded == ["data.zip"]
    res2 = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res2.ok
    assert res2.uploaded == []
    assert res2.already_present == ["data.zip"]


def test_multipart_falls_back_to_single_put(tmp_path, settings):
    # Environment without transfer-type-M support: init 400s, upload
    # silently falls back to the plain single PUT.
    fake = FakeZenodo()
    fake.multipart_supported = False
    fake.files["100"] = {}
    folder = _big_folder(tmp_path)
    res = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res.ok
    assert res.uploaded == ["data.zip"]
    entry = fake.files["100"]["data.zip"]
    assert "_parts" not in entry
    assert entry["_content"] == (folder / "data.zip").read_bytes()


def test_multipart_verification_failure_is_an_error(tmp_path, settings):
    fake = FakeZenodo()
    fake.corrupt_on_commit = True
    fake.files["100"] = {}
    res = zenodo.upload_files(fake, "100", _big_folder(tmp_path), _mp(settings))
    assert not res.ok
    assert "does not match" in res.errors[0]


def test_stale_pending_entry_is_replaced(tmp_path, settings):
    # An interrupted earlier upload leaves a "pending" entry (right size,
    # no checksum) — it must be deleted and re-uploaded, not trusted.
    fake = FakeZenodo()
    folder = _big_folder(tmp_path)
    size = (folder / "data.zip").stat().st_size
    fake.files["100"] = {"data.zip": {
        "key": "data.zip", "status": "pending", "checksum": None, "size": size,
    }}
    res = zenodo.upload_files(fake, "100", folder, _mp(settings))
    assert res.ok
    assert res.replaced == ["data.zip"]
    assert res.uploaded == ["data.zip"]
    assert fake.files["100"]["data.zip"]["status"] == "completed"


def test_part_reader_slices_and_reseeks(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"0123456789")
    with zenodo._PartReader(p, 3, 4) as r:
        assert r.read() == b"3456"
        assert r.read() == b""
        r.seek(0)                    # rewinds to the SLICE start, not byte 0
        assert r.read(2) == b"34"
        assert r.read() == b"56"


def test_client_retry_rewinds_streamed_body(monkeypatch, tmp_path):
    # A failed attempt leaves a streamed body spent; the retry must
    # rewind it or it silently sends zero bytes.
    sent = []

    def fake_urlopen(req, timeout=None):
        body = req.data.read() if hasattr(req.data, "read") else req.data
        sent.append(body)
        if len(sent) == 1:
            raise OSError("connection dropped")

        class R:
            status = 200
            def read(self):
                return b"{}"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr(zenodo.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(zenodo.time, "sleep", lambda s: None)
    p = tmp_path / "f.bin"
    p.write_bytes(b"HELLO")
    client = zenodo.ZenodoClient("https://x.invalid", "tok")
    with open(p, "rb") as f:
        status, _ = client.request(
            "PUT", "/y", data=f,
            content_type="application/octet-stream", content_length=5,
        )
    assert status == 200
    assert sent == [b"HELLO", b"HELLO"]


def test_record_ui_url(settings):
    assert zenodo.record_ui_url(settings, "42") == "https://sandbox.zenodo.org/uploads/42"
    settings.environment = "production"
    assert zenodo.record_ui_url(settings, "42") == "https://zenodo.org/uploads/42"
