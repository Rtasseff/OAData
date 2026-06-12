# SharePoint List — design (parallel track)

This is the design reference for the user-facing SharePoint List
interaction layer described in
[roadmap.md § Parallel track](roadmap.md). It is sequenced step 3
("list schema design") of that track. Auth is solved and write is
confirmed on the live `PublicationsData` site (see the roadmap progress
log, 2026-06-02); this doc locks **what the list looks like** so the
sync module (`src/oa_tracker/sharepoint.py`, steps 4–6) can be built
against a fixed schema.

The companion design for the Zenodo work is
[zenodo_design.md](zenodo_design.md); this doc follows the same shape
(schema → mapping → module → config → decisions log).

## Purpose

Give corresponding authors and data contacts a single browser page that
shows the publications biomaGUNE is processing, scoped to *them*, with a
few structured ways to signal back to the tracker (propose exemption,
"I think this is done", reassign the data contact). The OA CLI is the
sync engine; users install nothing and never see Microsoft Graph.

Two hard rules carried in from the project:

- **SQLite is the system of record.** The list is a projection of it,
  re-derivable on every sync. Nothing on the list is authoritative.
- **No auto state changes for a new input source.** Every user edit
  surfaces as an action-sheet row for operator review first; classes of
  signal graduate to auto-apply only after they've behaved correctly
  across real cycles (see `feedback_no_auto_state_changes.md`).

**Why corresponding author, not PI.** In the publication context the
responsible party is, by tradition, the *corresponding author* — often
the PI but not always. Internal docs sometimes name the PI as ultimately
responsible, but we deliberately model the corresponding author because
(a) it fits how papers actually work, and (b) it is already derived and
cached from the central DB (`publi_corr_auth → center_user`, see
`mandate_classification.md`), so it needs **no new enrichment**. PI /
group is intentionally not modeled.

## Sync model (recap, so the schema choices make sense)

`oa sharepoint sync` runs on a ~15-minute schedule (polling, not push —
rationale in the roadmap). Each run:

1. **Push** (outbound): for every live archive, overwrite the
   *system-owned* columns from SQLite. Full overwrite — operator/SQLite
   always wins on these.
2. **Pull** (inbound): read the *user-editable* columns; for any that
   changed since we last ingested them, emit an action-sheet row. Never
   overwrite a user column from our side except to update the read-only
   "request status" feedback column.

This split — system-owned vs user-editable — is the backbone of the
column design below.

## The list

One regular SharePoint List on the existing `PublicationsData` site,
named per config (`OA Archive Tracker`). One item per **open** archive
(closed-archive handling is in *List size* below). Columns fall into
three groups.

Internal names (the `name` we set when provisioning the column via
Graph) have no spaces; display names are what the user sees. We set both
explicitly to keep the sync code stable.

### Group A — system-owned (synced out of SQLite; users cannot edit)

| Display name | Internal name | SP column type | Source in `archives` / derivation |
|---|---|---|---|
| Title | `Title` (built-in) | Single line of text | `pub_title`; fallback `Publication {publication_id}` |
| Publication ID | `PubId` | Single line of text (**indexed**, item match key) | `publication_id` |
| Status | `Status` | Choice (single) | `status` → friendly label (mapping table below) |
| Data archiving | `DataArchiving` | Choice (single): Required / Not required / Unknown | `oa_data_required` (1/0/NULL) + `oa_mandate_missing` |
| Embargo (months) | `EmbargoMonths` | Number | `max_embargo_months` (blank when null) |
| Corresponding author | `CorrAuthor` | Person | best-effort resolve of the *effective* corresponding-author email to a tenant user; drives the CA `[Me]` view; blank when external/unknown |
| Corresponding author (name) | `CorrAuthorName` | Single line of text | effective `corresponding_author_name` (always shown when known; blank for external/unknown CA) |
| Data contact | `DataContact` | Person | best-effort resolve of `data_contact_email`; drives the data-contact `[Me]` view; blank when unresolved |
| Data contact (name) | `DataContactName` | Single line of text | `data_contact_name` (always populated — the unmapped-tolerant fallback) |
| Folder | `FolderLink` | Hyperlink | `folder_path` → SharePoint web URL |
| DOI | `Doi` | Hyperlink | `pub_doi` → `https://doi.org/{doi}` (blank when null) |
| Journal / year | `JournalYear` | Single line of text | `"{pub_journal} ({pub_year})"` |
| Zenodo record | `ZenodoLink` | Hyperlink | `zenodo_code` → `https://zenodo.org/records/{code}` (blank when null) |
| SOP | `SopLink` | Hyperlink | constant from config (`sharepoint.sop_url`) |
| Last updated | `LastSynced` | Date and time | sync run timestamp |

### Group B — user-editable (filled in the SharePoint UI; pulled back)

| Display name | Internal name | SP column type | Drives action-sheet code |
|---|---|---|---|
| I think this is done | `ProposedDone` | Yes/No (default No) | `propose_done` |
| Propose exemption | `ProposedExemption` | Choice (single, closed list — categories below) | `propose_exemption` |
| External archive PID | `ExtArchivePid` | Single line of text | part of `propose_exemption` — **required** when the exemption is "archived elsewhere" |
| External archive URL | `ExtArchiveUrl` | Hyperlink | part of `propose_exemption` — **required** when the exemption is "archived elsewhere" |
| Exemption / done detail | `ProposalDetail` | Multiple lines of text (plain) | free text — **never** auto-applies; surfaced to operator only |
| Suggest a new data contact | `ProposedDataContact` | Person | `propose_data_contact` |
| Notes | `UserNotes` | Multiple lines of text (plain) | `user_note` — awareness-only (no status change); recorded to the archive's notes on apply so it isn't lost on an unattended sync |

`ExtArchivePid` / `ExtArchiveUrl` are only meaningful when the exemption
category is "archived elsewhere"; SharePoint can hint this with a
conditional-formatting rule on the form, but the real enforcement is at
apply time (the operator/auto-apply rejects that category without a
PID + URL). All other categories ignore these two fields.

### Group C — system-internal (sync bookkeeping; hidden from default views)

| Display name | Internal name | SP column type | Purpose |
|---|---|---|---|
| Request status | `RequestStatus` | Single line of text (read-only to users via column setting) | feedback the sync writes back: `Received — pending review` / `Applied` / `Declined: <reason>` so the user sees their signal landed |
| Ingested signature | `IngestedSig` | Single line of text (hidden) | hash/snapshot of Group-B values at last ingest; the pull step emits a new action row only when current Group-B differs from this (de-dupes the 15-min poll) |

`IngestedSig` is hidden from all views; `RequestStatus` shows in the
item detail form but is not user-editable. Keeping the dedup state on
the list item (not in SQLite) means it survives a SQLite rebuild and
keeps the proposal lifecycle co-located with the row the user sees.

## Status label mapping

The list shows friendly labels, never the raw codes. The mapping is
1:1 and small; the sync module owns it. Pull never reads `Status`
(system-owned), so there is no reverse-mapping fragility.

| `status` code | List label |
|---|---|
| `OPEN_INACTIVE` | Waiting for data |
| `OPEN_ACTIVE` | Data uploaded — under review |
| `OPEN_READY_FOR_ZENODO_DRAFT` | Ready to archive |
| `OPEN_ZENODO_DRAFT_CREATED` | Archive draft created |
| `OPEN_ZENODO_DRAFT_VALIDATED` | Archive draft validated |
| `OPEN_ZENODO_PUBLISHED` | Published to Zenodo |
| `OPEN_DB_UPDATED` | Recorded in publication DB |
| `CLOSED_DATA_ARCHIVED` | Done — data archived |
| `CLOSED_PUBLICATION_ONLY` | Closed — no data required |
| `CLOSED_EXCEPTION` | Closed — exception |

The `Status` Choice column is provisioned with exactly these ten labels.

## Identity resolution (the Person columns)

This is the highest-risk part of the design and the one most likely to
need an empirical pass before it's trusted (see *Empirical unknowns*).
There are **two** people per row — corresponding author and data
contact — and both resolve the same way.

**Join key is the institutional email, not a name match.** Per
`mandate_classification.md`, contact identity comes from `center_user`
(NOT `mdm_personal` — that table's `id`-space is unstable and was the
source of months of wrong-name bugs). The corresponding-author and
data-contact emails are both derived as
`center_user.username + "@cicbiomagune.es"` and cached on the archive.
That email is the natural key to map a person to an Entra/SharePoint
user.

**Tolerate-unmapped is a first-class requirement.** For each person we
**always** populate the plain-text name column (`CorrAuthorName`,
`DataContactName`) and treat the `Person` column as best-effort:

- Resolves → the Person column is set; the row appears in that person's
  `[Me]` view. Self-service works.
- Doesn't resolve → Person column blank; the name column still shows who
  it is; the row simply won't surface in that `[Me]` view until the
  mapping is fixed. The sync logs how many rows are unmapped.

### Corresponding author, and the "effective" override

The corresponding author comes from the DB cache
(`corresponding_author_name/email`). But **many papers have a
non-biomaGUNE corresponding author**, in which case the DB field is
blank (the lookup returns nothing for `id_user ∈ {-1, 0}`, people not in
`center_user`, or departed staff). For those rows:

- `CorrAuthor` (Person) and `CorrAuthorName` are blank → the row is
  **orphaned**: it appears in *no* corresponding-author view, and shows
  up in a data-contact view only once a data contact is manually set.
  This is accepted v1 behavior.
- Automatically inferring "the biomaGUNE PI from the author list" (or
  the PI of a student co-author) is fuzzy — the author list is WoS free
  text — and is **out of scope for v1**.

Instead, the operator can set an **effective corresponding author**: a
CLI override (`set_corresponding_author`, mirroring the existing
`set_data_contact`) that writes `corresponding_author_name/email` and
sets a new `corresponding_author_overridden = 1` flag so scans don't
clobber it. This lets the operator point an orphaned paper at the right
biomaGUNE person so it surfaces in their CA view, and they can then
reassign the data contact from there. `reset_corresponding_author`
clears the flag and lets the next scan re-seed from the DB.

Because `data_contact_name` already seeds from
`corresponding_author_name`, setting an effective CA on an orphaned row
also gives it a sensible default data contact on the next scan.

### Filters are UX, not security

SharePoint view filters (`[Me]`) scope what a person *sees by default*,
but they do **not** restrict who can edit a row: any authenticated site
user can, in principle, build a view that shows all rows and edit the
user-editable columns on any of them. We do not impose item-level
permissions (they are heavy and would fight the sync). The "the
corresponding author reassigns the data contact" workflow is therefore
an *expectation*, not an enforced rule — the **operator-review gate**
(action-sheet first) is the actual control. Nothing a user types changes
state until the operator applies it.

## Views and zero-click access

The adoption budget is one click past a bookmark. SharePoint's `[Me]`
filter token resolves per-viewer at render time, so one view serves
everyone. Each view filters on a single Person column (no OR-across-
columns needed):

- **Default view — "Papers I'm the data contact for."** Filter
  `DataContact = [Me]`. Default sort by `Status`. Columns: Title,
  Status, Data archiving, Embargo, Folder, plus the Group-B inputs. The
  bookmark is the bare list URL — opening it lands the viewer on their
  own rows. Zero clicks.
- **Second view — "Papers I'm corresponding author for."** Filter
  `CorrAuthor = [Me]`. Typically a superset of the data-contact view
  (the CA starts as the default data contact and can reassign some
  away). This is where a CA reassigns the data contact.
- **One-line list description** (replaces an SOP):
  *"These are publications where you are the corresponding author or the
  data contact. Open a row to send us a signal — propose an exemption,
  reassign the data contact, or tell us you think it's done."*
- **Operator view — "All open."** No `[Me]` filter; used from the
  workstation. Not advertised.
- **Every view filters out CLOSED_\* statuses** to stay under the list
  view threshold (see *List size*).

Email deep links: reminder/completion templates link straight to the
user's row. Preferred form is the item display form using the stored
SharePoint item id (`.../Lists/<List>/DispForm.aspx?ID=<itemId>`), which
requires caching the item id back into SQLite (schema addition below).
Fallback that needs no item id:
`?FilterField1=PubId&FilterValue1=<publication_id>` against a view.

### Keep the displayed surface dead-simple

Real users won't read, and a long form reads as noise. Two rules keep the
visible surface minimal even though the data model carries fallback columns:

- **The Person columns are plumbing, not display.** `DataContact` and
  `CorrAuthor` (and the user's `ProposedDataContact`) exist only to power
  the `[Me]` filter — a view can filter on a column without *showing* it.
  So **hide the system Person columns from every displayed view** and show
  only the plain-text `…(name)` columns. Each identity then appears once
  (one readable name), not as a chip *and* a name. The `[Me]` filter still
  works because it reads the hidden Person column.
- **The edit form shows only what a user can act on.** Configure the list's
  edit form (browser: open an item → *Edit form* → *Edit columns*) to hide
  every system column and show only the user-editable fields, ideally with
  a one-line section header. The short list a user should ever see:
  *I think this is done*, *Propose exemption* (+ *External archive PID/URL*,
  *Exemption / done detail*), *Suggest a new data contact*, *Notes*.

The single place a user changes the data contact is the **"Suggest a new
data contact"** people-picker (internal `ProposedDataContact`). It is named
as an action, not a noun, precisely so it's unambiguous; everything else
about contacts is read-only system state they don't touch.

## One-time GUI setup (do once; persists across every sync)

`provision` creates columns and `sync` fills rows, but the view/form/link
polish is browser-only (Graph can't create views, hyperlink columns, or
navigation). None of it is touched by `provision`/`sync`, so configure once.

Display names referenced below are the ones shown in the UI. Note the two
columns per person: the **person/chip** column (`Data contact`,
`Corresponding author`) is plumbing for the `[Me]` filter; the **`(name)`**
text column is what humans read.

**1. Two personal views** — Gear ⚙ → *List settings* → *Views* → *Create
view* → *Standard View*. The create-view page sets displayed columns,
filter, and sort together.

- *"My data-contact papers"* (tick **Make this the default view**):
  - Filter: *Show items only when* `Data contact` **is equal to** `[Me]`
    (type `[Me]` literally — works even though the column is hidden from
    the table).
  - Show: Title, Status, Data archiving, Embargo (months), Corresponding
    author (name), Folder, DOI, I think this is done, Propose exemption,
    Suggest a new data contact, Request status, Notes. Uncheck everything
    else (the person/chip columns, Ingested signature, the duplicate name
    column, etc.). Sort by Status.
- *"Papers I'm corresponding author for"* (not default):
  - Filter: `Corresponding author` **is equal to** `[Me]`.
  - Same columns, but show **Data contact (name)** instead (so the CA sees
    who's handling it).
- Keep the original **All Items** view for yourself (operator) — it's
  unfiltered, so it doesn't go empty like the `[Me]` views do for non-contacts.

**2. Trim the item form** — open any row → *Edit* → *Edit form* (top of the
panel) → toggle **off** every system column, leaving only: I think this is
done, Propose exemption, External archive PID, External archive URL,
Exemption / done detail, Suggest a new data contact, Notes.

**3. Clickable links** — column header ▾ → *Column settings* → *Format this
column* → *Advanced mode* → paste.

**Folder** — dual purpose: the clickable text is the folder name (= the
publication ID, via the `[$PubId]` field reference) and it still links to the
folder, so the column carries information instead of repeating "Open folder".
`[$PubId]` uses the *internal* name of the Publication ID column (ours is
`PubId`); if the text comes up blank, check that column's settings-page URL
(`…Field=<InternalName>`) and swap it in. Swap `[$PubId]` for `[$Title]` to
show the article title instead.

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/sp/v2/column-formatting.schema.json",
  "elmType": "a",
  "txtContent": "[$PubId]",
  "attributes": { "href": "@currentField", "target": "_blank" },
  "style": { "display": "=if(@currentField == '', 'none', 'inline')" }
}
```

**DOI** and **Zenodo record** — there's no useful per-row label, so keep a
static word; paste this and change `"DOI"` to `"Zenodo"` for that column:

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/sp/v2/column-formatting.schema.json",
  "elmType": "a",
  "txtContent": "=if(@currentField == '', '', 'DOI')",
  "attributes": { "href": "@currentField", "target": "_blank" }
}
```

**4. Pin to the sidebar** — Gear ⚙ → *List settings* → *List name,
description and navigation* → *Display this list on the Quick Launch?* →
**Yes**.

**5. (Optional) One-line list description** at the top of the list, e.g.
*"Publications where you're the corresponding author or data contact. Open a
row to send us a signal — propose an exemption, suggest a different data
contact, or tell us you think it's done."*

## What does NOT go on the list

Default posture: only fields a non-operator would want or could act on.
Explicitly **excluded** (kept in SQLite, never pushed):

- Reminder machinery: `reminder_count`, `last_notified_at`,
  `next_reminder_at`.
- Mandate-derivation diagnostics: `oa_mandate_source` (audit trace) and
  the raw `oa_paper_required` / `oa_mandate_missing` internals beyond the
  single user-facing "Data archiving" summary.
- Operator-internal `notes`.
- A standalone email column (the Person/name columns already convey who
  to contact).
- PI / group identity — intentionally not modeled (see *Purpose*).
- Anything from the central publication DB beyond title/DOI/journal/year
  that a researcher wouldn't act on.

## Action routing — the `propose_*` task codes

All inbound signals start **operator-confirmed** (action-sheet row,
`done=1` to apply); promotion order is at the bottom.

| Task code | Emitted when (pull) | Apply effect (after operator `done=1`) |
|---|---|---|
| `propose_data_contact` | `ProposedDataContact` set and differs from `IngestedSig` | resolve the proposed person to name+email, set `data_contact_*`, mark `data_contact_overridden=1` (same effect as `set_data_contact`). No status change. |
| `propose_exemption` | `ProposedExemption` Choice set | routes by category to a closure (table below), carrying the category in the note; for "archived elsewhere" it carries `ExtArchivePid`/`ExtArchiveUrl` and applies as `close_archived_external`. |
| `propose_done` | `ProposedDone = Yes` | acknowledgment/investigation row (like `mandate_missing`): it does **not** close the archive. It tells the operator "the data contact believes this is done — verify, then run the real closure." Closure stays the operator's deliberate step. |

### Exemption categories → closure (locked 2026-06-02)

The closed list for `ProposedExemption`, with the closure each maps to
when an operator approves. "Other / needs explanation" is always present
and **never** auto-applies.

| Category (user-facing label) | Closes as | Notes |
|---|---|---|
| All data deposited in another archive | **`CLOSED_DATA_ARCHIVED`** via `close_archived_external` | requires `ExtArchivePid` + `ExtArchiveUrl`; the external PID becomes `final_pid`, the URL `final_url`. Counts as a completed archive. |
| No data shareable (sensitivity/confidentiality) | `CLOSED_EXCEPTION` | operator-reviewed |
| No data generated (review/theory/perspective) | `CLOSED_PUBLICATION_ONLY` | no data archiving applicable |
| Collaborative project AND no biomaGUNE data or lead | `CLOSED_EXCEPTION` | conjunction is deliberate — wording must not let everyone on a collaboration self-exempt; operator-reviewed regardless |
| Other — needs explanation (free text in `ProposalDetail`) | none (operator-routed) | never auto-applies |

### New task codes to add in `status.py`

- `propose_data_contact` — `changes_status: False`; handled like the
  `set_data_contact` override in `actions.py`.
- `propose_exemption` — `changes_status: True`; reuses existing
  closures (`close_publication_only` / `close_exception`) or the new
  `close_archived_external`, selected by category.
- `propose_done` — `changes_status: False`; acknowledgment-only like
  `mandate_missing`.
- `user_note` — `changes_status: False`; awareness-only. Emitted for a
  free-text `UserNotes` entry so the note is **durable** (a row in the
  proposals file, not just stdout that a scheduled sync would lose) and is
  recorded to the archive's notes on apply.
- `close_archived_external` — `changes_status: True`, `requires_pid:
  True` (mirrors `zenodo_published`); a new **wildcard** transition
  (any OPEN → `CLOSED_DATA_ARCHIVED`) added to `_WILDCARD_TASKS`-style
  handling, validated to require PID + URL.
- `set_corresponding_author` / `reset_corresponding_author` — CLI-only
  overrides (not emitted on the sheet), mirroring
  `set_data_contact` / `reset_data_contact`; write
  `corresponding_author_*` and toggle `corresponding_author_overridden`.

After an action row is emitted, the sync writes `RequestStatus =
"Received — pending review"` on the item and stores the current Group-B
values into `IngestedSig`. When the operator applies (or declines), the
next sync updates `RequestStatus` to `Applied` / `Declined: <reason>`.

### Promotion order (action-sheet-routed → auto-apply)

1. `propose_data_contact` — unambiguous, easily reversed. First.
2. "Archived elsewhere" exemption (`close_archived_external`) — a real,
   verifiable PID/URL is the strongest evidence we get; with a PID
   heuristic check it's a strong auto-apply candidate.
3. Other `propose_exemption` categories (closed list only) — the closed
   list forces a recognized bucket. "Other" never promotes.
4. `propose_done` — last, if ever. Closure is the most irreversible
   step; operator verification has the highest safety value here.

Free-text `UserNotes` and `ProposalDetail` never auto-apply. `ProposalDetail`
rides along inside the related proposal's note; `UserNotes` is emitted as a
durable `user_note` row (recorded to the archive's notes on apply) so it isn't
lost on an unattended sync.

## SQLite schema additions (v2 → v3)

Bump `_SCHEMA_VERSION` to 3 in `db.py` and add:

```sql
ALTER TABLE archives ADD COLUMN sharepoint_item_id           INTEGER;  -- SP list item ID for this archive
ALTER TABLE archives ADD COLUMN sharepoint_synced_at         TEXT;     -- last successful push timestamp
ALTER TABLE archives ADD COLUMN corresponding_author_overridden INTEGER NOT NULL DEFAULT 0;  -- effective-CA override
```

`corresponding_author_overridden` lets `set_corresponding_author` pin an
effective CA that scans won't overwrite — the same pattern as
`data_contact_overridden`. The pub-DB enrichment (`pub_db.enrich_archive`
/ the scanner) must learn to skip refreshing `corresponding_author_*`
when this flag is 1, exactly as it already does for the data contact.

Proposal de-dup (`IngestedSig`) and user feedback (`RequestStatus`) live
on the list item, not in SQLite — no columns needed for them. The
`propose_*` rows still flow through `apply_actions`, so they land in the
existing `events` audit log for free.

## config.toml additions

```toml
[sharepoint]
enabled    = true
tenant     = "biomagune.onmicrosoft.com"
client_id  = "fbd87ebd-fd7e-4e4c-967a-9932bde32da1"   # OA Archive Tracker Entra app (verified 2026-06-02)
site       = "biomagune.sharepoint.com:/sites/PublicationsData"
list_name  = "OA Archive Tracker"
sop_url    = "https://biomagune.sharepoint.com/sites/PublicationsData/..."  # SOP link shown in the SOP column
sync_closed = false   # keep CLOSED_* archives off the list (stay under the view threshold)
token_cache = "~/.oa_sharepoint_token.json"   # MSAL refresh-token cache, OUTSIDE the repo (matches ~/.my.cnf, ~/.zenodorc)
```

The `client_id` was deliberately held out of `config.toml` until now
(per the roadmap) so the SharePoint config schema gets designed in one
pass — this is that pass. It moves into config when `sharepoint.py` is
built. No secret goes in config: device-code flow uses the operator's
own identity; only the refresh-token cache (in `~/`) is sensitive, and
it never enters the repo.

## Module layout — `src/oa_tracker/sharepoint.py`

Mirrors `zenodo.py`: a thin Graph client + pure mapping functions, with
DB orchestration in `actions.py` / `cli.py`. The module knows Graph and
SharePoint, not SQLite.

```python
# Config / client
def load_config(cfg: Config) -> SharePointConfig
class GraphClient(tenant, client_id, token_cache, timeout=...)
    def request(method, path, **kwargs) -> dict      # device-code auth + refresh, retry on 429/5xx
    def resolve_user(email) -> int | None             # email -> site UserInfo LookupId (for Person fields)
# Provisioning (idempotent; safe to re-run)
def ensure_list(client, site_id, cfg) -> str               # returns list_id; creates list + columns + views if absent
# Push (system-owned)
def build_item_fields(archive: dict, cfg) -> dict          # pure: SQLite row -> SP `fields` dict (Group A)
def push_archives(client, site_id, list_id, archives) -> PushResult   # create/patch, idempotent on PubId
# Pull (user-editable)
def fetch_items(client, site_id, list_id) -> list[dict]
def diff_proposals(items) -> list[Proposal]                # Group-B changed vs IngestedSig -> proposals
def write_request_status(client, ..., item_id, text) -> None
```

Build order follows roadmap steps 4–6: **(4)** read-only — pull the list
into a scratch JSON and diff against SQLite, write nothing; **(5)** push
system-owned columns, idempotent; **(6)** pull user edits → emit
`propose_*` action-sheet rows for `oa apply`.

`msal` is already in `.venv` (the spike). Promote it (and any HTTP helper
shared with `zenodo.py`) to `pyproject.toml` when this module lands.

## List size and closed archives

SharePoint list views have a **5000-item threshold** by default. With
one item per open archive we are far below it. Policy: **only OPEN_\*
archives live on the list** (`sync_closed = false`). When an archive
closes, the next sync sets its final `Status` label, lets the closure
show for confirmation, and removes the item on the following sync (SQLite
keeps the permanent record). All views also filter out CLOSED_\*
regardless, so even a transient closed row never bloats the active view.

## Empirical unknowns to validate (steps 4–5, before trusting the path)

Honesty about what we've proven vs. assumed. **Proven on the live site
(2026-06-02):** create a list, create a list item. **Not yet proven —
validate in the read-only/push prototype before relying on them:**

- **Writing a Person column value via Graph.** Graph generally requires
  the user's site `LookupId` (e.g. a `CorrAuthorLookupId` /
  `DataContactLookupId` field), not just an email string — hence
  `GraphClient.resolve_user`. This is the single most likely thing to
  need iteration. Validate with one row before building the full push.
- **`@cicbiomagune.es` ↔ Entra UPN correspondence** for real users.
- **Hyperlink + Choice field write payloads** via Graph (shape of the
  `fields` dict for those types).

Each is a one-row check in step 4/5, not a blocker for the schema.

## References

- Graph — create a list:
  `https://learn.microsoft.com/en-us/graph/api/list-post-lists`
- Graph — create/update a list item:
  `https://learn.microsoft.com/en-us/graph/api/listitem-create`,
  `https://learn.microsoft.com/en-us/graph/api/listitem-update`
- SharePoint list view threshold (5000):
  `https://learn.microsoft.com/en-us/sharepoint/manage-large-lists`
- `[Me]` / person view filtering is standard SharePoint view
  configuration.

## Decisions log

Locked 2026-06-02 (operator review of the step-3 draft):

1. **Model the corresponding author, not the PI.** It fits papers, it is
   already cached from the DB, and it needs no new enrichment. PI/group
   is not modeled. The CA gets its own `[Me]` view and is the expected
   (not enforced) party to reassign the data contact.
2. **Effective corresponding author override.** New
   `set_corresponding_author` / `reset_corresponding_author` CLI
   overrides + a `corresponding_author_overridden` column let the
   operator pin a biomaGUNE person on rows whose real CA is external/
   blank. Auto-deriving a biomaGUNE PI from the author list is out of
   scope for v1.
3. **Exemption categories** locked to the four structured buckets +
   "Other", per the *Exemption categories → closure* table.
4. **"Archived elsewhere" closes as archived** (`CLOSED_DATA_ARCHIVED`
   via the new `close_archived_external`, requiring PID + URL), so
   external archiving counts in "data archived" totals — not as a
   generic exception.

Still open (low stakes): the SOP link target (`sharepoint.sop_url`) —
placeholder until decided.
