"""SharePoint List parallel track — Graph sync engine.

Graduates the spike work (spike_sharepoint_push.py / _person.py) into a
config-driven, testable module. The design is docs/sharepoint_list_design.md.

Separation of concerns (mirrors zenodo.py):
  * ``GraphClient`` is the only thing that does network I/O and auth. It
    uses delegated device-code flow with a persisted MSAL token cache, so
    a scheduled run reuses the refresh token instead of re-prompting.
  * Everything else — column registry, field mapping, the push/diff
    orchestration — takes a client object (or plain dicts) and is unit
    tested with a fake client. No SQLite in here; actions.py / cli.py
    orchestrate against the database.

Proven on the live site (see roadmap 2026-06-02): list create, item
create/patch, choice/number/text writes, and Person-field writes via
``<Column>LookupId`` resolved from the site User Information List. The one
field type not yet live-verified is Hyperlink — the push is resilient
(per-row errors are collected, not fatal) so an unexpected payload shape
surfaces as a warning rather than aborting the sync.

Inbound (pull → ``propose_*`` action rows) is the next increment and is
intentionally not implemented here yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oa_tracker.config import Config, SharePointSettings

GRAPH = "https://graph.microsoft.com/v1.0"

# status code → friendly label shown on the list (never the raw code).
STATUS_LABELS = {
    "OPEN_INACTIVE": "Waiting for data",
    "OPEN_ACTIVE": "Data uploaded — under review",
    "OPEN_READY_FOR_ZENODO_DRAFT": "Ready to archive",
    "OPEN_ZENODO_DRAFT_CREATED": "Archive draft created",
    "OPEN_ZENODO_DRAFT_VALIDATED": "Archive draft validated",
    "OPEN_ZENODO_PUBLISHED": "Published to Zenodo",
    "OPEN_DB_UPDATED": "Recorded in publication DB",
    "CLOSED_DATA_ARCHIVED": "Done — data archived",
    "CLOSED_PUBLICATION_ONLY": "Closed — no data required",
    "CLOSED_EXCEPTION": "Closed — exception",
}

EXEMPTION_CHOICES = [
    "All data deposited in another archive",
    "No data shareable (sensitivity/confidentiality)",
    "No data generated (review/theory/perspective)",
    "Collaborative project AND no biomaGUNE data or lead",
    "Other — needs explanation",
]

# ── Display-name constants (single source for the column registry and
#    the field-building code, so the two never drift). ────────────────
D_PUBID = "Publication ID"
D_STATUS = "Status"
D_DATA = "Data archiving"
D_EMBARGO = "Embargo (months)"
D_CORR = "Corresponding author"
D_CORR_NAME = "Corresponding author (name)"
D_CONTACT = "Data contact"
D_CONTACT_NAME = "Data contact (name)"
D_FOLDER = "Folder"
D_DOI = "DOI"
D_JOURNAL = "Journal / year"
D_ZENODO = "Zenodo record"
D_SOP = "SOP"
D_SYNCED = "Last updated"
# user-editable
D_PDONE = "I think this is done"
D_PEXEMPT = "Propose exemption"
D_EXTPID = "External archive PID"
D_EXTURL = "External archive URL"
D_DETAIL = "Exemption / done detail"
D_REASSIGN = "Suggest a new data contact"
D_NOTES = "Notes"
# sync-internal
D_REQSTATUS = "Request status"
D_INGESTED = "Ingested signature"


def _text(multiline: bool = False) -> dict:
    return {"text": ({"allowMultipleLines": True} if multiline else {})}


def _choice(choices: list[str]) -> dict:
    return {"choice": {"choices": choices, "displayAs": "dropDownMenu"}}


def _person() -> dict:
    return {"personOrGroup": {"allowMultipleSelection": False, "chooseFromType": "peopleOnly"}}


# Column registry: drives both provisioning and the display→internal-name
# lookup. ``group`` is documentation of intent; provisioning creates all.
# v1 uses only column types proven to create via Graph in this tenant
# (text/choice/number/boolean/personOrGroup). Link columns and the
# timestamp are plain TEXT (URLs shown as strings) rather than
# hyperlinkOrPicture/dateTime — those facets are unverified here and are a
# later polish; using a proven type keeps provisioning from failing.
COLUMNS: list[dict[str, Any]] = [
    # system-owned
    {"display": D_PUBID, "name": "PubId", "group": "system", "indexed": True, "spec": _text()},
    {"display": D_STATUS, "name": "PipelineStatus", "group": "system", "spec": _choice(list(STATUS_LABELS.values()))},
    {"display": D_DATA, "name": "DataArchiving", "group": "system", "spec": _choice(["Required", "Not required", "Unknown"])},
    {"display": D_EMBARGO, "name": "EmbargoMonths", "group": "system", "spec": {"number": {}}},
    {"display": D_CORR, "name": "CorrAuthor", "group": "system", "spec": _person()},
    {"display": D_CORR_NAME, "name": "CorrAuthorName", "group": "system", "spec": _text()},
    {"display": D_CONTACT, "name": "DataContact", "group": "system", "spec": _person()},
    {"display": D_CONTACT_NAME, "name": "DataContactName", "group": "system", "spec": _text()},
    {"display": D_FOLDER, "name": "FolderLink", "group": "system", "spec": _text()},
    {"display": D_DOI, "name": "DoiLink", "group": "system", "spec": _text()},
    {"display": D_JOURNAL, "name": "JournalYear", "group": "system", "spec": _text()},
    {"display": D_ZENODO, "name": "ZenodoLink", "group": "system", "spec": _text()},
    {"display": D_SOP, "name": "SopLink", "group": "system", "spec": _text()},
    {"display": D_SYNCED, "name": "LastSynced", "group": "system", "spec": _text()},
    # user-editable
    {"display": D_PDONE, "name": "ProposedDone", "group": "user", "spec": {"boolean": {}}},
    {"display": D_PEXEMPT, "name": "ProposedExemption", "group": "user", "spec": _choice(EXEMPTION_CHOICES)},
    {"display": D_EXTPID, "name": "ExtArchivePid", "group": "user", "spec": _text()},
    {"display": D_EXTURL, "name": "ExtArchiveUrl", "group": "user", "spec": _text()},
    {"display": D_DETAIL, "name": "ProposalDetail", "group": "user", "spec": _text(multiline=True)},
    {"display": D_REASSIGN, "name": "ProposedDataContact", "group": "user", "spec": _person()},
    {"display": D_NOTES, "name": "UserNotes", "group": "user", "spec": _text(multiline=True)},
    # sync-internal
    {"display": D_REQSTATUS, "name": "RequestStatus", "group": "internal", "spec": _text()},
    # NOT created hidden: Graph-created hidden columns are not returned by the
    # columns API (breaks idempotent re-provision AND the pull-path dedup read).
    # Hide it from views via column config instead.
    {"display": D_INGESTED, "name": "IngestedSig", "group": "internal", "spec": _text()},
]


# ── Pure mappers (no I/O) ────────────────────────────────────────────

def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def data_archiving_label(archive: dict) -> str:
    if archive.get("oa_mandate_missing") == 1:
        return "Unknown"
    dr = archive.get("oa_data_required")
    if dr == 1:
        return "Required"
    if dr == 0:
        return "Not required"
    return "Unknown"


def folder_url(archive: dict, sp: SharePointSettings) -> str | None:
    """Best-effort SharePoint folder URL from the configured template.

    Returns None when no template is configured — the local sync path
    (``/mnt/c/...``) is not a usable web link, so we leave the column
    blank rather than show a broken value.
    """
    if not sp.folder_url_template:
        return None
    try:
        return sp.folder_url_template.format(pub_id=archive["publication_id"])
    except (KeyError, IndexError):
        return None


def build_system_fields(
    archive: dict,
    sp: SharePointSettings,
    name_for: dict[str, str],
    email_to_lookup: dict[str, str],
    now: str,
) -> dict[str, Any]:
    """Project a SQLite archive row into the SharePoint ``fields`` dict for
    the system-owned (Group A) columns.

    ``name_for`` maps display name → actual internal column name (Graph can
    munge names on create, so we always resolve from the live list).
    ``email_to_lookup`` maps lowercased institutional email → site user
    LookupId; Person columns are set only when the email resolves
    (tolerate-unmapped: the name column always carries the human label).
    Each field is written only if its column exists on the live list, so a
    partially provisioned list never breaks the push. Link columns are
    plain-text URL strings (the proven column type).
    """
    f: dict[str, Any] = {
        "Title": archive.get("pub_title") or f"Publication {archive['publication_id']}",
    }

    def put(display: str, value) -> None:
        col = name_for.get(display)
        if col is not None and value is not None and value != "":
            f[col] = value

    put(D_PUBID, archive["publication_id"])
    put(D_STATUS, status_label(archive["status"]))
    put(D_DATA, data_archiving_label(archive))
    put(D_SYNCED, now)
    if archive.get("max_embargo_months") is not None:
        put(D_EMBARGO, archive["max_embargo_months"])
    put(D_CORR_NAME, archive.get("corresponding_author_name"))
    put(D_CONTACT_NAME, archive.get("data_contact_name"))

    jy = []
    if archive.get("pub_journal"):
        jy.append(str(archive["pub_journal"]))
    if archive.get("pub_year"):
        jy.append(f"({archive['pub_year']})")
    if jy:
        put(D_JOURNAL, " ".join(jy))

    if archive.get("pub_doi"):
        put(D_DOI, f"https://doi.org/{archive['pub_doi']}")
    if archive.get("zenodo_code"):
        put(D_ZENODO, f"https://zenodo.org/records/{archive['zenodo_code']}")
    if sp.sop_url:
        put(D_SOP, sp.sop_url)
    furl = folder_url(archive, sp)
    if furl:
        put(D_FOLDER, furl)

    # Person columns: set <internalName>LookupId when the email resolves.
    dce = (archive.get("data_contact_email") or "").strip().lower()
    if dce and dce in email_to_lookup and name_for.get(D_CONTACT):
        f[name_for[D_CONTACT] + "LookupId"] = email_to_lookup[dce]
    cae = (archive.get("corresponding_author_email") or "").strip().lower()
    if cae and cae in email_to_lookup and name_for.get(D_CORR):
        f[name_for[D_CORR] + "LookupId"] = email_to_lookup[cae]

    return f


def diff_against_list(
    archives: list[dict],
    existing: dict[str, Any],
    sp: SharePointSettings,
) -> dict[str, list[str]]:
    """Read-only diff: what a push WOULD change. ``existing`` maps PubId →
    item. Pure — used by the read-only prototype to write nothing."""
    live_ids = {a["publication_id"] for a in archives}
    on_list = set(existing.keys())
    would_create = sorted(live_ids - on_list, key=_natural)
    would_update = sorted(live_ids & on_list, key=_natural)
    # Rows on the list that are no longer open (closed since last sync).
    would_remove = sorted(on_list - live_ids, key=_natural) if not sp.sync_closed else []
    return {
        "would_create": would_create,
        "would_update": would_update,
        "would_remove": would_remove,
    }


def _natural(pub_id: str):
    return (0, int(pub_id)) if pub_id.isdigit() else (1, pub_id)


# ── Result types ─────────────────────────────────────────────────────

@dataclass
class PushResult:
    created: int = 0
    updated: int = 0
    person_set: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"Created: {self.created}", f"Updated: {self.updated}",
                 f"Person columns set: {self.person_set}"]
        if self.warnings:
            parts.append(f"Warnings: {len(self.warnings)}")
            parts += [f"  - {w}" for w in self.warnings]
        if self.errors:
            parts.append(f"Errors: {len(self.errors)}")
            parts += [f"  - {e}" for e in self.errors]
        return "\n".join(parts)


@dataclass
class ReconcileResult:
    """Outcome of handling list rows whose archive is no longer open."""
    relabeled: int = 0   # row's Status updated to the closed label
    removed: int = 0     # row deleted from the list
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"Closed rows — relabeled: {self.relabeled}, removed: {self.removed}"]
        if self.warnings:
            parts.append(f"Warnings: {len(self.warnings)}")
            parts += [f"  - {w}" for w in self.warnings]
        return "\n".join(parts)


# ── GraphClient (the only I/O) ───────────────────────────────────────

class GraphClient:
    """Delegated Microsoft Graph client with a persisted MSAL token cache."""

    def __init__(self, sp: SharePointSettings, scopes=("Sites.Selected",), timeout: int = 30):
        if not sp.client_id:
            raise ValueError("sharepoint.client_id is not configured")
        self._authority = f"https://login.microsoftonline.com/{sp.tenant}"
        self._client_id = sp.client_id
        self._cache_path = Path(sp.token_cache).expanduser()
        self._scopes = list(scopes)
        self._timeout = timeout
        self._token: str | None = None

    # -- auth --
    def _load_cache(self):
        import msal
        cache = msal.SerializableTokenCache()
        if self._cache_path.exists():
            cache.deserialize(self._cache_path.read_text())
        return cache

    def _save_cache(self, cache) -> None:
        if cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(cache.serialize())
            try:
                os.chmod(self._cache_path, 0o600)
            except OSError:
                pass

    def authenticate(self) -> str:
        import msal
        cache = self._load_cache()
        app = msal.PublicClientApplication(
            self._client_id, authority=self._authority, token_cache=cache
        )
        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(self._scopes, account=accounts[0])
        if not result:
            flow = app.initiate_device_flow(scopes=self._scopes)
            if "user_code" not in flow:
                raise RuntimeError("device flow init failed: " + json.dumps(flow))
            print(flow["message"], flush=True)
            result = app.acquire_token_by_device_flow(flow)
        self._save_cache(cache)
        if "access_token" not in result:
            raise RuntimeError(
                "auth failed: " + result.get("error_description", json.dumps(result))
            )
        self._token = result["access_token"]
        return self._token

    # -- request with light retry on 429/5xx --
    def request(self, method: str, path: str, json_body: Any = None) -> tuple[int, dict]:
        if self._token is None:
            self.authenticate()
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self._token}")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    txt = resp.read().decode("utf-8")
                    return resp.status, (json.loads(txt) if txt.strip() else {})
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(int(e.headers.get("Retry-After", "5")))
                    continue
                if 500 <= e.code < 600 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")  # pragma: no cover

    # -- convenience reads used by the orchestration --
    def get_site_id(self, site: str) -> str:
        _, body = self.request("GET", f"/sites/{site}")
        return body["id"]

    def _read_user_info(self, site_id: str) -> list[dict]:
        """Raw User Information List rows as ``{id, email, name}``.

        Returns ``[]`` if the list can't be read with this token. Only users
        who've signed into the site appear — unresolved people stay unmapped
        by design.
        """
        uil_id = self._find_user_info_list(site_id)
        if not uil_id:
            return []
        rows: list[dict] = []
        url = (f"/sites/{site_id}/lists/{uil_id}/items"
               f"?$expand=fields($select=EMail,Title,UserName)&$top=200")
        while url:
            _, page = self.request("GET", url)
            for it in page.get("value", []):
                fields = it.get("fields") or {}
                email = (fields.get("EMail") or fields.get("UserName") or "").strip()
                rows.append({
                    "id": it["id"], "email": email,
                    "name": (fields.get("Title") or "").strip(),
                })
            url = page.get("@odata.nextLink")
        return rows

    def resolve_users(self, site_id: str) -> dict[str, str]:
        """email(lower) → site user LookupId (for *writing* Person columns)."""
        return {r["email"].lower(): r["id"] for r in self._read_user_info(site_id) if r["email"]}

    def resolve_user_details(self, site_id: str) -> dict[str, dict]:
        """LookupId → ``{"name", "email"}`` (for *reading* who a user picked
        in a Person column, e.g. a suggested new data contact)."""
        return {r["id"]: {"name": r["name"], "email": r["email"]} for r in self._read_user_info(site_id)}

    def _find_user_info_list(self, site_id: str) -> str | None:
        flt = urllib.parse.quote("displayName eq 'User Information List'")
        try:
            _, page = self.request("GET", f"/sites/{site_id}/lists?$filter={flt}")
            vals = page.get("value", [])
            if vals:
                return vals[0]["id"]
        except urllib.error.HTTPError:
            pass
        for name in ("User%20Information%20List", "users"):
            try:
                _, lst = self.request("GET", f"/sites/{site_id}/lists/{name}")
                if lst.get("id"):
                    return lst["id"]
            except urllib.error.HTTPError:
                continue
        return None


# ── Orchestration (client-injected; unit-tested with a fake client) ──

def get_list(client, site_id: str, list_name: str) -> dict | None:
    url = f"/sites/{site_id}/lists?$select=id,displayName,webUrl&$top=200"
    while url:
        _, page = client.request("GET", url)
        for lst in page.get("value", []):
            if lst.get("displayName") == list_name:
                return lst
        url = page.get("@odata.nextLink")
    return None


def column_names(client, site_id: str, list_id: str) -> dict[str, str]:
    _, page = client.request("GET", f"/sites/{site_id}/lists/{list_id}/columns?$select=name,displayName")
    return {c.get("displayName"): c.get("name") for c in page.get("value", [])}


def resolve_names(client, site_id: str, list_id: str) -> dict[str, str]:
    """display → internal name for every registry column.

    Starts from the registry (our chosen names) and overlays what the live
    list reports — so the map is complete even for columns the columns API
    doesn't return (e.g. hidden ones), and still picks up any Graph name
    munging for the columns it does return.
    """
    names = {c["display"]: c["name"] for c in COLUMNS}
    names.update(column_names(client, site_id, list_id))
    return names


def ensure_list(client, site_id: str, sp: SharePointSettings) -> tuple[str, str, dict[str, str]]:
    """Idempotent provisioning. Returns (list_id, web_url, name_for).

    Creates the list bare (the proven operation) then adds any missing
    columns one at a time. Safe to re-run: existing columns are skipped.
    """
    lst = get_list(client, site_id, sp.list_name)
    if lst is None:
        _, lst = client.request(
            "POST", f"/sites/{site_id}/lists",
            {"displayName": sp.list_name, "list": {"template": "genericList"}},
        )
    list_id = lst["id"]

    have = column_names(client, site_id, list_id)
    failures: list[str] = []
    for col in COLUMNS:
        if col["display"] in have:
            continue
        body: dict[str, Any] = {"name": col["name"], "displayName": col["display"], **col["spec"]}
        if col.get("indexed"):
            body["indexed"] = True
        if col.get("hidden"):
            body["hidden"] = True
        try:
            client.request("POST", f"/sites/{site_id}/lists/{list_id}/columns", body)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                # Column already exists (prior run, or a column the columns API
                # doesn't list back). Idempotent — treat as present, skip.
                continue
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            failures.append(f"{col['display']!r} ({col['name']}): HTTP {e.code} — {detail[:300]}")
    name_for = resolve_names(client, site_id, list_id)
    if failures:
        # The columns that succeeded are created (re-running skips them).
        # Surface Graph's own error so the bad column is precisely fixable.
        raise RuntimeError(
            "Some columns could not be created — fix and re-run "
            "`oa sharepoint provision`:\n  - " + "\n  - ".join(failures)
        )
    return list_id, lst.get("webUrl", ""), name_for


def fetch_items(client, site_id: str, list_id: str, pubid_internal: str) -> dict[str, dict]:
    """PubId → item (with id and expanded fields) for rows already on the list."""
    out: dict[str, dict] = {}
    url = f"/sites/{site_id}/lists/{list_id}/items?$expand=fields&$top=200"
    while url:
        _, page = client.request("GET", url)
        for it in page.get("value", []):
            pv = (it.get("fields") or {}).get(pubid_internal)
            if pv is not None:
                out[str(pv)] = it
        url = page.get("@odata.nextLink")
    return out


def push_archives(
    client,
    site_id: str,
    list_id: str,
    sp: SharePointSettings,
    name_for: dict[str, str],
    email_to_lookup: dict[str, str],
    archives: list[dict],
    now: str,
) -> PushResult:
    """Create/patch one row per archive (system-owned columns). Idempotent
    on PubId. Per-row failures are collected as warnings, not fatal."""
    result = PushResult()
    pubid_internal = name_for[D_PUBID]
    existing = fetch_items(client, site_id, list_id, pubid_internal)
    contact_key = name_for[D_CONTACT] + "LookupId"
    corr_key = name_for[D_CORR] + "LookupId"

    for archive in archives:
        pub_id = archive["publication_id"]
        try:
            fields = build_system_fields(archive, sp, name_for, email_to_lookup, now)
        except KeyError as e:
            result.errors.append(f"{pub_id}: column {e} missing from list — re-provision")
            continue
        if contact_key in fields or corr_key in fields:
            result.person_set += 1
        try:
            if pub_id in existing:
                item_id = existing[pub_id]["id"]
                client.request(
                    "PATCH", f"/sites/{site_id}/lists/{list_id}/items/{item_id}/fields", fields,
                )
                result.updated += 1
            else:
                client.request(
                    "POST", f"/sites/{site_id}/lists/{list_id}/items", {"fields": fields},
                )
                result.created += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200] if hasattr(e, "read") else ""
            result.warnings.append(f"{pub_id}: HTTP {e.code} on push ({body})")
    return result


def reconcile_closed_rows(
    client,
    site_id: str,
    list_id: str,
    sp: SharePointSettings,
    name_for: dict[str, str],
    existing: dict[str, dict],
    archive_by_id: dict[str, dict | None],
    now: str,
) -> ReconcileResult:
    """Handle list rows whose archive is no longer OPEN (closed since last sync).

    ``existing`` is PubId → item (from :func:`fetch_items`). ``archive_by_id``
    maps the *non-open* PubIds on the list to their DB archive (or ``None`` if
    no such archive exists). Open archives are handled by :func:`push_archives`;
    rows with no matching archive are left untouched (never delete a row we
    don't recognise).

    Uses the row's own Status field as the state marker, so no extra
    bookkeeping is needed:

    - If the row still shows an open label, relabel it to the closed label
      ("show 'Done' once"). Applies in both ``sync_closed`` modes.
    - If it already shows the closed label and ``sync_closed`` is False, delete
      it ("remove on the next sync"). When ``sync_closed`` is True the row is
      kept (correctly labelled) instead.
    """
    result = ReconcileResult()
    status_col = name_for[D_STATUS]
    synced_col = name_for.get(D_SYNCED)
    for pub_id, item in existing.items():
        arch = archive_by_id.get(pub_id)
        if arch is None or not str(arch.get("status", "")).startswith("CLOSED_"):
            continue
        desired = status_label(arch["status"])
        current = (item.get("fields") or {}).get(status_col)
        try:
            if current != desired:
                body: dict[str, Any] = {status_col: desired}
                if synced_col:
                    body[synced_col] = now
                client.request(
                    "PATCH", f"/sites/{site_id}/lists/{list_id}/items/{item['id']}/fields", body,
                )
                result.relabeled += 1
            elif not sp.sync_closed:
                client.request(
                    "DELETE", f"/sites/{site_id}/lists/{list_id}/items/{item['id']}",
                )
                result.removed += 1
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", errors="replace")[:200] if hasattr(e, "read") else ""
            result.warnings.append(f"{pub_id}: HTTP {e.code} on reconcile ({body_txt})")
    return result


# ── Pull path: user edits → reviewable proposals ─────────────────────

REQUEST_STATUS_PENDING = "Received — pending review"

# Exemption category → the concrete closure task code applied (after the
# operator confirms). "needs_evidence" categories require PID + URL. Maps
# the docs/sharepoint_list_design.md table; "Other" stays operator-routed.
EXEMPTION_ROUTING: dict[str, tuple[str, bool]] = {
    "All data deposited in another archive": ("close_archived_external", True),
    "No data shareable (sensitivity/confidentiality)": ("close_exception", False),
    "No data generated (review/theory/perspective)": ("close_publication_only", False),
    "Collaborative project AND no biomaGUNE data or lead": ("close_exception", False),
    "Other — needs explanation": ("propose_exemption", False),
}


@dataclass
class Proposal:
    """One reviewable action-sheet row derived from a user's list edit."""
    task_code: str
    task_text: str
    pid: str = ""
    url: str = ""
    note: str = ""


@dataclass
class PulledItem:
    pub_id: str
    item_id: str
    new_sig: str
    proposals: list[Proposal] = field(default_factory=list)
    user_notes: str | None = None


def _fval(fields: dict, name: str):
    """Read a field value, unwrapping a hyperlink dict to its URL."""
    v = fields.get(name)
    if isinstance(v, dict):
        return v.get("Url", "")
    return v


def user_signature(fields: dict, name_for: dict[str, str]) -> str:
    """Stable hash of the user-editable fields. The pull emits a proposal
    only when this differs from the stored ``IngestedSig`` — so a 15-min
    poll doesn't re-surface the same edit every cycle."""
    reassign = fields.get(name_for[D_REASSIGN] + "LookupId", fields.get(name_for[D_REASSIGN], ""))
    parts = [
        str(fields.get(name_for[D_PDONE], "") or ""),
        str(fields.get(name_for[D_PEXEMPT], "") or ""),
        str(_fval(fields, name_for[D_EXTPID]) or ""),
        str(_fval(fields, name_for[D_EXTURL]) or ""),
        str(fields.get(name_for[D_DETAIL], "") or ""),
        str(reassign or ""),
        str(fields.get(name_for[D_NOTES], "") or ""),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _is_true(v) -> bool:
    return v in (True, 1, "true", "True", "1")


def pull_proposals(
    items: list[dict],
    name_for: dict[str, str],
    user_details: dict[str, dict] | None = None,
) -> list[PulledItem]:
    """Turn changed user edits into reviewable proposals (pure; no I/O).

    Each item is a fetched list item (``{"id", "fields"}``). Returns only
    items whose user fields changed since their stored ``IngestedSig`` and
    that carry an actionable signal or a note. ``user_details`` (LookupId →
    ``{"name", "email"}``, from ``GraphClient.resolve_user_details``) lets a
    "suggest a new data contact" proposal name the person and pre-fill the
    ``set_data_contact`` command; without it, it falls back to "open the row".
    """
    out: list[PulledItem] = []
    for it in items:
        fields = it.get("fields") or {}
        pub_id = str(fields.get(name_for[D_PUBID], "") or "")
        sig = user_signature(fields, name_for)
        if sig == (fields.get(name_for[D_INGESTED]) or ""):
            continue
        detail = (fields.get(name_for[D_DETAIL]) or "").strip()
        proposals: list[Proposal] = []

        choice = fields.get(name_for[D_PEXEMPT])
        if choice:
            task_code, needs_evidence = EXEMPTION_ROUTING.get(choice, ("propose_exemption", False))
            ext_pid = str(_fval(fields, name_for[D_EXTPID]) or "").strip()
            ext_url = str(_fval(fields, name_for[D_EXTURL]) or "").strip()
            if needs_evidence and not (ext_pid and ext_url):
                proposals.append(Proposal(
                    "propose_exemption", "Review user-proposed exemption",
                    note=(f"Proposed '{choice}' but the External archive PID/URL is missing "
                          f"— ask the user for both before closing. {detail}").strip(),
                ))
            else:
                note = f"User-proposed exemption: {choice}." + (f" {detail}" if detail else "")
                proposals.append(Proposal(
                    task_code, f"Apply exemption — {choice}",
                    pid=ext_pid if needs_evidence else "",
                    url=ext_url if needs_evidence else "",
                    note=note,
                ))

        if _is_true(fields.get(name_for[D_PDONE])):
            proposals.append(Proposal(
                "propose_done", "Verify user 'done' before closing",
                note=("Data contact flagged this as done — verify the data is archived, then "
                      "close via the normal flow." + (f" {detail}" if detail else "")),
            ))

        reassign = fields.get(name_for[D_REASSIGN] + "LookupId") or fields.get(name_for[D_REASSIGN])
        if reassign:
            person = (user_details or {}).get(str(reassign))
            if person and (person.get("email") or person.get("name")):
                pname = person.get("name") or ""
                pemail = person.get("email") or ""
                who = pname or pemail
                # shlex.quote keeps the pre-filled command copy-paste-safe AND
                # uses single quotes (not double), so the TSV writer doesn't
                # escape them into the "" thicket that's awkward to paste.
                cmd = (f"oa action {pub_id} set_data_contact "
                       f"--email {shlex.quote(pemail)} --name {shlex.quote(pname)}")
                note = (f"User suggested {who}"
                        + (f" <{pemail}>" if pemail and pemail != who else "")
                        + f" as the new data contact. Apply with: {cmd}")
            else:
                note = (f"User suggested a new data contact (open the list row to see who, under "
                        f"'{D_REASSIGN}'). Apply with: "
                        f"oa action {pub_id} set_data_contact --email <email> --name <name>")
            proposals.append(Proposal(
                "propose_data_contact", "Apply suggested data contact", note=note,
            ))

        notes_val = (fields.get(name_for[D_NOTES]) or "").strip() or None
        if proposals or notes_val:
            out.append(PulledItem(pub_id, it["id"], sig, proposals, notes_val))
    return out


def write_proposal_feedback(client, site_id: str, list_id: str, name_for: dict[str, str], item: PulledItem) -> None:
    """Mark the row as ingested: stamp IngestedSig (dedup) and, when there
    are actionable proposals, set the user-visible RequestStatus."""
    body: dict[str, Any] = {name_for[D_INGESTED]: item.new_sig}
    if item.proposals:
        body[name_for[D_REQSTATUS]] = REQUEST_STATUS_PENDING
    client.request("PATCH", f"/sites/{site_id}/lists/{list_id}/items/{item.item_id}/fields", body)


def load_settings(cfg: Config) -> SharePointSettings:
    """Return the SharePoint settings, raising a clear error if disabled/unconfigured."""
    sp = cfg.sharepoint
    if not sp.enabled:
        raise RuntimeError("SharePoint sync is disabled (set [sharepoint] enabled = true in config.toml)")
    if not sp.client_id:
        raise RuntimeError("sharepoint.client_id is not set in config.toml")
    return sp
