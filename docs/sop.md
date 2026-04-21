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

Every archive has exactly one status at a time. Statuses begin with `OPEN_*` (work in progress) or `CLOSED_*` (terminal).

### OPEN statuses (linear pipeline)

```
OPEN_INACTIVE  →  OPEN_ACTIVE  →  OPEN_READY_FOR_ZENODO_DRAFT
    →  OPEN_ZENODO_DRAFT_CREATED  →  OPEN_ZENODO_DRAFT_VALIDATED
    →  OPEN_ZENODO_PUBLISHED  →  OPEN_DB_UPDATED  →  CLOSED_DATA_ARCHIVED
```

| Status | Meaning |
|---|---|
| `OPEN_INACTIVE` | Folder exists but is empty — data contact has not yet uploaded anything |
| `OPEN_ACTIVE` | Folder contains files — ready for QA review |
| `OPEN_READY_FOR_ZENODO_DRAFT` | QA passed — ready to create a Zenodo draft |
| `OPEN_ZENODO_DRAFT_CREATED` | Draft deposit exists on Zenodo — ready to validate |
| `OPEN_ZENODO_DRAFT_VALIDATED` | Draft validated — ready to publish |
| `OPEN_ZENODO_PUBLISHED` | Published on Zenodo (PID and URL recorded) — ready to update internal DB |
| `OPEN_DB_UPDATED` | Internal publication DB updated with DOI — ready for folder cleanup |

### CLOSED statuses (terminal)

| Status | Meaning |
|---|---|
| `CLOSED_DATA_ARCHIVED` | Normal successful closure — PID is on record |
| `CLOSED_PUBLICATION_ONLY` | Closed because the publication has no data to deposit (policy decision) |
| `CLOSED_EXCEPTION` | Closed as an exception (note strongly encouraged — e.g. non-compliance, skipped by directive, archived externally) |

Any OPEN status can be closed directly as `CLOSED_PUBLICATION_ONLY` or `CLOSED_EXCEPTION` via the special actions in §7. A CLOSED archive can be reopened via the `oa reopen` command (see §8.5).

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
| `contact_pi_manual` | MAX reminder reached; manually contact PI | *(see "Final reminder" below)* |
| `close_publication_only` | Close as publication-only (no data deposit needed) | `CLOSED_PUBLICATION_ONLY` |
| `close_exception` | Close with exception (add note explaining why) | `CLOSED_EXCEPTION` |

### Final reminder: manual PI contact

The tool sends `max_reminders - 1` automated reminder emails. Once an archive has reached that count (i.e. one slot remains), the next sheet generation replaces the usual `remind_sent` row with a **`contact_pi_manual`** row and **no automated email draft is produced**. This is deliberate: at this stage the operator should step in personally — direct email from their own account, a phone call, or an in-person conversation with the PI — rather than send yet another template reminder.

Three outcomes are possible once the manual contact has been made:

- **PI responds with data + PID:** Set `done=2` on the row, paste the Zenodo DOI into `pid` (and URL if available). Closes as `CLOSED_DATA_ARCHIVED`. Same behavior as the generic full-closure shortcut.
- **PI responds but there will be no deposit:** Set `done=1` with no PID or URL. The archive closes as `CLOSED_EXCEPTION`. If `note` is filled in, your note is recorded; if left blank, the system records the default note *"No response after max reminders and manual PI contact; closed as non-compliant with OA policy."*
- **No response yet:** Leave `done=0`. The row stays and regenerates on the next sheet run until you act.

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

### Shortcuts: fast-track and full closure

Two shortcuts let you skip intermediate pipeline steps when you've already done the work:

**`done=1` with a PID or URL → fast-track to `OPEN_ZENODO_PUBLISHED`**

If you set `done=1` on any row and fill in the `pid` and/or `url` columns, the system skips straight to `OPEN_ZENODO_PUBLISHED` regardless of the current status or task code. Use this when you've already completed QA, created the Zenodo deposit, and published it — just record the result in one step.

Example: an `OPEN_ACTIVE` archive where you did everything at once. Set `done=1`, paste the Zenodo DOI in `pid` and the URL in `url`. The archive jumps directly to `OPEN_ZENODO_PUBLISHED`.

This does NOT apply to `remind_sent` or `qa_hold` rows — those are handled normally even if a PID is present.

**`done=2` → full closure (everything done, folder removed)**

Setting `done=2` means: "I did everything needed and the folder has been removed." The system closes the archive immediately:

- If a PID is present (either in the `pid` column or already recorded in the database) → `CLOSED_DATA_ARCHIVED`
- If no PID anywhere → `CLOSED_EXCEPTION` (with a warning)

Use `done=2` when you want to close out an archive in a single action without stepping through each intermediate status.

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
| `done` | `0` = not done, `1` = done, `2` = fully closed (see shortcuts below) |
| `pid` | Enter the dataset DOI/PID here (required for `zenodo_published`) |
| `url` | Enter the dataset URL here (optional) |
| `note` | Optional free-text note (recorded in audit log) |

## 8. Procedure

All operations are driven by the `oa` CLI. Run `oa --help` for the full command list. Every command accepts `--config PATH` and `--db PATH` overrides.

### 8.1 Regular scanning (daily or as-needed)

```bash
oa scan
```

Updates SQLite based on the current SharePoint folder tree:

* New folder → `OPEN_INACTIVE`
* Empty → non-empty transition → `OPEN_ACTIVE` and sets `became_active_at`
* Folder missing while status is still `OPEN_*` → flags integrity warning (see §9)

The scanner is read-only against the folder tree — it observes, never modifies.

### 8.2 Weekly operations (recommended cadence)

1. **`oa scan`** — pick up folder activity since the last run.
2. **`oa report`** — review `output/weekly_report.md`: new items, stuck items, reminders due, ready queue, integrity warnings.
3. **`oa sheet`** — regenerate `output/action_sheet.tsv` with the current pending tasks.
4. **`oa emails`** — generate reminder and completion email drafts into `output/email_drafts/`. Review, copy into your mail client, and send manually. (Archives at the manual-contact stage are deliberately *not* drafted here — see §8.4.)
5. **Perform the manual work** for tasks on the sheet:
   * QA review for `OPEN_ACTIVE` archives
   * Zenodo draft creation, validation, publish
   * Internal publication DB updates
   * SharePoint folder removal when complete (operator does this directly in SharePoint)
6. **Edit `action_sheet.tsv`** to record what you did — see §7 for the rules (set `done` to `1` or `2`, fill in `pid`/`url`/`note` where relevant, change `task_code` for branching decisions).
7. **`oa apply output/action_sheet.tsv`** — writes your changes to SQLite, appends audit events, and moves applied rows to `output/action_history.tsv`.

Steps 1–4 are regeneration; steps 5–7 are the real weekly work.

### 8.3 Email handling

* `oa emails` produces reminder drafts (from `templates/reminder.txt`) for every archive whose `next_reminder_at` is due **and** whose `reminder_count` is still below the manual-contact threshold — and completion drafts (from `templates/completion.txt`) for every archive at `OPEN_ZENODO_PUBLISHED`.
* Sending remains manual — `oa` never touches email directly.
* After sending, record each sent email on the next action sheet by setting `done=1` on its `remind_sent` row.

### 8.4 Final reminder: manual PI contact

When an archive has reached `reminder_count = max_reminders - 1`, the next `oa sheet` run replaces its `remind_sent` row with a `contact_pi_manual` row and `oa emails` **does not** generate a draft for it. At this point the operator must contact the PI directly (personal email, phone, in-person) rather than send another template. Once contact has been made, apply one of the outcomes described in §7 under "Final reminder: manual PI contact."

### 8.5 Reopening a closed archive

Rarely, a `CLOSED_*` archive needs to come back to life — e.g. the PI finally delivered data after a `CLOSED_EXCEPTION`. This is handled by a dedicated CLI command, **not** via the action sheet:

```bash
oa reopen <pub_id> --reason "<why>" [--to OPEN_ACTIVE|OPEN_INACTIVE]
```

The command:

* Transitions the archive back to an OPEN status. By default it auto-detects: `OPEN_ACTIVE` if the SharePoint folder currently contains files, `OPEN_INACTIVE` otherwise. Pass `--to` to override.
* Resets `reminder_count` to 0 and clears `last_notified_at` / `next_reminder_at`. For `OPEN_ACTIVE`, a fresh `next_reminder_at` is scheduled using `first_reminder_days`.
* Records a `reopened` event in the audit log with `--reason` as the note.
* Leaves any recorded `final_pid` / `final_url` intact — they remain part of the archive's history.

Reopens are rare and consequential, so they're deliberately kept off the action sheet and require an explicit `--reason`.

## 9. Integrity controls

* If a folder disappears but the archive is not `CLOSED_*`, the report flags:

  * “Missing folder but still OPEN” → manual reconciliation required.
* Closed items remain in SQLite permanently for lookup/audit.

## 10. Audit trail

Every applied action is recorded in an `events` table (timestamp, publication_id, action_code, old_status, new_status, user_note, pid/url if supplied).


