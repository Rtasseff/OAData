"""Tests for the SharePoint sync module.

No network: GraphClient's I/O is replaced by an in-memory FakeGraph that
mimics the Graph list/column/item endpoints, so push idempotency and
provisioning are exercised end to end. Pure mappers are tested directly.
"""

import io
import urllib.error

import pytest

from oa_tracker import sharepoint as sp_mod
from oa_tracker.config import SharePointSettings, load_config
from oa_tracker.sharepoint import (
    COLUMNS, D_PUBID, D_CONTACT, D_CORR, D_INGESTED, D_REQSTATUS, D_STATUS,
    EXEMPTION_CHOICES, Proposal, PulledItem,
    build_system_fields, data_archiving_label, diff_against_list,
    ensure_list, fetch_items, folder_url, pull_proposals, push_archives,
    reconcile_closed_rows, status_label, user_signature, write_proposal_feedback,
)


def _item(pub_id="3000", item_id="I1", ingested="", **by_internal):
    """Build a fetched list item ({id, fields}) keyed by internal column names."""
    nf = _name_for()
    fields = {nf[D_PUBID]: pub_id}
    if ingested:
        fields[nf[D_INGESTED]] = ingested
    fields.update(by_internal)
    return {"id": item_id, "fields": fields}

SID = "SITE1"


def _name_for():
    return {c["display"]: c["name"] for c in COLUMNS}


def _archive(pub_id="3000", **over):
    base = {
        "publication_id": pub_id,
        "status": "OPEN_ACTIVE",
        "pub_title": f"Title {pub_id}",
        "pub_journal": "J. Test",
        "pub_year": 2025,
        "pub_doi": "10.1/abc",
        "oa_data_required": 1,
        "oa_mandate_missing": 0,
        "max_embargo_months": 6,
        "corresponding_author_name": "Corr Author",
        "corresponding_author_email": "corr@cicbiomagune.es",
        "data_contact_name": "Data Contact",
        "data_contact_email": "dc@cicbiomagune.es",
        "zenodo_code": None,
    }
    base.update(over)
    return base


# ── Fake Graph endpoint ──────────────────────────────────────────────

class FakeGraph:
    """Minimal stateful stand-in for GraphClient.request."""

    def __init__(self, fail_create_pubids=(), conflict_displays=()):
        self.lists = {}          # list_id -> {displayName, webUrl, columns{display:name}, items{iid:item}}
        self._lid = 0
        self._iid = 0
        self.calls = []
        self.fail_create_pubids = set(fail_create_pubids)
        # Column display names that 409 on create (simulating an already-existing
        # column the columns API doesn't list back, e.g. a hidden one).
        self.conflict_displays = set(conflict_displays)

    def request(self, method, path, json_body=None):
        self.calls.append((method, path))
        parts = path.split("?", 1)[0].strip("/").split("/")
        # /sites/{sid}/lists ...
        if parts[:3] == ["sites", SID, "lists"]:
            rest = parts[3:]
            if not rest:
                if method == "GET":
                    return 200, {"value": [
                        {"id": lid, "displayName": l["displayName"], "webUrl": l["webUrl"]}
                        for lid, l in self.lists.items()
                    ]}
                if method == "POST":
                    self._lid += 1
                    lid = f"L{self._lid}"
                    self.lists[lid] = {
                        "displayName": json_body["displayName"],
                        "webUrl": f"https://sp/lists/{lid}",
                        "columns": {}, "items": {},
                    }
                    return 201, {"id": lid, "displayName": json_body["displayName"],
                                 "webUrl": self.lists[lid]["webUrl"]}
            else:
                lid = rest[0]
                lst = self.lists[lid]
                if rest[1:] == ["columns"]:
                    if method == "GET":
                        return 200, {"value": [
                            {"name": n, "displayName": d} for d, n in lst["columns"].items()
                        ]}
                    if method == "POST":
                        if json_body["displayName"] in self.conflict_displays:
                            raise urllib.error.HTTPError(
                                path, 409, "Conflict", {},
                                io.BytesIO(b'{"error":{"code":"nameAlreadyExists"}}'),
                            )
                        lst["columns"][json_body["displayName"]] = json_body["name"]
                        return 201, {"name": json_body["name"], "displayName": json_body["displayName"]}
                if rest[1:] == ["items"]:
                    if method == "GET":
                        return 200, {"value": list(lst["items"].values())}
                    if method == "POST":
                        fields = json_body["fields"]
                        if fields.get("PubId") in self.fail_create_pubids:
                            raise urllib.error.HTTPError(
                                path, 400, "Bad", {}, io.BytesIO(b'{"error":"boom"}')
                            )
                        self._iid += 1
                        iid = f"I{self._iid}"
                        lst["items"][iid] = {"id": iid, "fields": dict(fields)}
                        return 201, lst["items"][iid]
                if len(rest) == 4 and rest[1] == "items" and rest[3] == "fields" and method == "PATCH":
                    iid = rest[2]
                    lst["items"][iid]["fields"].update(json_body)
                    return 200, json_body
                if len(rest) == 3 and rest[1] == "items" and method == "DELETE":
                    lst["items"].pop(rest[2], None)
                    return 204, {}
        raise AssertionError(f"unhandled fake request: {method} {path}")


# ── Pure mappers ─────────────────────────────────────────────────────

def test_status_label_maps_known_and_passes_through_unknown():
    assert status_label("OPEN_INACTIVE") == "Waiting for data"
    assert status_label("OPEN_ZENODO_PUBLISHED") == "Published to Zenodo"
    assert status_label("WEIRD") == "WEIRD"


@pytest.mark.parametrize("archive,expected", [
    ({"oa_data_required": 1, "oa_mandate_missing": 0}, "Required"),
    ({"oa_data_required": 0, "oa_mandate_missing": 0}, "Not required"),
    ({"oa_data_required": None, "oa_mandate_missing": 0}, "Unknown"),
    ({"oa_data_required": 1, "oa_mandate_missing": 1}, "Unknown"),
])
def test_data_archiving_label(archive, expected):
    assert data_archiving_label(archive) == expected


def test_folder_url_template_and_none():
    sp = SharePointSettings(folder_url_template="https://sp/docs/{pub_id}")
    assert folder_url({"publication_id": "42"}, sp) == "https://sp/docs/42"
    assert folder_url({"publication_id": "42"}, SharePointSettings()) is None


def test_build_system_fields_populates_and_resolves_person():
    sp = SharePointSettings(sop_url="https://sp/sop")
    name_for = _name_for()
    a = _archive()
    email_to_lookup = {"dc@cicbiomagune.es": "7", "corr@cicbiomagune.es": "9"}
    f = build_system_fields(a, sp, name_for, email_to_lookup, "2026-06-03T00:00:00")

    assert f["Title"] == "Title 3000"
    assert f["PubId"] == "3000"
    assert f["PipelineStatus"] == "Data uploaded — under review"
    assert f["DataArchiving"] == "Required"
    assert f["EmbargoMonths"] == 6
    assert f["JournalYear"] == "J. Test (2025)"
    assert f["DoiLink"] == "https://doi.org/10.1/abc"   # plain-text URL (proven column type)
    assert f["SopLink"] == "https://sp/sop"
    # Person columns set via <internal>LookupId
    assert f["DataContactLookupId"] == "7"
    assert f["CorrAuthorLookupId"] == "9"


def test_build_system_fields_tolerates_unmapped_and_missing():
    """Unresolved emails leave the Person column unset (text name still set);
    a None embargo / external CA omit those fields rather than send nulls."""
    sp = SharePointSettings()
    name_for = _name_for()
    a = _archive(
        max_embargo_months=None,
        corresponding_author_name=None,
        corresponding_author_email=None,
        data_contact_email="TBD",
    )
    f = build_system_fields(a, sp, name_for, {}, "2026-06-03T00:00:00")
    assert "EmbargoMonths" not in f
    assert "CorrAuthorName" not in f
    assert "DataContactLookupId" not in f   # 'TBD' doesn't resolve
    assert "CorrAuthorLookupId" not in f
    assert f["DataContactName"] == "Data Contact"  # text fallback still present


def test_diff_against_list():
    archives = [_archive("1"), _archive("2"), _archive("3")]
    existing = {"2": {"id": "I2"}, "3": {"id": "I3"}, "9": {"id": "I9"}}
    diff = diff_against_list(archives, existing, SharePointSettings())
    assert diff["would_create"] == ["1"]
    assert diff["would_update"] == ["2", "3"]
    assert diff["would_remove"] == ["9"]   # on list, no longer open
    # sync_closed=True suppresses removals
    diff2 = diff_against_list(archives, existing, SharePointSettings(sync_closed=True))
    assert diff2["would_remove"] == []


# ── Orchestration with the fake client ───────────────────────────────

def test_ensure_list_creates_list_and_all_columns_idempotently():
    g = FakeGraph()
    sp = SharePointSettings(list_name="OA Archive Tracker")
    list_id, web_url, name_for = ensure_list(g, SID, sp)
    assert web_url.startswith("https://sp/")
    # Every registry column got provisioned.
    for col in COLUMNS:
        assert name_for[col["display"]] == col["name"]

    # Re-running creates no duplicate list and no duplicate columns.
    creates_before = sum(1 for m, p in g.calls if m == "POST" and p.endswith("/columns"))
    list_id2, _, name_for2 = ensure_list(g, SID, sp)
    assert list_id2 == list_id
    assert name_for2 == name_for
    creates_after = sum(1 for m, p in g.calls if m == "POST" and p.endswith("/columns"))
    assert creates_after == creates_before  # nothing new created on re-run


def test_ensure_list_treats_existing_column_as_idempotent():
    """A 409 (column already exists, e.g. one the columns API doesn't list
    back) is benign — provisioning still succeeds and name_for still carries
    every registry column (from the registry base)."""
    from oa_tracker.sharepoint import D_INGESTED
    g = FakeGraph(conflict_displays={D_INGESTED})
    _, _, name_for = ensure_list(g, SID, SharePointSettings())
    for col in COLUMNS:
        assert name_for[col["display"]] == col["name"]


def test_push_creates_then_updates_idempotent():
    g = FakeGraph()
    sp = SharePointSettings()
    list_id, _, name_for = ensure_list(g, SID, sp)
    archives = [_archive("100"), _archive("101")]

    r1 = push_archives(g, SID, list_id, sp, name_for, {}, archives, "2026-06-03T00:00:00")
    assert (r1.created, r1.updated) == (2, 0)
    assert len(fetch_items(g, SID, list_id, name_for[D_PUBID])) == 2

    # Second push updates the same rows (matched on PubId), no duplicates.
    r2 = push_archives(g, SID, list_id, sp, name_for, {}, archives, "2026-06-03T01:00:00")
    assert (r2.created, r2.updated) == (0, 2)
    assert len(fetch_items(g, SID, list_id, name_for[D_PUBID])) == 2


def test_push_counts_person_columns_when_resolved():
    g = FakeGraph()
    sp = SharePointSettings()
    list_id, _, name_for = ensure_list(g, SID, sp)
    archives = [_archive("200", data_contact_email="dc@cicbiomagune.es")]
    email_to_lookup = {"dc@cicbiomagune.es": "5"}
    r = push_archives(g, SID, list_id, sp, name_for, email_to_lookup, archives, "now")
    assert r.person_set == 1
    item = next(iter(g.lists[list_id]["items"].values()))
    assert item["fields"][name_for[D_CONTACT] + "LookupId"] == "5"


def test_push_is_resilient_to_a_failing_row():
    g = FakeGraph(fail_create_pubids={"BAD"})
    sp = SharePointSettings()
    list_id, _, name_for = ensure_list(g, SID, sp)
    archives = [_archive("GOOD"), _archive("BAD")]
    r = push_archives(g, SID, list_id, sp, name_for, {}, archives, "now")
    assert r.created == 1                 # GOOD landed
    assert len(r.warnings) == 1           # BAD surfaced as a warning, not fatal
    assert "BAD" in r.warnings[0]


# ── Reconcile closed rows ────────────────────────────────────────────

def test_reconcile_closed_relabels_then_removes_when_sync_closed_false():
    g = FakeGraph()
    sp = SharePointSettings(sync_closed=False)
    list_id, _, name_for = ensure_list(g, SID, sp)
    push_archives(g, SID, list_id, sp, name_for, {}, [_archive("300")], "t0")
    existing = fetch_items(g, SID, list_id, name_for[D_PUBID])
    closed = _archive("300", status="CLOSED_DATA_ARCHIVED")

    # First sync after close: row still shows the open label → relabel to closed.
    r1 = reconcile_closed_rows(g, SID, list_id, sp, name_for, existing, {"300": closed}, "t1")
    assert (r1.relabeled, r1.removed) == (1, 0)
    existing = fetch_items(g, SID, list_id, name_for[D_PUBID])
    assert existing["300"]["fields"][name_for[D_STATUS]] == status_label("CLOSED_DATA_ARCHIVED")

    # Next sync: the row already shows the closed label → remove it.
    r2 = reconcile_closed_rows(g, SID, list_id, sp, name_for, existing, {"300": closed}, "t2")
    assert (r2.relabeled, r2.removed) == (0, 1)
    assert fetch_items(g, SID, list_id, name_for[D_PUBID]) == {}


def test_reconcile_closed_keeps_row_when_sync_closed_true():
    g = FakeGraph()
    sp = SharePointSettings(sync_closed=True)
    list_id, _, name_for = ensure_list(g, SID, sp)
    push_archives(g, SID, list_id, sp, name_for, {}, [_archive("301")], "t0")
    existing = fetch_items(g, SID, list_id, name_for[D_PUBID])
    closed = _archive("301", status="CLOSED_EXCEPTION")

    r1 = reconcile_closed_rows(g, SID, list_id, sp, name_for, existing, {"301": closed}, "t1")
    assert (r1.relabeled, r1.removed) == (1, 0)
    existing = fetch_items(g, SID, list_id, name_for[D_PUBID])
    # Already labelled and sync_closed=true → kept, never deleted.
    r2 = reconcile_closed_rows(g, SID, list_id, sp, name_for, existing, {"301": closed}, "t2")
    assert (r2.relabeled, r2.removed) == (0, 0)
    assert "301" in fetch_items(g, SID, list_id, name_for[D_PUBID])


def test_reconcile_leaves_unknown_rows_untouched():
    g = FakeGraph()
    sp = SharePointSettings(sync_closed=False)
    list_id, _, name_for = ensure_list(g, SID, sp)
    push_archives(g, SID, list_id, sp, name_for, {}, [_archive("302")], "t0")
    existing = fetch_items(g, SID, list_id, name_for[D_PUBID])
    # No matching archive (e.g. a human-made row) → never deleted or relabelled.
    r = reconcile_closed_rows(g, SID, list_id, sp, name_for, existing, {"302": None}, "t1")
    assert (r.relabeled, r.removed) == (0, 0)
    assert "302" in fetch_items(g, SID, list_id, name_for[D_PUBID])


# ── Config wiring ────────────────────────────────────────────────────

def test_load_config_parses_sharepoint_section(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[sharepoint]\n"
        "enabled = true\n"
        'client_id = "abc-123"\n'
        'list_name = "My List"\n'
        'token_cache = "~/.oa_tok.json"\n'
    )
    cfg = load_config(config_path=tmp_path / "config.toml", project_root=tmp_path)
    assert cfg.sharepoint.enabled is True
    assert cfg.sharepoint.client_id == "abc-123"
    assert cfg.sharepoint.list_name == "My List"
    # token_cache is expanded (no literal ~ left)
    assert "~" not in str(cfg.sharepoint.token_cache)


def test_load_config_defaults_sharepoint_disabled(tmp_path):
    (tmp_path / "config.toml").write_text("[paths]\ndatabase = \"./x.sqlite\"\n")
    cfg = load_config(config_path=tmp_path / "config.toml", project_root=tmp_path)
    assert cfg.sharepoint.enabled is False


# ── Pull path: user edits → proposals ────────────────────────────────

def _codes(pulled_item):
    return [p.task_code for p in pulled_item.proposals]


def test_pull_done_emits_propose_done():
    nf = _name_for()
    pulled = pull_proposals([_item(ProposedDone=True)], nf)
    assert len(pulled) == 1
    assert _codes(pulled[0]) == ["propose_done"]


def test_pull_exemption_no_data_generated_maps_to_publication_only():
    nf = _name_for()
    item = _item(ProposedExemption="No data generated (review/theory/perspective)")
    assert _codes(pull_proposals([item], nf)[0]) == ["close_publication_only"]


def test_pull_exemption_sensitivity_and_collaborative_map_to_exception():
    nf = _name_for()
    for choice in ("No data shareable (sensitivity/confidentiality)",
                   "Collaborative project AND no biomaGUNE data or lead"):
        assert _codes(pull_proposals([_item(ProposedExemption=choice)], nf)[0]) == ["close_exception"]


def test_pull_archived_elsewhere_with_evidence_maps_to_close_archived_external():
    nf = _name_for()
    item = _item(
        ProposedExemption="All data deposited in another archive",
        ExtArchivePid="10.5061/dryad.x",
        ExtArchiveUrl={"Url": "https://datadryad.org/x"},   # hyperlink shape
    )
    prop = pull_proposals([item], nf)[0].proposals[0]
    assert prop.task_code == "close_archived_external"
    assert prop.pid == "10.5061/dryad.x"
    assert prop.url == "https://datadryad.org/x"


def test_pull_archived_elsewhere_missing_evidence_falls_back_to_propose():
    nf = _name_for()
    item = _item(ProposedExemption="All data deposited in another archive")
    prop = pull_proposals([item], nf)[0].proposals[0]
    assert prop.task_code == "propose_exemption"   # not closed without evidence
    assert prop.pid == "" and prop.url == ""
    assert "missing" in prop.note.lower()


def test_pull_reassign_emits_propose_data_contact_with_cli_hint():
    nf = _name_for()
    prop = pull_proposals([_item(ProposedDataContactLookupId="7")], nf)[0].proposals[0]
    assert prop.task_code == "propose_data_contact"
    assert "set_data_contact" in prop.note
    # Without user_details we can't name the person — fall back to "open the row".
    assert "open the list row" in prop.note


def test_pull_reassign_names_person_and_prefills_command_with_details():
    nf = _name_for()
    details = {"7": {"name": "Jane García", "email": "jgarcia@cicbiomagune.es"}}
    prop = pull_proposals([_item(ProposedDataContactLookupId="7")], nf, details)[0].proposals[0]
    assert prop.task_code == "propose_data_contact"
    assert "Jane García" in prop.note
    assert "jgarcia@cicbiomagune.es" in prop.note
    # shell-quoted with single quotes (so the TSV writer doesn't double them)
    assert "set_data_contact --email jgarcia@cicbiomagune.es --name 'Jane García'" in prop.note
    # and no double-quotes that would get CSV-escaped in the proposals file
    assert '"' not in prop.note


def test_pull_reassign_unmapped_lookupid_falls_back():
    """A picked person who hasn't signed into the site stays unmapped."""
    nf = _name_for()
    details = {"7": {"name": "Jane García", "email": "jgarcia@cicbiomagune.es"}}
    prop = pull_proposals([_item(ProposedDataContactLookupId="999")], nf, details)[0].proposals[0]
    assert "open the list row" in prop.note


def test_pull_dedup_skips_unchanged_signature():
    nf = _name_for()
    base = _item(ProposedDone=True)
    base["fields"][nf[D_INGESTED]] = user_signature(base["fields"], nf)
    assert pull_proposals([base], nf) == []


def test_pull_empty_item_emits_nothing():
    assert pull_proposals([_item()], _name_for()) == []


def test_pull_notes_only_pulled_without_proposals():
    nf = _name_for()
    pulled = pull_proposals([_item(UserNotes="please call me")], nf)
    assert len(pulled) == 1
    assert pulled[0].proposals == []
    assert pulled[0].user_notes == "please call me"


def test_user_signature_changes_with_edits():
    nf = _name_for()
    assert user_signature(_item()["fields"], nf) != user_signature(_item(ProposedDone=True)["fields"], nf)


def test_write_proposal_feedback_sets_status_and_sig_when_actionable():
    g = FakeGraph()
    lid, _, name_for = ensure_list(g, SID, SharePointSettings())
    g.lists[lid]["items"]["I1"] = {"id": "I1", "fields": {}}
    item = PulledItem("3000", "I1", "abc123", proposals=[Proposal("propose_done", "t")])
    write_proposal_feedback(g, SID, lid, name_for, item)
    f = g.lists[lid]["items"]["I1"]["fields"]
    assert f[name_for[D_INGESTED]] == "abc123"
    assert f[name_for[D_REQSTATUS]] == sp_mod.REQUEST_STATUS_PENDING


def test_write_proposal_feedback_notes_only_stamps_sig_only():
    g = FakeGraph()
    lid, _, name_for = ensure_list(g, SID, SharePointSettings())
    g.lists[lid]["items"]["I1"] = {"id": "I1", "fields": {}}
    item = PulledItem("3000", "I1", "zzz", proposals=[], user_notes="note")
    write_proposal_feedback(g, SID, lid, name_for, item)
    f = g.lists[lid]["items"]["I1"]["fields"]
    assert f[name_for[D_INGESTED]] == "zzz"
    assert name_for[D_REQSTATUS] not in f


# ── Seam: a pulled proposal applies through oa apply ─────────────────

def _write_proposal_tsv(path, pulled_item, done="1"):
    """Build the proposals TSV exactly as `oa sharepoint sync` does."""
    import csv
    from oa_tracker.sheet import SHEET_COLUMNS
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
        w.writeheader()
        for prop in pulled_item.proposals:
            row = {c: "" for c in SHEET_COLUMNS}
            row.update({
                "publication_id": pulled_item.pub_id, "task_code": prop.task_code,
                "task_text": prop.task_text, "done": done,
                "pid": prop.pid, "url": prop.url, "note": prop.note,
            })
            w.writerow(row)
    return path


def test_pulled_exemption_closes_archive_via_apply(test_config):
    from oa_tracker.actions import apply_actions
    from oa_tracker.db import get_archive, get_connection, upsert_archive

    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="3000", folder_path="/t/3000",
                       first_seen_at="2026-01-01T00:00:00",
                       last_seen_at="2026-01-01T00:00:00", status="OPEN_INACTIVE")

    item = _item(pub_id="3000", ProposedExemption="No data generated (review/theory/perspective)")
    pulled = pull_proposals([item], _name_for())[0]
    sheet = _write_proposal_tsv(test_config.output_dir / "sharepoint_proposals.tsv", pulled)

    res = apply_actions(sheet, test_config)
    assert res.applied == 1 and res.errors == []
    with get_connection(test_config.database) as conn:
        assert get_archive(conn, "3000")["status"] == "CLOSED_PUBLICATION_ONLY"


def test_pulled_archived_elsewhere_closes_data_archived_via_apply(test_config):
    from oa_tracker.actions import apply_actions
    from oa_tracker.db import get_archive, get_connection, upsert_archive

    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="3001", folder_path="/t/3001",
                       first_seen_at="2026-01-01T00:00:00",
                       last_seen_at="2026-01-01T00:00:00", status="OPEN_ACTIVE")

    item = _item(
        pub_id="3001", ProposedExemption="All data deposited in another archive",
        ExtArchivePid="10.5061/dryad.x", ExtArchiveUrl={"Url": "https://datadryad.org/x"},
    )
    pulled = pull_proposals([item], _name_for())[0]
    sheet = _write_proposal_tsv(test_config.output_dir / "sharepoint_proposals.tsv", pulled)

    res = apply_actions(sheet, test_config)
    assert res.applied == 1 and res.errors == []
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "3001")
        assert a["status"] == "CLOSED_DATA_ARCHIVED"
        assert a["final_pid"] == "10.5061/dryad.x"
