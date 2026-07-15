"""Zenodo API integration — Stages 2.5 (drafts) and 3 (uploads + publish).

Built against Zenodo's current InvenioRDM-based REST API (``/api/records``),
NOT the legacy ``/api/deposit/depositions`` API. The legacy API's relation
vocabulary has no "Is published in" — the operator-specified link from the
dataset record to the paper — while the RDM vocabulary does (verified live
against ``https://zenodo.org/api/vocabularies/relationtypes`` on 2026-07-02:
``ispublishedin``, license ``cc0-1.0``, resource types ``dataset`` /
``publication-article``, contributor role ``datacurator`` all exist).
Endpoint reference: https://inveniordm.docs.cern.ch/reference/rest_api_drafts_records/

Separation of concerns (mirrors sharepoint.py):
  * ``ZenodoClient`` is the only thing that does network I/O. Everything
    else — metadata building, author parsing, file discovery — is pure and
    unit-tested with a fake client. No SQLite in here; actions.py / auto.py
    orchestrate against the database.

Operator-locked metadata rules (2026-07-02, correcting the IT script):
  * The dataset always gets a NEW Zenodo-minted DOI, reserved on the draft
    at creation time (``POST .../draft/pids/doi``) — never the paper's DOI.
  * ``resource_type`` is always ``dataset``.
  * License defaults to CC0 1.0 Universal (``cc0-1.0``).
  * The paper is linked as a related work: relation "Is published in"
    (``ispublishedin``), scheme DOI, resource type Journal article.
  * Visibility is Public (embargoed files only when the mandate requires).

Token storage — ``~/.zenodorc`` (mode 600, never in the repo):

    [zenodo]
    token = <production token>

    [zenodo-sandbox]
    token = <sandbox token>
"""

from __future__ import annotations

import configparser
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from oa_tracker.config import Config, ZenodoSettings

# Zenodo per-file and per-record limit (50 GB).
_MAX_BYTES = 50 * 1024**3

# Folder clutter never uploaded, whatever the upload_files mode.
_IGNORE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


class ZenodoError(RuntimeError):
    """A terminal Zenodo API failure, classified for the operator.

    ``kind`` is one of:
      * ``config`` — token missing/expired/wrong scope (401/403) or a
        missing token file. Fix the configuration; do not retry.
      * ``data`` — Zenodo rejected the payload (400/404). The metadata
        builder or our cached state needs fixing; do not retry.
      * ``transient`` — retries exhausted on 5xx/429/connection errors.
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def load_token(settings: ZenodoSettings) -> str:
    """Read the PAT for the configured environment from the token file."""
    path = Path(settings.token_file).expanduser()
    section = "zenodo" if settings.environment == "production" else "zenodo-sandbox"
    if not path.exists():
        raise ZenodoError(
            "config",
            f"Zenodo token file not found: {path} — create it (chmod 600) with a "
            f"[{section}] section containing `token = <your token>`.",
        )
    parser = configparser.ConfigParser()
    parser.read(path)
    token = parser.get(section, "token", fallback="").strip()
    if not token:
        raise ZenodoError(
            "config",
            f"No token under [{section}] in {path} — add `token = <your token>` "
            f"(environment is {settings.environment!r}).",
        )
    return token


# ── Client (the only I/O) ────────────────────────────────────────────

class ZenodoClient:
    """Thin JSON client with retry on 429/5xx/connection errors."""

    def __init__(self, base_url: str, token: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        data: bytes | Any = None,
        content_type: str | None = None,
        content_length: int | None = None,
    ) -> tuple[int, dict]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        body = data
        if json_body is not None:
            body = json.dumps(json_body).encode()
            content_type = "application/json"
        last_exc: Exception | None = None
        for attempt in range(3):
            # A streamed body (open file / _PartReader) is spent by a
            # failed attempt — rewind it or the retry sends zero bytes.
            if attempt and hasattr(body, "seek"):
                body.seek(0)
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Authorization", f"Bearer {self._token}")
            if content_type:
                req.add_header("Content-Type", content_type)
            if content_length is not None:
                req.add_header("Content-Length", str(content_length))
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    txt = resp.read().decode("utf-8")
                    return resp.status, (json.loads(txt) if txt.strip() else {})
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                if e.code == 429 and attempt < 2:
                    time.sleep(int(e.headers.get("Retry-After", "10")))
                    last_exc = e
                    continue
                if 500 <= e.code < 600 and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    last_exc = e
                    continue
                if e.code in (401, 403):
                    raise ZenodoError(
                        "config",
                        f"HTTP {e.code} from Zenodo — token missing, expired, or "
                        f"lacking deposit scope ({detail})",
                        e.code,
                    ) from e
                if 400 <= e.code < 500:
                    raise ZenodoError(
                        "data", f"HTTP {e.code} from Zenodo: {detail}", e.code
                    ) from e
                raise ZenodoError(
                    "transient", f"HTTP {e.code} from Zenodo after retries: {detail}", e.code
                ) from e
            except OSError as e:  # DNS/conn/timeouts
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    last_exc = e
                    continue
                raise ZenodoError("transient", f"connection to Zenodo failed: {e}") from e
        raise ZenodoError("transient", f"Zenodo retries exhausted: {last_exc}")


def get_client(settings: ZenodoSettings) -> ZenodoClient:
    return ZenodoClient(settings.base_url, load_token(settings))


# ── Author parsing (pure) ────────────────────────────────────────────

_WOS_PAREN = re.compile(r"\(([^)]+)\)")
_WOS_BRACKETS = re.compile(r"\[[^\]]*\]")


def _normalize(s: str) -> str:
    """Casefold + strip accents so 'Rodríguez' matches 'Rodriguez'."""
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()


def _split_family_given(name: str) -> tuple[str, str]:
    """'Family, Given' → (family, given); no comma → everything is family."""
    if "," in name:
        family, _, given = name.partition(",")
        return family.strip(), given.strip()
    return name.strip(), ""


def parse_wos_authors(author_with_affiliation: str) -> list[tuple[str, str]]:
    """Parse the Web of Science export format into (family, given) pairs.

    Entries look like ``Carregal-Romero, S (Carregal-Romero, Susana)[ 1,2 ]``
    separated by `` ; ``. The parenthesized full form is preferred; the
    short form is the fallback. Bracketed affiliation indices are dropped
    (the strings they point to are not in our data). Returns ``[]`` when
    the field doesn't look like WoS format at all.
    """
    text = (author_with_affiliation or "").strip()
    if not text:
        return []
    authors: list[tuple[str, str]] = []
    for entry in text.split(";"):
        entry = _WOS_BRACKETS.sub("", entry).strip()
        if not entry:
            continue
        m = _WOS_PAREN.search(entry)
        if m:
            name = m.group(1)
        else:
            name = entry.split("(")[0]
        family, given = _split_family_given(name)
        if family:
            authors.append((family, given))
    return authors


def parse_plain_authors(author: str) -> list[tuple[str, str]]:
    """Fallback parser for ``publication.author`` (``Last, F.; Last2, G.``)."""
    out: list[tuple[str, str]] = []
    for entry in (author or "").split(";"):
        family, given = _split_family_given(entry.strip())
        if family:
            out.append((family, given))
    return out


def _matches_person(family: str, given: str, known_name: str) -> bool:
    """True when a parsed author matches a biomaGUNE person's display name
    (``center_user.name``, e.g. 'Susana Carregal Romero') by surname +
    first-initial, accent- and case-insensitive."""
    known = _normalize(known_name)
    if not known:
        return False
    fam_tokens = [t for t in _normalize(family).replace("-", " ").split() if t]
    if not fam_tokens or not all(t in known for t in fam_tokens):
        return False
    if given:
        initial = _normalize(given)[0]
        return any(tok.startswith(initial) for tok in known.split())
    return True


def build_creators(
    author_with_affiliation: str | None,
    author_fallback: str | None,
    biomagune_names: list[str],
    affiliation: str,
) -> tuple[list[dict], bool]:
    """Build the RDM ``creators`` list. Returns ``(creators, used_fallback)``.

    Every author is listed by name; ``affiliation`` is attached only to
    authors matching a known biomaGUNE person (corresponding/first author
    from the central DB FK tables) — external co-authors' affiliations are
    not in our data and are left blank, per docs/zenodo_design.md.
    """
    parsed = parse_wos_authors(author_with_affiliation or "")
    used_fallback = False
    if not parsed:
        parsed = parse_plain_authors(author_fallback or "")
        used_fallback = True
    creators: list[dict] = []
    for family, given in parsed:
        entry: dict[str, Any] = {
            "person_or_org": {
                "type": "personal",
                "family_name": family,
                **({"given_name": given} if given else {}),
            }
        }
        if any(_matches_person(family, given, n) for n in biomagune_names if n):
            entry["affiliations"] = [{"name": affiliation}]
        creators.append(entry)
    return creators, used_fallback


# ── Metadata building (pure) ─────────────────────────────────────────

_DESCRIPTION_WITH_ABSTRACT = (
    "<p>This record contains the supporting research data for the publication "
    "&ldquo;{title}&rdquo; by {first_author} et al., {journal} ({year}), "
    'DOI: <a href="https://doi.org/{doi}">{doi}</a>.</p>'
    "<p>Abstract (reproduced from the original publication):</p>"
    "<p>{abstract}</p>"
)

_DESCRIPTION_NO_ABSTRACT = (
    "<p>This record contains the supporting research data for the publication "
    "&ldquo;{title}&rdquo; by {first_author} et al., {journal} ({year}), "
    'DOI: <a href="https://doi.org/{doi}">{doi}</a>.</p>'
    "<p>See the original publication for full context.</p>"
)


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def build_record_payload(
    archive: dict[str, Any],
    settings: ZenodoSettings,
    *,
    abstract: str | None = None,
    author_with_affiliation: str | None = None,
    author_fallback: str | None = None,
    extra_biomagune_names: list[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the full RDM draft payload for an archive (pure; no I/O).

    ``abstract`` / ``author_*`` come live from the central DB (they are
    not cached on the archive row); the rest comes from the archive and
    config. The paper DOI is only ever emitted as a related identifier.
    """
    today = today or date.today()
    pub_id = archive["publication_id"]
    title = archive.get("pub_title") or f"Supporting data for publication {pub_id}"
    journal = archive.get("pub_journal") or "publication"
    year = archive.get("pub_year") or ""
    pub_doi = (archive.get("pub_doi") or "").strip()

    biomagune_names = [
        archive.get("corresponding_author_name") or "",
        archive.get("data_contact_name") or "",
        *(extra_biomagune_names or []),
    ]
    creators, _ = build_creators(
        author_with_affiliation, author_fallback, biomagune_names,
        settings.default_affiliation,
    )
    if not creators:
        # A record must have at least one creator; fall back to the data
        # contact (they are, at minimum, the curator of this deposit).
        fallback_name = (
            archive.get("data_contact_name")
            or archive.get("corresponding_author_name")
            or settings.default_affiliation
        )
        family, given = _split_family_given(fallback_name)
        creators = [{
            "person_or_org": {
                "type": "personal",
                "family_name": family,
                **({"given_name": given} if given else {}),
            },
            "affiliations": [{"name": settings.default_affiliation}],
        }]

    first_author = creators[0]["person_or_org"]["family_name"]
    tpl = _DESCRIPTION_WITH_ABSTRACT if (abstract or "").strip() else _DESCRIPTION_NO_ABSTRACT
    description = tpl.format(
        title=title, first_author=first_author, journal=journal,
        year=year, doi=pub_doi or "(pending)", abstract=(abstract or "").strip(),
    )

    metadata: dict[str, Any] = {
        "resource_type": {"id": "dataset"},
        "title": title,
        # The DATA publication date (draft-creation time), not the paper's.
        "publication_date": today.isoformat(),
        "creators": creators,
        "description": description,
        # The Zenodo UI auto-fills Publisher as "Zenodo"; the API does not
        # (operator-observed on sandbox, 2026-07-02) — set it explicitly.
        "publisher": "Zenodo",
        "rights": [{"id": settings.default_license}],
        "subjects": [{"subject": k} for k in settings.default_keywords],
        "version": "1.0.0",
    }

    if pub_doi:
        metadata["related_identifiers"] = [{
            "identifier": pub_doi,
            "scheme": "doi",
            "relation_type": {"id": "ispublishedin"},
            "resource_type": {"id": "publication-article"},
        }]

    contact_name = (archive.get("data_contact_name") or "").strip()
    if contact_name:
        family, given = _split_family_given(contact_name)
        metadata["contributors"] = [{
            "person_or_org": {
                "type": "personal",
                "family_name": family,
                **({"given_name": given} if given else {}),
            },
            "role": {"id": "datacurator"},
            "affiliations": [{"name": settings.default_affiliation}],
        }]

    embargo_months = archive.get("max_embargo_months")
    access: dict[str, Any] = {"record": "public", "files": "public"}
    if embargo_months:
        access = {
            "record": "public",
            "files": "restricted",
            "embargo": {
                "active": True,
                "until": _add_months(today, int(embargo_months)).isoformat(),
                "reason": "Embargo required by the funder's OA mandate.",
            },
        }

    return {"access": access, "files": {"enabled": True}, "metadata": metadata}


def summarize_payload(payload: dict[str, Any]) -> str:
    """One-line operator summary for sheet notes / the digest."""
    md = payload.get("metadata", {})
    creators = md.get("creators", [])
    first = creators[0]["person_or_org"]["family_name"] if creators else "?"
    files_access = payload.get("access", {}).get("files", "?")
    return (
        f"'{md.get('title', '?')[:60]}' — {len(creators)} creator(s), first: {first}, "
        f"license {md.get('rights', [{}])[0].get('id', '?')}, files {files_access}"
    )


# ── Lifecycle operations (client-injected) ───────────────────────────

@dataclass
class DraftInfo:
    record_id: str
    doi: str | None          # reserved DOI (10.5281/zenodo.<id>) or None
    html_url: str


def record_ui_url(settings: ZenodoSettings, record_id: str) -> str:
    """The draft's edit page (the 'validate it in the browser' link)."""
    return f"{settings.base_url}/uploads/{record_id}"


def record_public_url(settings: ZenodoSettings, record_id: str) -> str:
    """The PUBLISHED record's public URL (``/records/``) — vs the draft's
    ``/uploads/``. Deterministic from the record id, so it's the final
    URL to record without scraping it back from Zenodo."""
    return f"{settings.base_url}/records/{record_id}"


def _extract_doi(body: dict, record_id: str) -> str:
    """Pull the Zenodo DOI out of a draft/record/reserve response.

    Zenodo puts it at the top-level ``doi`` (and ``metadata.doi``), NOT at
    InvenioRDM's ``pids.doi.identifier`` — which stays null. Falls back to
    the deterministic ``10.5281/zenodo.<id>`` so a quiet response never
    loses the DOI."""
    return (
        body.get("doi")
        or ((body.get("pids") or {}).get("doi") or {}).get("identifier")
        or (body.get("metadata") or {}).get("doi")
        or code_to_doi(record_id)
    )


def create_draft(client: ZenodoClient, payload: dict[str, Any]) -> DraftInfo:
    """Create a draft record and reserve its DOI. Returns id + reserved DOI.

    Reserving explicitly (rather than trusting mint-on-publish) is what
    guarantees the dataset gets its own Zenodo DOI, recorded in our
    system before anything is published.
    """
    _, body = client.request("POST", "/api/records", json_body=payload)
    record_id = str(body["id"])
    # The dataset DOI is deterministically 10.5281/zenodo.<record_id>;
    # record it now so our DB has it before publish (the design goal).
    # We still hit the reserve endpoint so the DOI is registered as
    # reserved on Zenodo's side, but we do NOT depend on parsing its
    # response: Zenodo returns the reserved DOI at the top-level `doi`
    # (and `metadata.doi`/`links.doi`), NOT at InvenioRDM's
    # `pids.doi.identifier` — which stays null, so the old pids-only read
    # always missed it and silently stored None. Reserve failure is
    # non-fatal (the same DOI mints at publish regardless).
    doi: str | None = code_to_doi(record_id)
    try:
        _, with_doi = client.request("POST", f"/api/records/{record_id}/draft/pids/doi")
        doi = _extract_doi(with_doi, record_id)
    except ZenodoError:
        pass
    links = body.get("links") or {}
    html_url = links.get("self_html") or ""
    return DraftInfo(record_id=record_id, doi=doi, html_url=html_url)


def get_draft(client: ZenodoClient, record_id: str) -> dict:
    _, body = client.request("GET", f"/api/records/{record_id}/draft")
    return body


def get_record(client: ZenodoClient, record_id: str) -> dict:
    """Fetch the PUBLISHED record. Raises ``ZenodoError`` with
    ``status == 404`` when the record isn't published yet (only a draft
    exists) — the signal we use to confirm the operator actually clicked
    Publish before recording it."""
    _, body = client.request("GET", f"/api/records/{record_id}")
    return body


def record_doi(record: dict, record_id: str) -> str:
    """The DOI of a fetched record (env-safe: read live, not derived)."""
    return _extract_doi(record, record_id)


def update_metadata(client: ZenodoClient, record_id: str, payload: dict[str, Any]) -> dict:
    """PUT the full draft body (access + files + metadata)."""
    _, body = client.request("PUT", f"/api/records/{record_id}/draft", json_body=payload)
    return body


def list_draft_files(client: ZenodoClient, record_id: str) -> dict[str, dict]:
    """filename → entry (with ``checksum`` like ``md5:<hex>`` once committed)."""
    _, body = client.request("GET", f"/api/records/{record_id}/draft/files")
    return {e["key"]: e for e in body.get("entries", [])}


def delete_draft_file(client: ZenodoClient, record_id: str, key: str) -> None:
    client.request("DELETE", f"/api/records/{record_id}/draft/files/{urllib.parse.quote(key)}")


def publish(client: ZenodoClient, record_id: str) -> dict:
    """Publish the draft. Returns ``{doi, html_url}`` from the record.

    Zenodo returns the minted DOI at the top-level ``doi`` field (and
    ``metadata.doi``), NOT at InvenioRDM's ``pids.doi.identifier`` — which
    is null even on PUBLISHED records. Reading pids-only stored an empty
    ``final_pid`` for the permanent record; read all known locations and
    fall back to the deterministic Zenodo form so the DOI is never lost."""
    _, body = client.request("POST", f"/api/records/{record_id}/draft/actions/publish")
    doi = _extract_doi(body, record_id)
    links = body.get("links") or {}
    return {"doi": doi, "html_url": links.get("self_html") or ""}


def discard_draft(client: ZenodoClient, record_id: str) -> None:
    client.request("DELETE", f"/api/records/{record_id}/draft")


# ── File discovery + upload (Stage 3) ────────────────────────────────

def _is_package_file(p: Path) -> bool:
    name = p.name.lower()
    return name.endswith(".zip") or (name.startswith("readme") and name.endswith(".txt"))


def discover_files(folder: Path, mode: str = "package") -> tuple[list[Path], list[Path]]:
    """Return ``(to_upload, skipped)`` for an archive folder.

    ``mode="package"`` uploads only the protocol package (``*.zip`` +
    ``README*.txt``); everything else lands in ``skipped`` so the caller
    can report it (never silently dropped). ``mode="all"`` uploads every
    non-clutter file. Files are taken from the folder root and one level
    of subfolders (the protocol puts the package at the root).
    """
    to_upload: list[Path] = []
    skipped: list[Path] = []
    if not folder.is_dir():
        return [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        if p.name.lower() in _IGNORE_NAMES or p.name.startswith("~$") or p.name.startswith("."):
            continue
        if mode == "all" or _is_package_file(p):
            to_upload.append(p)
        else:
            skipped.append(p)
    return to_upload, skipped


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _PartReader:
    """File-like view of one slice of a file, for streaming a multipart
    part without reading it into memory.

    ``seek(0)`` rewinds to the *slice start* — that is what the client's
    retry loop calls, so a failed part PUT restarts cleanly at the part
    boundary, never at byte 0 of the whole file.
    """

    def __init__(self, path: Path, offset: int, length: int):
        self._f = open(path, "rb")
        self._offset = offset
        self._length = length
        self._f.seek(offset)
        self._remaining = length

    def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        if n is None or n < 0 or n > self._remaining:
            n = self._remaining
        chunk = self._f.read(n)
        self._remaining -= len(chunk)
        return chunk

    def seek(self, pos: int) -> None:
        if pos != 0:
            raise ValueError("_PartReader only supports seek(0)")
        self._f.seek(self._offset)
        self._remaining = self._length

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "_PartReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _upload_multipart(
    client: ZenodoClient,
    record_id: str,
    key: str,
    path: Path,
    part_size: int,
    on_progress: Callable[[str], None] | None = None,
) -> bool:
    """Upload one large file via the InvenioRDM multipart transfer
    (type ``M``): init returns one URL per part; each part is an
    independent, retryable PUT; commit assembles the file server-side.

    Returns False when the server does not accept multipart — the
    caller falls back to the single-PUT path. Detection covers BOTH the
    init call and the part PUTs: verified live 2026-07-04 that Zenodo
    sandbox accepts a type-M init (and issues part URLs) but then denies
    the part uploads with 403 — the scaffolding is deployed, the feature
    isn't enabled for API users yet. Transient failures still raise.
    """
    size = path.stat().st_size
    parts = max(1, (size + part_size - 1) // part_size)
    try:
        _, resp = client.request(
            "POST", f"/api/records/{record_id}/draft/files",
            json_body=[{
                "key": key,
                "size": size,
                "transfer": {"type": "M", "parts": parts, "part_size": part_size},
            }],
        )
    except ZenodoError as e:
        if e.kind == "data":
            # Environment without multipart support — feature-detect
            # fallback. Clear any half-created entry, then let the
            # caller single-PUT.
            try:
                delete_draft_file(client, record_id, key)
            except ZenodoError:
                pass
            return False
        raise

    entry = next((e for e in resp.get("entries", []) if e.get("key") == key), None)
    part_urls = {
        p["part"]: p["url"]
        for p in ((entry or {}).get("links") or {}).get("parts", [])
    }
    if len(part_urls) < parts:
        # Accepted the init but gave no usable part links — treat like
        # an unsupported environment rather than guessing URLs.
        try:
            delete_draft_file(client, record_id, key)
        except ZenodoError:
            pass
        return False

    try:
        for i in range(1, parts + 1):
            offset = (i - 1) * part_size
            length = min(part_size, size - offset)
            if on_progress:
                on_progress(f"uploading {key} part {i}/{parts} ({length} bytes)")
            with _PartReader(path, offset, length) as reader:
                client.request(
                    "PUT", part_urls[i],
                    data=reader,
                    content_type="application/octet-stream",
                    content_length=length,
                )
        client.request(
            "POST",
            f"/api/records/{record_id}/draft/files/{urllib.parse.quote(key)}/commit",
        )
    except ZenodoError as e:
        if e.kind in ("config", "data"):
            # 4xx on a part PUT with a token that already passed the
            # init → multipart isn't enabled here. Clean up and fall
            # back; a genuinely bad token fails the single PUT loudly.
            try:
                delete_draft_file(client, record_id, key)
            except ZenodoError:
                pass
            return False
        raise
    return True


@dataclass
class UploadResult:
    uploaded: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped_local: list[str] = field(default_factory=list)   # not in upload mode
    manual_required: list[str] = field(default_factory=list)  # too big for unattended
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        parts = [
            f"uploaded {len(self.uploaded)}",
            f"already present {len(self.already_present)}",
        ]
        if self.replaced:
            parts.append(f"replaced {len(self.replaced)}")
        if self.manual_required:
            parts.append(f"MANUAL UPLOAD NEEDED: {', '.join(self.manual_required)}")
        if self.skipped_local:
            parts.append(f"not uploaded (outside package): {', '.join(self.skipped_local)}")
        if self.errors:
            parts.append(f"ERRORS: {'; '.join(self.errors)}")
        return "; ".join(parts)


def _entry_matches(entry: dict | None, local_md5: str, local_size: int) -> bool:
    """Is the remote draft entry the same file we have locally?

    Committed single-PUT uploads carry ``checksum = "md5:<hex>"`` —
    compare directly. Multipart-assembled files may report a non-md5
    checksum (or none), so fall back to completed-status + byte size.
    Without the fallback a completed large upload would look changed and
    be deleted + re-sent on every run. Stale ``pending`` entries (an
    interrupted upload) never match — status isn't ``completed``.
    """
    if not entry:
        return False
    checksum = entry.get("checksum") or ""
    if checksum.startswith("md5:"):
        return checksum.removeprefix("md5:") == local_md5
    return entry.get("status") == "completed" and entry.get("size") == local_size


def upload_files(
    client: ZenodoClient,
    record_id: str,
    folder: Path,
    settings: ZenodoSettings,
    on_progress: Callable[[str], None] | None = None,
) -> UploadResult:
    """Upload the archive folder's files to the draft, idempotently.

    Convergent: files already on the draft with a matching md5 are left
    alone; a local file whose checksum changed is deleted and re-uploaded;
    missing files are uploaded. A manifest is written next to the upload
    (``manifest_dir/<record_id>/manifest.json``) for the audit trail.
    Flattening: nested files upload under ``subdir_name`` keys (collision
    → error, never overwrite).
    """
    result = UploadResult()
    to_upload, skipped = discover_files(folder, settings.upload_files)
    result.skipped_local = [p.name for p in skipped]
    if not to_upload:
        result.errors.append(f"no uploadable files found in {folder}")
        return result

    # Flatten nested paths; refuse on collision.
    keyed: dict[str, Path] = {}
    for p in to_upload:
        rel = p.relative_to(folder)
        key = "_".join(rel.parts)
        if key in keyed:
            result.errors.append(f"filename collision after flattening: {key}")
            return result
        keyed[key] = p

    total = sum(p.stat().st_size for p in keyed.values())
    oversized = [k for k, p in keyed.items() if p.stat().st_size > _MAX_BYTES]
    if oversized or total > _MAX_BYTES:
        result.errors.append(
            f"upload exceeds Zenodo's 50 GB limit (total {total/1024**3:.1f} GB; "
            f"oversized: {', '.join(oversized) or 'none'}) — no upload method "
            "fixes this; split the deposit or contact Zenodo support"
        )
        return result

    threshold = settings.multipart_threshold_mb * 1024**2
    part_size = settings.multipart_part_size_mb * 1024**2
    single_put_max = settings.single_put_max_mb * 1024**2

    remote = list_draft_files(client, record_id)
    manifest_entries = []
    for key, path in keyed.items():
        local_md5 = _md5(path)
        local_size = path.stat().st_size
        entry = remote.get(key)
        used_multipart = False
        try:
            if _entry_matches(entry, local_md5, local_size):
                result.already_present.append(key)
            else:
                if entry:
                    # Covers changed files AND stale "pending" entries
                    # left by an interrupted upload — both restart clean.
                    delete_draft_file(client, record_id, key)
                    result.replaced.append(key)
                if local_size > threshold:
                    used_multipart = _upload_multipart(
                        client, record_id, key, path, part_size, on_progress,
                    )
                if not used_multipart and local_size > single_put_max:
                    # Multipart unavailable and the file is too big to
                    # single-PUT unattended — a mid-stream drop would
                    # re-send everything. Defer to the operator; a hand
                    # upload is recognised by checksum on the next run.
                    result.manual_required.append(key)
                    result.errors.append(
                        f"{key} ({local_size / 1024**3:.1f} GB): too large for an "
                        f"unattended single-PUT upload (> "
                        f"{settings.single_put_max_mb} MB) and Zenodo does not "
                        "currently accept multipart part uploads — upload this "
                        "file by hand to the draft, then re-run "
                        "zenodo_upload_files to record it (checksum match, no "
                        "bytes re-sent)"
                    )
                    continue
                if not used_multipart:
                    if on_progress:
                        on_progress(f"uploading {key} ({local_size} bytes)")
                    client.request(
                        "POST", f"/api/records/{record_id}/draft/files",
                        json_body=[{"key": key}],
                    )
                    with open(path, "rb") as f:
                        client.request(
                            "PUT",
                            f"/api/records/{record_id}/draft/files/{urllib.parse.quote(key)}/content",
                            data=f,
                            content_type="application/octet-stream",
                            content_length=local_size,
                        )
                    client.request(
                        "POST",
                        f"/api/records/{record_id}/draft/files/{urllib.parse.quote(key)}/commit",
                    )
                else:
                    # Multipart went through — verify the assembled file
                    # before trusting it (md5 when the server reports one,
                    # else committed-status + size).
                    committed = list_draft_files(client, record_id).get(key)
                    if not _entry_matches(committed, local_md5, local_size):
                        raise ZenodoError(
                            "transient",
                            f"multipart upload of {key} committed but the draft "
                            f"entry does not match the local file "
                            f"(checksum {(committed or {}).get('checksum')!r}, "
                            f"size {(committed or {}).get('size')!r} vs {local_size})",
                        )
                result.uploaded.append(key)
            manifest_entries.append({
                "key": key, "path": str(path), "md5": local_md5,
                "size": local_size,
                "multipart": used_multipart,
            })
        except ZenodoError as e:
            result.errors.append(f"{key}: {e}")

    manifest_dir = Path(settings.manifest_dir) / str(record_id)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"record_id": record_id, "files": manifest_entries}, indent=2)
    )
    return result


# ── Central-DB fields needed live at draft time ──────────────────────

def fetch_publication_extras(pub_id: str) -> dict[str, Any]:
    """Fetch abstract + author fields from the central DB (best-effort).

    These aren't cached on the archive row (they're only needed at draft
    time). Returns empty strings when the central DB is unreachable —
    the payload builder degrades gracefully (no-abstract template,
    data-contact-only creator fallback).
    """
    out = {"abstract": "", "author_with_affiliation": "", "author": "",
           "first_author_name": ""}
    try:
        from oa_tracker import pub_db
        conn = pub_db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT abstract, author, author_with_affiliation "
                    "FROM publication WHERE id = %s",
                    (pub_id,),
                )
                row = cur.fetchone()
                if row:
                    out["abstract"] = row.get("abstract") or ""
                    out["author"] = row.get("author") or ""
                    out["author_with_affiliation"] = row.get("author_with_affiliation") or ""
                # First author (for biomaGUNE affiliation tagging) — same
                # center_user join as the corresponding author.
                cur.execute(
                    "SELECT cu.name AS name FROM publi_first_auth pfa "
                    "JOIN center_user cu ON cu.id_user = pfa.id_user "
                    "WHERE pfa.id_publi = %s AND pfa.id_user > 0",
                    (pub_id,),
                )
                fa = cur.fetchone()
                if fa and fa.get("name"):
                    import html
                    out["first_author_name"] = html.unescape(fa["name"])
        finally:
            conn.close()
    except Exception:
        pass  # degrade gracefully; the caller notes the missing extras
    return out


def code_to_doi(zenodo_code: str) -> str:
    return f"10.5281/zenodo.{zenodo_code}"
