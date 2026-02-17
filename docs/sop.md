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

## 7. Procedure

### 7.1 Regular scanning (daily or as-needed)

1. Run `scan_folders`.
2. Script updates SQLite:

   * New folder → `OPEN_INACTIVE`
   * Empty→non-empty transition → `OPEN_ACTIVE` and sets `became_active_at`
   * Folder missing while status OPEN → flags integrity warning

### 7.2 Weekly operations (recommended cadence)

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

### 7.3 Email handling

* Reminder and completion emails are generated as drafts.
* Sending remains manual (copy/paste), but `apply_actions` records `remind_sent` or `completion_sent` when you mark them.

## 8. Integrity controls

* If a folder disappears but the archive is not `CLOSED_*`, the report flags:

  * “Missing folder but still OPEN” → manual reconciliation required.
* Closed items remain in SQLite permanently for lookup/audit.

## 9. Audit trail

Every applied action is recorded in an `events` table (timestamp, publication_id, action_code, old_status, new_status, user_note, pid/url if supplied).


