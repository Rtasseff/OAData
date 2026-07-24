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
* **Package (decision 2026-07-02; manuscript rule added 2026-07-15):** one
  `.zip` of the datasets **plus a `README.txt` as its own file next to the
  zip** — one canonical location, chosen because the standalone README
  uploads to Zenodo as a browser-readable file and can be QC'd in
  SharePoint without extracting anything. (A copy inside the zip is
  welcome but not the requirement.) Detection is deliberately lenient —
  the scanner also accepts a README found only inside the zip, so nobody
  is bounced on a technicality — but all instructions and reminder texts
  ask for the sibling file. **Plus (updated archiving rules): a version of
  the manuscript — a pre-print, i.e. the pre-submission version — as
  `.doc`/`.docx`/`.pdf`, a third file NOT inside the zip.** The scanner
  only accepts it beside the zip; auto-QC holds without it. The pre-print
  is **part of the Zenodo deposit** and uploads with the package
  (decision 2026-07-15: the pre-submission version was never peer-reviewed
  or transferred to a journal, so publishing it on an open repository is
  within our rights). The user-facing protocol docx should say the same
  (see its §2.4/§4).

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
| `OPEN_READY_FOR_ZENODO_DRAFT` | `zenodo_draft_created` | Create Zenodo draft deposit **by hand** and record it | `OPEN_ZENODO_DRAFT_CREATED` |
| `OPEN_READY_FOR_ZENODO_DRAFT` | `zenodo_create_draft` | **API:** done=1 creates the draft (metadata + reserved DOI) | `OPEN_ZENODO_DRAFT_CREATED` |
| `OPEN_ZENODO_DRAFT_CREATED` | `zenodo_upload_files` | **API:** upload the package files to the draft | *(stays OPEN_ZENODO_DRAFT_CREATED)* |
| `OPEN_ZENODO_DRAFT_CREATED` | `zenodo_validated` | Review the draft on Zenodo and **click Publish there**; done=1 confirms it | `OPEN_ZENODO_PUBLISHED` *(system-made draft — DOI/URL auto-recorded)* · `OPEN_ZENODO_DRAFT_VALIDATED` *(hand-made draft)* |
| `OPEN_ZENODO_DRAFT_VALIDATED` | `zenodo_published` | Record a **hand-published** Zenodo record (enter PID and URL) | `OPEN_ZENODO_PUBLISHED` |
| `OPEN_ZENODO_DRAFT_VALIDATED` | `zenodo_publish` | **API:** done=1 publishes the draft and mints the DOI | `OPEN_ZENODO_PUBLISHED` |
| `OPEN_ZENODO_PUBLISHED` | `db_updated` | Update internal publication DB with dataset DOI/URL | `OPEN_DB_UPDATED` |
| `OPEN_DB_UPDATED` | `folder_removed` | Confirm SharePoint folder removed; close archive | `CLOSED_DATA_ARCHIVED` |

The **API** codes appear on the sheet automatically when `[zenodo]` is
enabled in `config.toml`; when a draft was made by hand or lives on a
different Zenodo environment than the config, the manual codes appear
instead. `zenodo_publish` is the deliberate human keystroke that mints
the permanent DOI — it is never applied automatically.

### Publishing a Zenodo draft (review → publish → confirm)

Publishing is always your deliberate act. For a draft **the system
created** (the common case — it created it, so it holds the record id),
you never re-type identifiers we already have:

1. Open the draft on Zenodo (the review link is in the `zenodo_validated`
   row / the digest) and check it.
2. If it's good, **click Publish on Zenodo** — you see it go live exactly
   as you reviewed it. This mints the reserved DOI (same value; the
   `10.5281/zenodo.<id>` reserved at draft creation *is* the minted DOI)
   and the URL flips from `…/uploads/<id>` to `…/records/<id>`.
3. Set **`done=1`** on the `zenodo_validated` row (leave `pid`/`url`
   blank). `oa apply` verifies the record is actually published on Zenodo,
   then **auto-records the minted DOI in `final_pid` and the `/records/`
   URL in `final_url`**, and advances straight to `OPEN_ZENODO_PUBLISHED`.
   No hand entry; no second publish step.
   - Safety: if you set `done=1` **before** actually clicking Publish, the
     apply refuses with *"record … is not published yet — publish it on
     Zenodo, then re-apply."* Nothing is invented.

Manual entry is required **only** for a draft the system did **not**
create (a hand-made deposit — no record id on file). There the two-step
manual path applies: `zenodo_validated` → `OPEN_ZENODO_DRAFT_VALIDATED`,
then `zenodo_published` with the DOI in `pid` and the record URL in `url`.
(The API-publish code `zenodo_publish` — where the tool clicks Publish for
you — remains available for a system draft if you'd rather not publish in
the UI; don't do both, or the second publish errors.)

**After publish**, the archive is `OPEN_ZENODO_PUBLISHED` carrying the
final DOI/URL, and the terminal manual chain follows: `db_updated` (enter
the DOI/URL into the internal publication DB) → `folder_removed` (remove
the SharePoint folder) → closes as `CLOSED_DATA_ARCHIVED`.

### Special actions (available from any OPEN status)

| task_code | task_text | resulting status |
|---|---|---|
| `remind_sent` | Send reminder email to data contact | *(no status change; updates reminder count)* |
| `contact_pi_manual` | MAX reminder reached; manually contact PI | *(see "Final reminder" below)* |
| `handover_sent` | Send handover notice to the new data contact | *(no status change; clears the pending handover — see §8.1b)* |
| `completion_sent` | Send completion email to the data contact (data archived) | *(no status change; recurs while the archive is published-and-open until `done=1` logs it as sent)* |
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

The tool sends `max_reminders - 1` automated reminder emails. Once an archive has reached that count (i.e. one slot remains), the next sheet generation replaces the usual `remind_sent` row with a **`contact_pi_manual`** row. At this stage the operator should step in personally — direct email from their own account, a phone call, or an in-person conversation with the PI — rather than send yet another template reminder.

To support that personal contact, `oa emails` still produces a draft: the **past-due variant** (`reminder_<pub>_<n>_PASTDUE.eml`), the normal reminder marked "PAST DUE" in the subject and body. Use it as-is, as a skeleton for a personalized email, or just as the fact sheet for a face-to-face conversation. The sheet row's `note` names the file.

Outcomes once the manual contact has been made:

- **PI responds with data + PID:** Set `done=2` on the row, paste the Zenodo DOI into `pid` (and URL if available). Closes as `CLOSED_DATA_ARCHIVED`. Same behavior as the generic full-closure shortcut.
- **Contact made, still waiting on the PI:** Set `done=1` with no PID or URL (put what was said/agreed in `note`). This does **not** close anything: the contact is logged, the reminder count ticks up, and the item re-queues at the normal reminder interval — the `contact_pi_manual` row and a fresh past-due draft come back until the data arrives. PIs who promise and stall stay on the hook automatically.
- **Definitive no-deposit decision:** Change the row's `task_code` to `close_exception`, add a `note` explaining why, set `done=1`. Closing is always this explicit decision — it never happens as a side effect of logging a contact.
- **No response yet:** Leave `done=0`. The row stays and regenerates on the next sheet run until you act.

### How to edit the action sheet

The sheet is generated with one row per archive showing the next expected action. **You only need to edit three columns:** `done`, `pid`/`url` (when applicable), and `note`. Leave all other columns as-is.

- **To complete an action:** Set `done=1`. The system applies the `task_code` already in the row.
- **To provide data:** Fill in `pid` and/or `url` (e.g. when marking `zenodo_published`).
- **To add context:** Write in the `note` column. Notes are preserved in the audit log.
- **To take an alternate action:** Change `task_code` (and optionally `task_text`) before setting `done=1`. This is mainly relevant at the QA step — see below.

### Row ordering rule for OPEN_ACTIVE archives

When an archive is `OPEN_ACTIVE` with a reminder due, the sheet emits
**both** a `qa_pass` row and a `remind_sent` row — and they're emitted
in that order on purpose. **QA is checked before any reminder fires.**

- If QA passes (`qa_pass` with `done=1`), the archive advances to
  `OPEN_READY_FOR_ZENODO_DRAFT` and the `remind_sent` row below
  becomes moot. `oa apply` will detect that the status moved and
  **skip the reminder with a warning** instead of incrementing the
  reminder counter on an archive that's no longer waiting for data.
- If QA fails (change `task_code` from `qa_pass` to `qa_hold` with
  `done=1`), the archive stays `OPEN_ACTIVE` and the `remind_sent`
  row applies normally on the next line — exactly the "QA failed,
  data contact needs another nudge" pattern.

This matters for eventual automation: a top-down read of the sheet
encounters QA before the reminder, so the right decision (do QA
first) is the obvious one.

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

### 8.1b Automated operation (`oa auto`) — the standing cadence

With `[automation]` enabled, a scheduled `oa auto` run (cron or Windows
Task Scheduler → `scripts/run_auto.sh`) does the whole regeneration cycle
unattended and advances what it safely can:

1. `scan` — folder states, package detection (`.zip` + `README.txt` +
   manuscript `.doc`/`.docx`/`.pdf`), pub-DB enrichment.
2. SharePoint pull — records each contact's "I think this is done" tick;
   auto-applies promoted signals (data-contact reassignments, categorized
   exemptions with evidence, notes); routes everything else to
   `output/sharepoint_proposals.tsv` as before. An auto-applied
   data-contact reassignment also queues a **handover notice**: `oa
   emails` writes `email_drafts/handover_<pub>.eml` — addressed to the
   new contact, modeled on the standard notice, with a line naming the
   previous contact who handed over — and the action sheet carries a
   `handover_sent` row (with the file path in its note) until you send
   the email and mark the row `done=1`. The digest lists both the
   reassignment and the drafted notice.
3. Advance — auto-QC (done tick + complete package incl. manuscript +
   data-required mandate → `qa_pass`), then Zenodo draft with reserved
   DOI + package upload (stops at `OPEN_ZENODO_DRAFT_CREATED`), and
   closure of `OPEN_DB_UPDATED` archives whose folder you already
   removed.
4. SharePoint push + closed-row reconcile, then fresh
   sheet / email drafts / weekly report.
5. Digest → `output/auto_digest.md` — **this is the one file to read
   when you sit down**: what was done automatically, what needs your
   decision, and the pipeline states only you can advance.

Your weekly manual session shrinks to: read the digest → validate any
Zenodo drafts in the browser (link is in the sheet row/digest) →
`done=1` on `zenodo_validated` and `zenodo_publish` rows → update the
internal DB (`db_updated`) → remove finished folders in SharePoint →
send the generated `.eml` drafts → `oa apply`.

Never automated: Zenodo publish (permanent DOI), the QC judgement when
the done-tick and the folder package disagree (both mismatch directions
are flagged on the sheet and in the reminder text), free-text "Other"
exemptions, and anything on a placeholder archive (no central-DB
metadata to build a record from).

Large packages: files above `[zenodo] multipart_threshold_mb` try
Zenodo's multipart transfer (per-part retry — a mid-transfer drop
costs one ~200 MB part, not the file). **As of 2026-07-04 Zenodo
denies the part uploads (403)** — the code detects this and falls
back automatically; multipart activates by itself the day Zenodo
enables it (see zenodo_design.md § Large files). Until then, fallback
files above `single_put_max_mb` (5 GB) are deferred to you: the
digest carries the manual path — upload by hand to the
already-created draft, then `oa action <pub_id> zenodo_upload_files`
records it (checksum match, no bytes re-sent). The same manual path
appears when smaller uploads keep failing across runs. Deposits over
Zenodo's 50 GB/record cap are refused up front — split them or
contact Zenodo support.

Zenodo credentials: `~/.zenodorc` (mode 600), sections `[zenodo]`
(production) and `[zenodo-sandbox]`, each with `token = ...`. The
environment is chosen by `[zenodo] environment` in `config.toml` —
sandbox first, production after the drafts have been inspected.

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

* `oa emails` produces reminder drafts (from `templates/reminder.txt`) for every archive whose `next_reminder_at` is due **and** whose `reminder_count` is still below the manual-contact threshold — and completion drafts (from `templates/completion.txt`) for every archive that is published-and-open (`OPEN_ZENODO_PUBLISHED` or `OPEN_DB_UPDATED`) and hasn't been marked sent, plus a short window after closure to catch the `done=2` shortcut. Every such archive also carries a **`completion_sent`** row on the action sheet — the "send it, then tick it" companion to the draft, mirroring `remind_sent`/`handover_sent`. The row and the draft both stop recurring once you set `done=1` on the `completion_sent` row.
* Reminders are suppressed for archives whose central mandate is paper-only, no-OA, or missing (the operator sheet still flags those — we just don't pester data contacts for data the mandate doesn't require).
* Sending remains manual — `oa` never touches email directly.
* After sending, record each sent email on the next action sheet by setting `done=1` on its `remind_sent` row (reminders) or `completion_sent` row (completion emails).
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

When an archive has reached `reminder_count = max_reminders - 1`, the next `oa sheet` run replaces its `remind_sent` row with a `contact_pi_manual` row, and `oa emails` generates the **past-due** draft variant (`reminder_<pub>_<n>_PASTDUE.eml` — the normal reminder marked PAST DUE) as material for a personal follow-up rather than another automated send. Contact the PI directly (personal email, phone, in-person). `done=1` logs the contact and re-queues the item at the next reminder interval — it keeps coming back until the data arrives or you explicitly close (`close_exception` with a note, or `done=2` + PID). Details in §7 under "Final reminder: manual PI contact."

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


