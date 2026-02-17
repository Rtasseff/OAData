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

# SOP: Open Access Archive Tracker (Ryan-side)

## 1. Purpose

Provide a lightweight, auditable system to:

* Track OA publication archive folders (inactive/active) from SharePoint sync,
* Manage reminders and manual workflow progress,
* Preserve closure records (PID/URL/notes) after SharePoint folders are removed,
* Reduce missed items and inconsistent closures.

## 2. Scope

Applies to the OA workflow **starting after**:

* A SharePoint publication-ID folder is created and the data contact has been requested to upload materials.

Out of scope:

* Fully automating author compliance, packaging quality, or all publication database operations (manual intervention remains required).

## 3. Roles and responsibilities

* **Data Quality Officer (Ryan):**

  * Performs manual QA, Zenodo actions, and DB updates (until automated),
  * Updates the Operator Action Sheet to record completed actions,
  * Runs the scripts to apply actions and generate reports/emails.
* **Project Office (Nerea/Aitor):**

  * Creates SharePoint folder and requests data contact uploads (upstream of this SOP).

## 4. Records and systems

**Systems of record**

* `oa_tracker.sqlite` (SQLite): statuses, timestamps, reminder history, closure PID/URL, notes, audit log.

**Operator interface**

* `action_sheet.tsv` (plain-text, tab-separated): generated tasks; operator marks completion.

**Outputs**

* `weekly_report.md`
* `email_drafts/` (reminders + completion)
* `change_log/` (optional export of detected changes)

## 5. Definitions

* **Publication folder:** SharePoint-synced folder named by publication ID.
* **Inactive:** folder exists but empty.
* **Active:** folder contains files (indicates data contact activity).
* **PID:** dataset persistent identifier (preferably Zenodo DOI); may also be external repository PID.

## 6. Status model

Main/sub statuses in SQLite:

OPEN:

* `OPEN_INACTIVE`
* `OPEN_ACTIVE`
* `OPEN_READY_FOR_ZENODO_DRAFT`
* `OPEN_ZENODO_DRAFT_CREATED`
* `OPEN_ZENODO_DRAFT_VALIDATED`
* `OPEN_ZENODO_PUBLISHED`
* `OPEN_DB_UPDATED` (temporary)

CLOSED:

* `CLOSED_DATA_ARCHIVED` (PID expected)
* `CLOSED_PUBLICATION_ONLY`
* `CLOSED_EXCEPTION` (note strongly encouraged)

## 7. Action Sheet Task Code Reference

The `task_code` column in `action_sheet.tsv` identifies the **action you are being asked to perform**. It is NOT the current status — see `current_status` for that. Setting `done=1` on a row asserts that you have completed the action described in `task_text`.

### Status transitions (normal pipeline)

| current_status | task_code | task_text (action to take) | resulting status |
|---|---|---|---|
| `OPEN_ACTIVE` | `qa_pass` | Review uploaded data and approve QA | `OPEN_READY_FOR_ZENODO_DRAFT` |
| `OPEN_ACTIVE` | `qa_hold` | Flag QA issue; add note and keep monitoring | *(stays OPEN_ACTIVE)* |
| `OPEN_READY_FOR_ZENODO_DRAFT` | `zenodo_draft_created` | Create Zenodo draft deposit | `OPEN_ZENODO_DRAFT_CREATED` |
| `OPEN_ZENODO_DRAFT_CREATED` | `zenodo_validated` | Validate Zenodo draft metadata and files | `OPEN_ZENODO_DRAFT_VALIDATED` |
| `OPEN_ZENODO_DRAFT_VALIDATED` | `zenodo_published` | Publish Zenodo record (enter PID and URL) | `OPEN_ZENODO_PUBLISHED` |
| `OPEN_ZENODO_PUBLISHED` | `db_updated` | Update internal publication DB with dataset DOI/URL | `OPEN_DB_UPDATED` |
| `OPEN_DB_UPDATED` | `folder_removed` | Confirm SharePoint folder removed; close archive | `CLOSED_DATA_ARCHIVED` |

### Special actions (available from any OPEN status)

| task_code | task_text | resulting status |
|---|---|---|
| `remind_sent` | Send reminder email to data contact | *(no status change; updates reminder count)* |
| `close_publication_only` | Close as publication-only (no data deposit needed) | `CLOSED_PUBLICATION_ONLY` |
| `close_exception` | Close with exception (add note explaining why) | `CLOSED_EXCEPTION` |

### How to edit the action sheet

The sheet is generated with one row per archive showing the next expected action. **You only need to edit three columns:** `done`, `pid`/`url` (when applicable), and `note`. Leave all other columns as-is.

- **To complete an action:** Set `done=1`. The system applies the `task_code` already in the row.
- **To provide data:** Fill in `pid` and/or `url` (e.g. when marking `zenodo_published`).
- **To add context:** Write in the `note` column. Notes are preserved in the audit log.
- **To take an alternate action:** Change `task_code` (and optionally `task_text`) before setting `done=1`. This is mainly relevant at the QA step — see below.

### QA review: branching decision

When an archive is `OPEN_ACTIVE`, the sheet pre-fills `task_code=qa_pass` (the optimistic path). After reviewing the uploaded data:

- **QA passes:** Set `done=1`. Status advances to `OPEN_READY_FOR_ZENODO_DRAFT`.
- **QA does not pass:** Change `task_code` from `qa_pass` to `qa_hold`, add a `note` explaining the issue, then set `done=1`. Status stays `OPEN_ACTIVE` and the note is recorded. The next time you run `oa sheet`, a fresh `qa_pass` row will appear for that archive so you can revisit it.
- **Not ready to decide yet:** Leave `done=0`. The row stays in the sheet untouched.

All other pipeline steps are linear — no branching needed.

### Action sheet columns

| Column | Purpose |
|---|---|
| `publication_id` | The folder/publication identifier |
| `current_status` | The archive's current status in the database |
| `task_code` | The action code — identifies what to do (see tables above) |
| `task_text` | Human-readable description of the action to take |
| `first_seen_at` | When the folder was first detected by the scanner |
| `next_reminder_at` | When the next reminder is due (blank if not applicable) |
| `reminder_count` | Number of reminders already sent |
| `done` | Set to `1` when you have completed this action |
| `pid` | Enter the dataset DOI/PID here (required for `zenodo_published`) |
| `url` | Enter the dataset URL here (optional) |
| `note` | Optional free-text note (recorded in audit log) |

## 8. Procedure

### 8.1 Regular scanning (daily or as-needed)

1. Run `scan_folders`.
2. Script updates SQLite:

   * New folder → `OPEN_INACTIVE`
   * Empty→non-empty transition → `OPEN_ACTIVE` and sets `became_active_at`
   * Folder missing while status OPEN → flags integrity warning

### 8.2 Weekly operations (recommended cadence)

1. Run `make_weekly_report`:

   * New items, stuck items, reminders due, ready queue, integrity warnings.
2. Run `generate_action_sheet`:

   * Produces `action_sheet.tsv` containing tasks for you to act on this week.
3. Perform manual actions:

   * QA review for OPEN_ACTIVE changes
   * Zenodo draft creation/validation/publish
   * Internal publication DB updates
   * SharePoint folder removal when complete
4. As you complete actions, update `action_sheet.tsv` (set `done=1`, add note/PID/URL).
5. Run `apply_actions`:

   * Updates SQLite statuses + timestamps
   * Appends to audit log
   * Regenerates any newly-available email drafts (e.g., completion email once DOI is entered)

### 8.3 Email handling

* Reminder and completion emails are generated as drafts.
* Sending remains manual (copy/paste), but `apply_actions` records `remind_sent` or `completion_sent` when you mark them.

## 9. Integrity controls

* If a folder disappears but the archive is not `CLOSED_*`, the report flags:

  * “Missing folder but still OPEN” → manual reconciliation required.
* Closed items remain in SQLite permanently for lookup/audit.

## 10. Audit trail

Every applied action is recorded in an `events` table (timestamp, publication_id, action_code, old_status, new_status, user_note, pid/url if supplied).


