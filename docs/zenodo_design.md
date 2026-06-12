# Zenodo automation — design (Stages 2.5 and 3)

This is the design reference for the Zenodo work in the staged
automation plan (`roadmap.md`). It covers Stage 2.5 (automate
creation of empty draft records) and Stage 3 (automate file uploads
and publication) as one design, since they share most of the
infrastructure. Stage 4 (write-back to the internal publication DB)
is out of scope here.

## Scope

**Stage 2.5 — what changes from today:** today, `oa emails` writes
`output/zenodo_cheat/<pub_id>.txt` and the operator opens Zenodo in
a browser, creates a new draft, and copies fields across by hand.
After Stage 2.5, an action-sheet row drives a single API call that
creates the draft with all consolidated metadata pre-filled and
records the returned deposition ID into the existing `zenodo_code`
column. No files yet.

**Stage 3 — what changes from today:** today, files are dragged
into the Zenodo UI. After Stage 3, an action-sheet row triggers
upload of all data files from the SharePoint-synced folder via the
Zenodo Files API, with progress reporting and resume support, then
a second row triggers publish.

The DOI/PID rule from the roadmap is preserved: the dataset gets a
Zenodo-minted DOI; the paper DOI is recorded only as a Zenodo
`related_identifiers` entry (relation `isSupplementTo`).

## Zenodo REST API surface we use

Two host URLs, controlled by a config flag:

- `https://zenodo.org` — production (real DOIs, `10.5281/zenodo.*`)
- `https://sandbox.zenodo.org` — sandbox (test DOIs, `10.5072/zenodo.*`).
  Tokens are separate from production; sandbox can be wiped at any time.
  Used for all initial integration testing before any production run.

### Endpoints

| Purpose | Method | Path |
|---|---|---|
| Create empty draft | `POST` | `/api/deposit/depositions` |
| Edit draft metadata | `PUT` | `/api/deposit/depositions/{id}` |
| Read draft | `GET` | `/api/deposit/depositions/{id}` |
| Upload a file (new Files API) | `PUT` | `{bucket_url}/{filename}` |
| List files on a draft | `GET` | `/api/deposit/depositions/{id}/files` |
| Delete a file from a draft | `DELETE` | `/api/deposit/depositions/{id}/files/{file_id}` |
| Publish draft → record | `POST` | `/api/deposit/depositions/{id}/actions/publish` |
| Discard draft | `POST` | `/api/deposit/depositions/{id}/actions/discard` |
| Create new version of a published record | `POST` | `/api/deposit/depositions/{id}/actions/newversion` |

The `bucket_url` for file uploads comes from the create-draft
response under `links.bucket`. The new Files API supports 50 GB per
file and 50 GB total per record (100 files max) — well above what
we need for any realistic preclinical data drop.

### Authentication

OAuth 2.0 personal access token (PAT), passed as
`Authorization: Bearer <token>` on every request. Tokens are
created in the Zenodo UI; the only scopes we need are
`deposit:write` and `deposit:actions`. We do **not** request
`user:email` or `user:read` — keep scope minimal.

Storage: token in `~/.zenodorc` mode 600 (matches the `~/.my.cnf`
precedent from Stage 2 — credential file outside the repo, never
read into chat or committed). Production and sandbox tokens are
kept under separate keys in the same file.

```ini
# ~/.zenodorc
[zenodo]
token = <production_token>

[zenodo-sandbox]
token = <sandbox_token>
```

The module picks which section to read based on the config flag
(`zenodo.environment = "production"` vs `"sandbox"`).

### Rate limits and retry policy

Zenodo's stated authenticated limits: 100 req/min, 5000 req/hour.
Response headers include `X-RateLimit-Remaining` and
`X-RateLimit-Reset`. The OA tracker is far below this even in the
worst case (each archive needs ~4 calls to draft + N uploads), so
limits are practically irrelevant unless we run the file-upload
path against many archives back-to-back.

Retry policy:

| Status | Retry? | How |
|---|---|---|
| 2xx | n/a | success |
| 400 (validation error) | no | surface to operator; the metadata builder produced bad input |
| 401 | no | surface as a config problem (token missing/expired) |
| 403 | no | surface as a scope problem (token missing `deposit:actions`) |
| 404 | no | surface; the deposition ID we cached is gone |
| 409 (conflict, async integration in progress) | yes | exponential backoff, 5 min cap, 3 attempts |
| 429 | yes | sleep until `X-RateLimit-Reset` |
| 5xx | yes | exponential backoff with jitter, 3 attempts |
| connection error | yes | exponential backoff with jitter, 3 attempts |

All retries log the attempt to the events table; final failures
become action-sheet errors the operator can re-trigger after the
underlying issue is fixed.

## Module structure

New file: `src/oa_tracker/zenodo.py`. Public surface:

```python
# Config / client
def load_config(cfg: Config) -> ZenodoConfig
class ZenodoClient(base_url, token, timeout=...)
    def request(method, path, **kwargs) -> dict        # handles retries, raises on terminal errors
    def upload_to_bucket(bucket_url, filename, fileobj) -> dict

# Metadata
def build_metadata_for_archive(archive: dict, cfg: Config) -> dict   # pure; no I/O
def summarize_metadata(metadata: dict) -> str                         # one-line operator summary

# Lifecycle ops (thin wrappers around ZenodoClient.request)
def create_draft(client, metadata: dict) -> DraftInfo                # returns {id, bucket_url, conceptdoi}
def get_draft(client, deposition_id) -> dict
def update_metadata(client, deposition_id, metadata: dict) -> dict
def list_files(client, deposition_id) -> list[FileInfo]
def upload_file(client, bucket_url, path: Path, on_progress=None) -> FileInfo
def delete_file(client, deposition_id, file_id) -> None
def publish(client, deposition_id) -> PublishedRecord                # returns {doi, conceptdoi, html_url}
def discard(client, deposition_id) -> None

# File discovery (in folder → list to upload)
def discover_files(folder: Path) -> list[Path]                       # walks, applies ignore rules
```

The module has zero dependencies on the database — it only knows
about Zenodo. `actions.py` is the layer that orchestrates: it reads
the archive row, calls into `zenodo.py`, writes back the resulting
deposition ID / DOI / status. This keeps `zenodo.py` testable with
plain dicts and mocked HTTP, no SQLite.

HTTP library: `urllib.request` is too clumsy for streaming PUTs and
multipart cases. Add `requests` as a runtime dep — already a
transitive dep via `msal` from the SharePoint spike, and small
enough that it doesn't violate the "lightweight ethos" of the
project. Promote to `pyproject.toml` as part of Stage 2.5.

## Metadata mapping

Zenodo fields populated from what we have in `archives` + `pub_db`
+ central DB + config. All defaults below are **locked as of
2026-05-30** — operator decisions and central-DB findings recorded
in the *Decisions log* section at the bottom of this document.

| Zenodo field | Source | Default |
|---|---|---|
| `upload_type` | constant | `"dataset"` |
| `title` | `archives.pub_title` (cached from `publication.title`) | fallback `"Supporting data for publication {pub_id}"` if missing |
| `description` | `publication.abstract` wrapped in a framing block (see *Description template* below); fallback to short generated description if abstract missing | populated in ~74% of cases from the live DB |
| `creators` | parsed from `publication.author_with_affiliation` (Web of Science export format). Name from the parenthesized full form (`Family, Given`). Affiliation defaults to empty; tagged `"CIC biomaGUNE"` only for authors we can match against `publi_corr_auth` / `publi_first_auth` FK lookups (optionally extended via affiliation-index propagation). Affiliation strings for non-biomaGUNE co-authors are not in our data and cannot be emitted. | parsing logic specified in *Author parsing* below |
| `contributors` | data contact, type `DataCurator`, affiliation `"CIC biomaGUNE"` | (have) |
| `publication_date` | today (ISO `YYYY-MM-DD`) at draft-creation time — this is the *data* publication date, not the paper's | (derive) |
| `access_right` | `"open"` if `max_embargo_months` is null or zero; `"embargoed"` otherwise | (derive) |
| `embargo_date` | today + `max_embargo_months` | only when access_right=embargoed |
| `license` | constant via config | `"CC0-1.0"` (CC0 1.0 Universal — public-domain dedication). Per-archive override possible via a new `zenodo_license_override` column if specific cases need a different license. |
| `related_identifiers` | `[{identifier: pub_doi, relation: "isSupplementTo", resource_type: "publication-article"}]` | (have) |
| `communities` | none | no biomaGUNE community exists; field omitted from payload |
| `keywords` | constant via config | `["CIC biomaGUNE"]`. Per-archive operator override possible via a new field, not yet defined. |
| `version` | `"1.0.0"` on first draft | (derive) |
| `grants` | derive from `project.id_funding` → grant ID lookup | (future — not in Stage 2.5) |

### Description template

The Zenodo `description` field is populated from
`publication.abstract` wrapped in framing text that (a) makes clear
this is the dataset record (not the paper), (b) provides
attribution for the reproduced abstract, and (c) links the paper:

```
This record contains the supporting research data for the publication
"{publication_title}" by {first_author_name} et al.,
{publication_journal} ({publication_year}), DOI: {publication_doi}.

Abstract (reproduced from the original publication):

{publication_abstract}
```

When `publication.abstract` is null or empty (~26% of rows), the
fallback description is:

```
This record contains the supporting research data for the publication
"{publication_title}" by {first_author_name} et al.,
{publication_journal} ({publication_year}), DOI: {publication_doi}.

See the original publication for full context.
```

**Copyright note:** abstracts are often copyright-held by the
publishing journal. Including the abstract verbatim under an
explicit "reproduced from" attribution is standard practice for
institutional dataset records and is generally considered fair use
for indexing/discovery. The operator can override the description
on a per-archive basis if a specific journal's license requires
different handling.

### Author parsing

Primary source: `publication.author_with_affiliation` (`varchar(6000)`,
NOT NULL).

Real-world format inspection (two sample rows, 2026-05-31) shows
this field uses **Web of Science export format** — not
free-text names + affiliations. Per-author entries look like:

```
Carregal-Romero, S (Carregal-Romero, Susana)[ 1,2 ]
```

Structure:

- Short form: `LASTNAME, INITIALS` (e.g., `Carregal-Romero, S`)
- Full form in parentheses: `(LASTNAME, GIVEN_NAME)` — preferred
  for Zenodo (already in the right `Family, Given` shape)
- Affiliation indices in brackets: `[ 1 ]` or `[ 1,5 ]` — pointers
  to a numbered affiliation list that lives **outside this field
  and outside our data sources entirely**. We have no affiliation
  strings for the non-biomaGUNE authors.

Entries are separated by ` ; ` (semicolon with spaces on each side).

Parsing strategy:

1. Split on ` ; ` to get per-author entries.
2. For each entry, regex-extract:
   - **Name**: content of the parenthesized group `\(([^)]+)\)`
     (the clean `Family, Given` form). Fallback to the short form
     `^([^(]+?)\s*\(` if no parentheses.
   - **Affiliation indices**: comma-separated integers inside
     `\[\s*([\d,\s]+)\s*\]`. Kept internally for the biomaGUNE
     index-propagation step (below) but never emitted to Zenodo
     verbatim.
3. Default `affiliation = ""` for every author.
4. Identify biomaGUNE-affiliated entries using **only** the FK
   tables we already join in Stage 2:
   - Corresponding author via `publi_corr_auth.id_user` →
     `mdm_personal` → name.
   - First author via `publi_first_auth.id_user` → same.
   Fuzzy-match each of these two names against the parsed entries
   by surname + first initial (case-insensitive). For matches, set
   `affiliation = "CIC biomaGUNE"`.
5. **Index propagation (Stage 2.5 enhancement, optional):** if a
   biomaGUNE author has affiliation indices `[i,j]`, any other
   author whose index set intersects `{i,j}` is also at
   biomaGUNE. This is the only way to reliably tag non-FK-known
   biomaGUNE authors. Implement this only if testing shows it
   improves output without false positives.
6. Emit a `creators` entry per author: `{name, affiliation}`
   (omit affiliation key when empty rather than sending `""`).

Fallback path (when `author_with_affiliation` doesn't match the WoS
pattern — e.g., a malformed or legacy row):

1. Parse `publication.author` (`varchar(1000)`, NOT NULL,
   populated for every row) as a list of names separated by `;`.
2. Apply the same FK-based biomaGUNE tagging as step 4 above.
3. Emit `{name, affiliation}` entries with affiliation only on
   FK-known biomaGUNE authors.

The implementation should log when it falls back so we can see how
often `author_with_affiliation` is well-formed in real data. If
fallback rate is high, revisit the parser; if it's near zero, retire
the fallback path.

**Known limitation:** for external (non-biomaGUNE) co-authors the
Zenodo record will list them by name with no affiliation. Their real
affiliations exist only in the original WoS export (numbered list)
and were not ingested into our central DB. Fixing this would require
either re-ingesting from WoS or a manual operator step per archive,
neither of which is in scope for Stage 2.5. The record stays
editable in the Zenodo UI for cases where the operator wants to
correct this by hand.

ORCIDs are not pulled (not present in our data sources). The Zenodo
record can be edited later via `update_metadata` once ORCIDs become
available from any source.

## File handling (Stage 3)

### Discovery

Walk `archives.folder_path` recursively. Apply ignore patterns:

- macOS / Windows clutter: `.DS_Store`, `Thumbs.db`, `desktop.ini`
- Office lock files: `~$*`
- Hidden files starting with `.` at the top level (operator can
  override with a config option if a legitimately-hidden file
  needs to ship — rare)
- Files > 50 GB (Zenodo per-file limit; surface as error, do not
  silently skip)

Total upload size > 50 GB also errors out. In practice neither
limit will be hit by realistic preclinical data drops, but the
checks are cheap and stop a bad surprise mid-upload.

### Naming

Zenodo stores files flat. For nested folder structures we have two
options:

1. **Flatten with separator** (`subfolder_filename.ext`). Simple,
   inspectable on the Zenodo UI, no extra tooling needed to
   re-assemble. Loss: folder semantics. Risk: name collisions if
   two folders contain a file with the same name (`flatten` must
   detect and refuse rather than overwrite).
2. **Zip the whole folder** into `{pub_id}_data.zip` and upload
   that single file. Preserves structure. Risk: downstream users
   need to download and unpack to inspect.

**Default: flatten with collision detection.** Cleaner for
researchers who reuse the data without bespoke tooling. Operator
can override on a per-archive basis to pre-zip if structure matters
for that dataset.

### Upload strategy

- One `PUT` per file to the bucket URL, streaming body (don't
  read the whole file into memory).
- After each successful upload, record the file's name + size +
  checksum in a local manifest file
  (`output/zenodo_uploads/{pub_id}/manifest.json`) so a re-run
  knows what's already done. Resume support is: on re-run,
  compare the manifest to the local folder, skip files whose
  name+checksum match an entry; re-upload the rest.
- If a file already exists on the draft with a different
  checksum (operator modified after first upload), delete it via
  the legacy file endpoint and re-upload.
- Progress reporting: print one line per file as it starts and
  one as it completes, with size. Total bytes uploaded / remaining
  shown at the end.

### Idempotency

Re-running `oa zenodo upload <pub_id>` on a draft that has been
partially uploaded should converge to a fully-uploaded state, not
duplicate or overwrite. The manifest mechanism above is the
idempotency layer.

## Workflow integration

Per the validation-phase rule
(`feedback_no_auto_state_changes.md`): no auto-apply on day one. All
Zenodo actions surface as action-sheet rows the operator confirms
with `done=1`. Promotion to auto-apply happens later, per signal
class, once the operator has seen it behave correctly.

### New task codes

Three new codes plus their action-sheet behavior:

| Task code | Emitted when | Apply effect |
|---|---|---|
| `zenodo_create_draft` | archive enters `OPEN_READY_FOR_ZENODO_DRAFT` and has no `zenodo_code` | calls `create_draft` with derived metadata, stores `zenodo_code = deposition_id`, transitions to `OPEN_ZENODO_DRAFT_CREATED` |
| `zenodo_upload_files` | archive is at `OPEN_ZENODO_DRAFT_CREATED` and the folder has files | walks folder, uploads all files, surfaces progress; on success leaves status at `OPEN_ZENODO_DRAFT_CREATED` (upload alone is not validation) |
| `zenodo_publish` | archive is at `OPEN_ZENODO_DRAFT_VALIDATED` | calls `publish`, stores returned DOI as `final_pid`, transitions to `OPEN_ZENODO_PUBLISHED` |

The `zenodo_validated` transition stays manual — the operator
confirms the draft is correct by looking at it in the Zenodo UI
before publishing. We do not auto-validate.

A separate human-readable `note` is emitted with each row so the
operator sees a summary before confirming:
- create: title + access_right + first creator
- upload: file count + total size
- publish: final metadata summary + the DOI that will be minted

### Promotion path (from action-sheet-routed to auto-apply)

Likely order of promotion once we have confidence:

1. `zenodo_create_draft` — creating a draft is reversible
   (`discard` deletes it cleanly, no DOI minted yet). Lowest-risk
   to auto-apply.
2. `zenodo_upload_files` — uploads are reversible until publish.
3. `zenodo_publish` — **last to promote, if ever**. Publishing
   mints a real DOI which is permanent. Operator confirmation here
   has very high safety value; auto-publish is probably never
   appropriate.

## Configuration

Additions to `config.toml`:

```toml
[zenodo]
environment = "sandbox"                # "sandbox" or "production"
default_license = "CC0-1.0"            # CC0 1.0 Universal — public-domain dedication
default_affiliation = "CIC biomaGUNE"
default_keywords = ["CIC biomaGUNE"]
upload_strategy = "flatten"            # "flatten" or "zip"
manifest_dir = "output/zenodo_uploads" # relative to project root
```

Notes:

- No `community` setting — biomaGUNE has no Zenodo community as of
  2026-05-30. Field omitted from the payload entirely.
- Per-archive license overrides will be supported via a new
  `zenodo_license_override` column on `archives` (added in the
  Stage 2.5 schema bump). The override is operator-managed,
  preserved across scans like `zenodo_code`.

Sensitive material — the token — stays in `~/.zenodorc` (see
*Authentication* above).

`environment = "sandbox"` is the safe default; the operator
explicitly switches to `"production"` only when ready. We never
default to production.

## Error handling

Three classes of failure, three handling patterns:

1. **Transient (5xx, 429, network).** Retry with backoff inside
   `ZenodoClient.request`. The operator sees nothing unless final
   failure.
2. **Configuration (401, 403, missing token, wrong environment).**
   Surface as a clear action-sheet error message naming the fix
   ("token missing from ~/.zenodorc[zenodo]" or "token lacks
   deposit:actions scope"). Do not retry.
3. **Data (400 validation errors, 404 stale ID).** Surface the
   Zenodo `errors` array verbatim in the action-sheet note. Do not
   retry — the metadata builder or local state needs fixing.

## Decisions log

All Stage 2.5 design decisions were locked 2026-05-30. Recorded here so the
rationale survives context loss.

1. **Default license: `CC0-1.0`** (CC0 1.0 Universal — public-domain
   dedication). Operator override allowed per archive via a new
   `zenodo_license_override` column. Rationale: most permissive
   default, maximizes downstream reusability.
2. **biomaGUNE Zenodo community: none.** No community exists today;
   the `communities` field is omitted from the payload. Revisit if a
   community is created later.
3. **Default keywords: `["CIC biomaGUNE"]`.** Per-archive operator
   override possible. Other tags (e.g., "preclinical imaging") may be
   added later as patterns emerge from real records.
4. **Description: paper abstract with attribution framing.**
   `publication.abstract` (live from the central DB; ~74% populated)
   wrapped in a block that names the record as supporting data for
   the publication, attributes the reproduced abstract to the
   original publication, and links the paper DOI. Fallback template
   used when `abstract` is null/empty. Full wording in *Description
   template* above.
5. **Sandbox first.** Ship Stage 2.5 against
   `https://sandbox.zenodo.org` for two or three real archives.
   Operator manually inspects the resulting drafts in the sandbox
   UI. Once verified, flip `environment = "production"` in
   `config.toml`.
6. **All authors on every record.** Primary source:
   `publication.author_with_affiliation` (NOT NULL, populated for
   all 2425 rows in the central DB). **Format discovery
   2026-05-31:** real rows use Web of Science export format —
   parenthesized full names plus opaque numeric affiliation
   indices, with the affiliation strings themselves *not* present
   in our data. We can therefore emit every author's name, and
   tag biomaGUNE affiliation only for authors we can identify via
   `publi_corr_auth` / `publi_first_auth` FK lookups (with
   optional index propagation to catch other biomaGUNE
   co-authors). External co-authors get name-only entries.
   Records remain editable in the Zenodo UI for cases where the
   operator wants to add affiliations by hand. ORCIDs are not
   pulled (not in our data).

### Defaults the operator flagged as locked

The operator's 2026-05-30 review explicitly confirmed two fields
that were already in the design but worth marking as locked
rather than under review:

- `upload_type = "dataset"` (constant; not configurable per archive).
- `related_identifiers` always contains the paper DOI as
  `{identifier: pub_doi, relation: "isSupplementTo",
  resource_type: "publication-article"}`. This is the single
  authoritative pointer from the dataset record back to the paper,
  per the DOI/PID rule from the roadmap.

## What we are not building (for now)

To keep scope tight and avoid premature work:

- Multi-version handling (`actions/newversion`) — only matters if
  we ever update an already-published record. Defer until a real
  use case appears.
- Grants linkage — Zenodo accepts grant identifiers from a known
  funder DB; mapping our `cff_funding` rows to that DB is
  research-y and not on the critical path.
- Author/ORCID enrichment from `mdm_personal` — see the gaps
  section. Follow-up after Stage 2.5 if needed.
- Embargo-date automation beyond the simple `today +
  max_embargo_months` calculation — anything more nuanced is a
  separate decision.
