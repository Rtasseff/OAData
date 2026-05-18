# SOP: Open Access Archive Tracker (Ryan-side)

> Staged automation plan: see [roadmap.md](roadmap.md).

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

Any OPEN status can be closed directly as `CLOSED_PUBLICATION_ONLY` or `CLOSED_EXCEPTION` via the special actions in §7. A CLOSED archive can be reopened via the `oa reopen` command (see §8.6).

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
| `mandate_missing` | Confirm with PO/IT — mandate could not be derived | *(no status change; see §8.7)* |

### Stage-2 mandate-aware behavior

> Full classification rules — sources, queries, prefix list,
> aggregation logic, and how to extend when the webpage disagrees:
> see [mandate_classification.md](mandate_classification.md).


After connectivity to the central publication DB (Stage 2), each scan
caches per-archive OA-mandate flags on the `archives` row. The sheet
adapts its output based on the cached classification:

- **Data required** (any linked project signals data via `cff_oaMandate`
  type 1/2/5 or matches the Spanish AEI 2022+ pattern): the usual
  pipeline row appears, reminders are emitted when due. No special note.
- **Paper-only** (paper required but no data): the pipeline row still
  appears with an auto-populated note *"PAPER ONLY: data not required
  by mandate; processing as if data were required."* Reminders are
  suppressed (we don't pester data contacts for data the mandate
  doesn't require).
- **No OA** (mandate explicitly says nothing required): a
  `close_publication_only` row appears with an auto-note *"No OA
  mandate on linked project(s); no data archiving required."* No
  reminders.
- **Mandate missing** (we couldn't derive anything from the central
  DB): a `mandate_missing` row appears, asking you to confirm with
  PO/IT. No pipeline progression, no reminders.

Nothing closes automatically — every transition still goes through
your hand. The cache is just there to pre-populate the right row and
note, so the operator sheet is mandate-aware without removing the
operator from the loop.

### Stage-2 operator overrides (CLI-only)

Four override commands manage the operator-managed fields (separate
from the auto-refreshed cache). All are invoked via `oa action`:

```bash
oa action <pub_id> set_data_contact --email "x@y.org" [--name "Foo Bar"]
oa action <pub_id> reset_data_contact
oa action <pub_id> set_zenodo_code --code "12345"
oa action <pub_id> reset_zenodo_code
```

`set_*` marks the field as operator-managed; the next scan does not
overwrite it. `reset_*` clears that flag so the next scan re-seeds
from the central DB (corresponding author for the data contact, or
the Zenodo record code if the central DB lists Zenodo as the
repository). Each writes an event to the audit log with `source="cli"`.

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

Each scan also enriches every active archive from the central publication
DB (Stage 2): pulls title/DOI/journal, derives the OA-mandate flags
(`oa_data_required`, `oa_paper_required`, embargo, mandate trace, plus
a `mandate_missing` flag when no rule applies), and refreshes the
corresponding-author and central-repository fields. If the central DB
is unreachable the scan logs an error and continues with the previously
cached values — folder detection still works.

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

### 8.3 Email handling and Zenodo cheat sheets

* `oa emails` produces reminder drafts (from `templates/reminder.txt`) for every archive whose `next_reminder_at` is due **and** whose `reminder_count` is still below the manual-contact threshold — and completion drafts (from `templates/completion.txt`) for every archive at `OPEN_ZENODO_PUBLISHED`.
* Reminders are suppressed for archives whose central mandate is paper-only, no-OA, or missing (the operator sheet still flags those — we just don't pester data contacts for data the mandate doesn't require).
* Sending remains manual — `oa` never touches email directly.
* After sending, record each sent email on the next action sheet by setting `done=1` on its `remind_sent` row.
* `oa emails` also writes a **Zenodo cheat sheet** to `output/zenodo_cheat/<pub_id>.txt` for every archive in `OPEN_READY_FOR_ZENODO_DRAFT`, `OPEN_ZENODO_DRAFT_CREATED`, or `OPEN_ZENODO_DRAFT_VALIDATED`. The cheat sheet consolidates publication metadata, OA-mandate flags, data-contact info, the central DB's existing repository reference, and the operator-managed Zenodo code — everything needed to create a Zenodo deposit by hand. (Future Stage 2.5 will automate the Zenodo creation itself; the cheat sheet is the manual interim.)

#### When you create the Zenodo draft

After creating a Zenodo deposit by hand from a cheat sheet, **record
the deposit's record id back on the archive immediately**, before (or
together with) advancing the status. That's what makes the cheat sheet
on the *next* `oa emails` run show your new code under "Our Zenodo
code" — and what protects you from re-running `oa scan` and getting a
stale value if the central DB ever records a non-Zenodo repository for
the same publication.

Two equivalent ways:

```bash
# (a) one-shot via CLI:
oa action <pub_id> set_zenodo_code --code <numeric_record_id>
oa action <pub_id> zenodo_draft_created               # advance status

# (b) on the next weekly action sheet:
#     - run `oa action <pub_id> set_zenodo_code --code <id>` once
#       (the override flag persists across scans), then set done=1
#       on the existing zenodo_draft_created row.
```

The `--code` is the **numeric Zenodo record id** (e.g. `20268493`), not
the DOI. The DOI is `10.5281/zenodo.<code>` and is captured later via
the `final_pid` field when you mark `zenodo_published`. The
`zenodo_code` we track is set early so the operator-managed view of
"which Zenodo record does this publication belong to" is correct from
the moment the draft exists, not only after publication.

If you've already done *everything* — created the deposit, validated,
published, updated the internal DB, removed the folder — you can skip
all the intermediate rows and use the full-closure shortcut: edit any
row for the archive on the action sheet, set `done=2`, paste the
Zenodo DOI into `pid`, and apply. See §7 "Shortcuts" for the details.

### 8.4 Final reminder: manual PI contact

When an archive has reached `reminder_count = max_reminders - 1`, the next `oa sheet` run replaces its `remind_sent` row with a `contact_pi_manual` row and `oa emails` **does not** generate a draft for it. At this point the operator must contact the PI directly (personal email, phone, in-person) rather than send another template. Once contact has been made, apply one of the outcomes described in §7 under "Final reminder: manual PI contact."

### 8.5 Mid-week single-archive changes (`oa action`)

Most operator work flows through the weekly action sheet, but occasionally a single archive needs attention outside that cadence — a PI emails in response to a reminder and says "what's there is as good as it'll get, accept it," or a specific archive needs to be closed as an exception immediately without waiting for the next sheet regeneration. Use:

```bash
oa action <pub_id> <task_code> [--done 1|2] [--pid ...] [--url ...] [--note "..."]
```

The semantics match a single row on the action sheet:

* Runs through the same validation, fast-track, and full-closure logic as `oa apply`.
* Defaults to `--done 1` (apply the named task). Use `--done 2` for the full-closure shortcut.
* Supplying `--pid` or `--url` triggers the fast-track to `OPEN_ZENODO_PUBLISHED` (except for `remind_sent` and `qa_hold`), exactly as on the sheet.
* Logs an event to the audit table with `source="cli"` so the origin is clear.

Examples:

```bash
# PI confirmed mid-week that what's uploaded is final; accept it.
oa action 3249 qa_pass --note "PI confirmed; accepting as-is"

# Close an archive as an exception by directive.
oa action 3105 close_exception --note "Skipped per leadership directive"

# PI delivered data externally; record the PID and fast-track.
oa action 3097 qa_pass --pid 10.5281/zenodo.42 --url https://zenodo.org/records/42

# Already fully done including folder removal.
oa action 3086 qa_pass --done 2 --pid 10.5281/zenodo.99

# Stage-2 overrides for the operator-managed fields:
oa action 3092 set_data_contact --email "real.contact@example.org" --name "Real Contact"
oa action 3092 reset_data_contact                           # let next scan re-seed
oa action 3092 set_zenodo_code --code "10298471"            # record the Zenodo record id immediately
                                                            # after creating the draft (see §8.3)
oa action 3092 reset_zenodo_code                            # clear override

# Acknowledge a mandate_missing row after asking the project office:
oa action 3092 mandate_missing --note "Confirmed with Nerea — really has no mandate"
```

`oa action` does **not** modify `action_sheet.tsv` (it's a side-channel). If the archive has a pending row on the sheet, that row will simply be skipped on the next `oa apply` because the transition no longer applies, or it will be regenerated correctly on the next `oa sheet`.

### 8.6 Reopening a closed archive

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

### 8.7 Mandate-missing investigation

When the scanner cannot derive any OA mandate for an archive's linked
projects (no `cff_oaMandate` row populated and no project_code matching
the Spanish AEI 2022+ pattern), `oa sheet` emits a single
`mandate_missing` row for that archive and `oa emails` does not draft a
reminder. The archive is also listed under "Mandate Issues — confirm
with PO/IT" in the weekly report.

This isn't a bug — it usually means the publication shouldn't have
ended up in our system at all (upstream data quality issue). Three
operator responses:

- **Leave `done=0`** on the row. The row regenerates next scan; if the
  upstream DB gets fixed and a mandate becomes derivable, the row
  disappears on its own.
- **`oa action <pub_id> mandate_missing --note "..."`** records an audit
  entry that you investigated; the row will still regenerate next scan
  until the situation actually changes. Useful for tracking your own
  follow-ups with PO/IT.
- **Change `task_code` to `close_exception` with a note**, then
  `done=1`. The archive closes as `CLOSED_EXCEPTION` and the row
  doesn't come back.

## 9. Integrity controls

* If a folder disappears but the archive is not `CLOSED_*`, the report flags:

  * “Missing folder but still OPEN” → manual reconciliation required.
* Closed items remain in SQLite permanently for lookup/audit.

## 10. Audit trail

Every applied action is recorded in an `events` table (timestamp, publication_id, action_code, old_status, new_status, user_note, pid/url if supplied).


