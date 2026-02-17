# Revised Plan (with Operator Action Sheet)

## Stage 1 (now): Tracking + reminders + operator action sheet

**Inputs:** Synced SharePoint folder tree only.

**Outputs:**

1. **Weekly report**: What’s new / stuck / reminders / ready / integrity warnings.
2. **Operator Action Sheet** (`action_sheet.tsv`): a text list of tasks you can tick off quickly.
3. **Email drafts**: reminder + completion messages generated from templates.

**Operator flow (no direct SQLite edits):**

* You do manual actions (QA review, Zenodo draft, publish, DB update).
* You record the action by editing the sheet (set `done=1`, paste DOI/URL if relevant, add an optional note).
* Run `apply_actions` to update SQLite.

## Stage 2: Zenodo upload automation (still driven by the same action sheet)

Add optional “automation hooks” per action:

* When you mark `zenodo_upload_start`, script can do the upload (or prepare commands).
* When you mark `zenodo_draft_created`, script can validate DOI policy fields, etc.

## Stage 3–4: DB read/write (optional; depends on IT)

Same model:

* DB read enriches the sheet (auto-fills data contact, etc.).
* DB write becomes another action (`db_updated`) that can be automated later.

---

# Technical Spec: OA Tracker + Operator Action Sheet

## 1. Components

### 1.1 SQLite database

File: `oa_tracker.sqlite`

Tables (minimum):

* `archives`

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

### 3.2 Task codes (v1)

* `remind_sent` (updates `last_notified_at`, increments reminder count)
* `qa_pass` (→ `OPEN_READY_FOR_ZENODO_DRAFT`)
* `qa_hold` (keeps `OPEN_ACTIVE`, writes note)
* `zenodo_draft_created` (→ `OPEN_ZENODO_DRAFT_CREATED`)
* `zenodo_validated` (→ `OPEN_ZENODO_DRAFT_VALIDATED`)
* `zenodo_published` (→ `OPEN_ZENODO_PUBLISHED`, requires PID/URL if available)
* `db_updated` (→ `OPEN_DB_UPDATED`)
* `folder_removed` (→ `CLOSED_DATA_ARCHIVED` if PID exists; otherwise `CLOSED_EXCEPTION` unless explicitly overridden)
* `close_publication_only` (→ `CLOSED_PUBLICATION_ONLY`)
* `close_exception` (→ `CLOSED_EXCEPTION`, note strongly encouraged)

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

## 4. CLI (suggested)

* `oa scan` → update registry from folder tree
* `oa report --weekly` → generate `weekly_report.md`
* `oa sheet --weekly` → generate `action_sheet.tsv`
* `oa apply action_sheet.tsv` → apply completed tasks to SQLite; write audit events; refresh drafts
* `oa emails` → regenerate drafts from current DB state (optional)
* `oa export --csv` → export current archive list for sharing (optional)

## 5. Email template generation (v1)

Templates (plain text) parameterized by:

* publication_id
* current status
* reminder count / last notify
* PID + URL (for completion)

Generated outputs:

* `email_drafts/reminder_<pubid>_<n>.txt`
* `email_drafts/completion_<pubid>.txt`

## 6. Future automation hooks (Stage 2+)

The key design choice: **task codes become triggers**.

* If later you allow `zenodo_upload` tasks, `apply_actions` can call a Zenodo upload routine.
* If later DB write becomes possible, `db_updated` can be automated (and you’d stop generating that task).

