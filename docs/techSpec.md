# Technical Spec: OA Tracker + Operator Action Sheet

> Staged automation plan: see [roadmap.md](roadmap.md).
>
> Mandate classification (how we decide if a publication needs data
> archiving): see [mandate_classification.md](mandate_classification.md).

## 1. Components

### 1.1 SQLite database

File: `oa_tracker.sqlite`

Tables (minimum):

* `archives`

  Core columns (v1):
  * `publication_id` TEXT PK
  * `folder_path` TEXT
  * `first_seen_at` DATETIME
  * `became_active_at` DATETIME NULL
  * `last_seen_at` DATETIME
  * `last_changed_at` DATETIME NULL
  * `status` TEXT (one of the status codes)
  * `final_pid` TEXT NULL
  * `final_url` TEXT NULL
  * `notes` TEXT NULL
  * `last_notified_at` DATETIME NULL
  * `reminder_count` INTEGER DEFAULT 0
  * `next_reminder_at` DATETIME NULL
  * `unexpected_missing_folder` INTEGER DEFAULT 0
  * `missing_folder_detected_at` DATETIME NULL

  Stage-2 pub-DB cache (v2; auto-refreshed every `oa scan`):
  * `pub_title` TEXT NULL — from `publication.title`
  * `pub_doi` TEXT NULL — from `publication.doi`
  * `pub_journal` TEXT NULL — from `publication.journal`
  * `pub_year` INTEGER NULL — from `publication.year`
  * `oa_paper_required` INTEGER NULL (0/1) — derived flag
  * `oa_data_required` INTEGER NULL (0/1) — derived flag (the trigger for our workflow)
  * `max_embargo_months` INTEGER NULL
  * `oa_mandate_source` TEXT NULL — audit trace of contributing per-project signals
  * `oa_mandate_missing` INTEGER NULL (0/1) — 1 when no mandate could be derived
  * `corresponding_author_name` TEXT NULL — from `publi_corr_auth → center_user`
  * `corresponding_author_email` TEXT NULL — derived as `center_user.username`@institutional domain
  * `central_repository` TEXT NULL — repository name(s) recorded centrally, joined with `; `
  * `central_repository_code` TEXT NULL — parallel codes, joined with `; `
  * `pub_db_last_refreshed_at` DATETIME NULL

  Stage-2 operator-managed (v2; preserved across scans when `*_overridden=1`):
  * `data_contact_name` TEXT NULL — seeds from `corresponding_author_name`
  * `data_contact_email` TEXT NULL — defaults to `'TBD'` until set
  * `data_contact_overridden` INTEGER DEFAULT 0
  * `zenodo_code` TEXT NULL — seeds from central DB iff repository name is `Zenodo`
  * `zenodo_code_overridden` INTEGER DEFAULT 0

  SharePoint List track (v3):
  * `sharepoint_item_id` INTEGER NULL, `sharepoint_synced_at` DATETIME NULL — sync bookkeeping
  * `corresponding_author_overridden` INTEGER DEFAULT 0 — mirrors `data_contact_overridden`

  Automation (v4):
  * `package_has_zip` / `package_has_readme` INTEGER NULL (0/1) — protocol
    package detected by the scanner (README counts beside the zip or inside it)
  * `package_has_manuscript` INTEGER NULL (0/1) — v5, rule update 2026-07-15:
    a manuscript version (`.doc`/`.docx`/`.pdf`, the pre-print) beside the
    zip (NOT accepted from inside it; `~$`/hidden files don't count).
    Auto-QC requires it alongside zip + README, and it uploads to Zenodo
    as part of the package (pre-prints are ours to publish openly).
  * `package_checked_at` DATETIME NULL
  * `user_done_flag` INTEGER DEFAULT 0, `user_done_at` DATETIME NULL — the
    Tracker "I think this is done" tick, persisted by the SharePoint pull
  * `zenodo_doi` TEXT NULL — the reserved (pre-publish) Zenodo DOI
  * `zenodo_env` TEXT NULL — `sandbox`/`production`; pins which Zenodo
    instance a draft lives on so the two can never be confused

  The executable source of truth for the schema is `_SCHEMA_SQL` +
  the `_Vn_TO_Vm_ALTERS` lists in `src/oa_tracker/db.py`.

* `events` (append-only audit log)

  * `event_id` INTEGER PK AUTOINCREMENT
  * `ts` DATETIME
  * `publication_id` TEXT
  * `action_code` TEXT
  * `old_status` TEXT
  * `new_status` TEXT
  * `pid` TEXT NULL
  * `url` TEXT NULL
  * `note` TEXT NULL
  * `source` TEXT (e.g., `action_sheet`, `scanner`)

Optional:

* `folder_fingerprints` (to avoid noisy reprocessing)
* `email_log` (track generated/sent drafts)

## 2. Status transition rules (validated by `apply_actions`)

Allowed transitions (core):

* scanner-driven:

  * (none) → `OPEN_INACTIVE` on first detection
  * `OPEN_INACTIVE` → `OPEN_ACTIVE` when content appears
* operator-driven:

  * `OPEN_ACTIVE` → `OPEN_READY_FOR_ZENODO_DRAFT` (QA passed)
  * `OPEN_ACTIVE` stays `OPEN_ACTIVE` (QA not passed; add note)
  * `OPEN_READY_FOR_ZENODO_DRAFT` → `OPEN_ZENODO_DRAFT_CREATED`
  * `OPEN_ZENODO_DRAFT_CREATED` → `OPEN_ZENODO_DRAFT_VALIDATED`
  * `OPEN_ZENODO_DRAFT_VALIDATED` → `OPEN_ZENODO_PUBLISHED`
  * `OPEN_ZENODO_PUBLISHED` → `OPEN_DB_UPDATED`
  * `OPEN_DB_UPDATED` → `CLOSED_DATA_ARCHIVED` (folder removed + PID present)
  * Any OPEN → `CLOSED_PUBLICATION_ONLY` (rare, explicitly selected)
  * Any OPEN → `CLOSED_EXCEPTION` (requires/strongly encourages note)

Hard invariant check (at least warning-level):

* `zenodo_published` action should supply a **dataset PID/DOI** that is not the paper DOI (where detectable).

## 3. Operator Action Sheet (the “text-based task system”)

### 3.1 File format

File: `action_sheet.tsv` (tab-separated; easy in VS Code, Notepad++, Excel-as-text)

Columns (recommended):

* `publication_id`
* `current_status`
* `task_code`
* `task_text`
* `due_date` (optional)
* `done` (0/1)
* `pid` (optional)
* `url` (optional)
* `note` (optional)

Example rows:

* `12345  OPEN_ACTIVE  qa_pass  "QA complete; ready for Zenodo draft"  2026-02-21  1  -  -  "README inside zip"`
* `12345  OPEN_READY_FOR_ZENODO_DRAFT  zenodo_draft_created  "Create Zenodo draft"  2026-02-21  1`
* `12345  OPEN_ZENODO_DRAFT_VALIDATED  zenodo_published  "Publish Zenodo record"  2026-02-21  1  10.5281/zenodo.XXXXXXX  https://zenodo.org/record/XXXXXXX  ""`
* `12345  OPEN_ZENODO_PUBLISHED  db_updated  "Update internal publication DB with dataset DOI/URL"  2026-02-21  1`
* `12345  OPEN_DB_UPDATED  folder_removed  "Remove SharePoint folder; close archive"  2026-02-21  1`

### 3.2 Task codes

Pipeline + closure (v1):

* `remind_sent` (updates `last_notified_at`, increments reminder count)
* `contact_pi_manual` (final-reminder slot — operator contacts PI directly)
* `qa_pass` (→ `OPEN_READY_FOR_ZENODO_DRAFT`)
* `qa_hold` (keeps `OPEN_ACTIVE`, writes note)
* `zenodo_draft_created` (→ `OPEN_ZENODO_DRAFT_CREATED`)
* `zenodo_validated` (→ `OPEN_ZENODO_DRAFT_VALIDATED`)
* `zenodo_published` (→ `OPEN_ZENODO_PUBLISHED`, requires PID/URL if available)
* `db_updated` (→ `OPEN_DB_UPDATED`)
* `folder_removed` (→ `CLOSED_DATA_ARCHIVED` if PID exists; otherwise `CLOSED_EXCEPTION` unless explicitly overridden)
* `close_publication_only` (→ `CLOSED_PUBLICATION_ONLY`)
* `close_exception` (→ `CLOSED_EXCEPTION`, note strongly encouraged)

Stage-2 additions:

* `mandate_missing` — acknowledgment-only investigation task; surfaced
  on the sheet when the pub-DB classification produces no derivable
  mandate. Setting `done=1` records an audit event but does not change
  status; the row regenerates next scan until the upstream mandate is
  fixed or the operator changes `task_code` to `close_exception`.
* `set_data_contact` / `reset_data_contact` — CLI-only operator
  overrides for the data-contact name/email. Setting marks the field
  as operator-managed (`data_contact_overridden=1`) so scans don't
  overwrite it; resetting clears the flag and lets the next scan
  re-seed from the corresponding author.
* `set_zenodo_code` / `reset_zenodo_code` — same pattern for the
  Zenodo record code.

SharePoint-track additions (v3):

* `propose_data_contact` / `propose_exemption` / `propose_done` —
  signals pulled from user edits on the List; acknowledgment-only as
  sheet rows (categorized exemptions re-route to a concrete `close_*`).
* `user_note` — records a List free-text note to the archive; no status change.
* `close_archived_external` — any OPEN → `CLOSED_DATA_ARCHIVED` with an
  **external** repository's PID + URL (both required).
* `set_corresponding_author` / `reset_corresponding_author` — CLI-only
  override, mirrors `set_data_contact`.

Zenodo API codes (v4 — the apply IS the API call; see
`actions._apply_zenodo_row`):

* `zenodo_create_draft` — `OPEN_READY_FOR_ZENODO_DRAFT` →
  `OPEN_ZENODO_DRAFT_CREATED`; creates the draft, reserves the DOI,
  records `zenodo_code`/`zenodo_doi`/`zenodo_env`.
* `zenodo_upload_files` — uploads the folder package to the draft;
  status unchanged (upload is not validation).
* `zenodo_publish` — `OPEN_ZENODO_DRAFT_VALIDATED` →
  `OPEN_ZENODO_PUBLISHED`; publishes, records `final_pid`/`final_url`.
  Never emitted by the automation engine — operator keystroke only.

The executable source of truth for codes and transitions is
`TASK_CODES` / `TRANSITIONS` in `src/oa_tracker/status.py`.

### 3.3 Apply semantics

`apply_actions`:

* Reads rows where `done=1` and not yet applied,
* Validates transitions,
* Updates `archives`,
* Inserts an `events` record,
* Optionally marks the row as applied (either by:

  * adding an `applied_at` column, or
  * moving applied rows to `action_history.tsv`)

This keeps the sheet as your working checklist without touching SQLite manually.

## 4. CLI

* `oa scan` → update registry from folder tree; refresh pub-DB cache
* `oa report` → generate `weekly_report.md`
* `oa sheet` → generate `action_sheet.tsv`
* `oa apply action_sheet.tsv` → apply completed actions; write events; move applied rows to `action_history.tsv`
* `oa emails` → generate reminder/completion drafts and Zenodo cheat sheets
* `oa action <pub_id> <task_code> [--done 1|2] [--pid] [--url] [--note] [--email] [--name] [--code]`
  — mid-week single-archive update; also the entry point for the Stage-2
  override task codes (`set_data_contact`, `set_zenodo_code`, etc.)
* `oa reopen <pub_id> --reason "..." [--to OPEN_ACTIVE|OPEN_INACTIVE]`
  → bring a `CLOSED_*` archive back to an OPEN status
* `oa status [<pub_id>]` → show one or all archives
* `oa sharepoint provision|sync [--read-only]` → List sync (parallel track)
* `oa auto` → unattended cycle for cron (scan → List pull/auto-apply →
  advance → List push → sheet/emails/report → `output/auto_digest.md`);
  gates in `[automation]`, engine in `src/oa_tracker/auto.py`

## 5. Email template generation (v1)

Templates (plain text) parameterized by:

* publication_id
* current status
* reminder count / last notify
* PID + URL (for completion)

Generated outputs:

* `email_drafts/reminder_<pubid>_<n>.txt`
* `email_drafts/completion_<pubid>.txt`

## 6. Automation hooks (Stages 2.5, 3, 4 and the parallel SharePoint track)

See [roadmap.md](roadmap.md) for stage definitions and current status.
Stage-specific design docs:

* [zenodo_design.md](zenodo_design.md) — Stage 2.5 (automated draft
  creation) and Stage 3 (file uploads + publish). Module layout,
  metadata mapping, file handling, configuration, error policy.
* [roadmap.md § Parallel track](roadmap.md) — SharePoint List
  user-interaction layer (covers list schema, view design, sync
  module, action-routing policy).

The key design choice across all of these: **task codes become
triggers**. New automation is added as new task codes processed by
`apply_actions`, not as out-of-band side effects in the scanner or
elsewhere. New code paths route through the action sheet at the
start (operator-confirmed) and graduate to auto-apply once a class
of signals has been validated against real data — see
`feedback_no_auto_state_changes.md` in the memory store for the
full rule.

Both sets of automation task codes shipped (see §3.2): the Zenodo API
codes (2026-07-02) and the SharePoint `propose_*` codes (2026-06-03).
The promoted signal classes now auto-apply through `oa auto`'s engine
(`src/oa_tracker/auto.py`), still via the same `apply_single` path with
`source="auto"` in the audit log.

Remaining: if/when central-DB write becomes possible (Stage 4),
`db_updated` can be automated end-to-end and stops being a manual task.

