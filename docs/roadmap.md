# OA Archive Tracker — Roadmap

This document is the **single source of truth** for the staged automation plan.
Other docs (`summary.md`, `techSpec.md`, `sop.md`) link here rather than
restating the plan, so there is one place to update when stages, scope, or
priorities change.

---

## Staged automation plan

### Stage 1 — Folder-based tracking + reminders + operator workflow (MVP)

**Status:** shipped, in active use.

**Goal:** get reliability and visibility fast, without depending on the internal
publication DB or the Zenodo API.

- **Input:** locally synced SharePoint "Publications Data" tree (one folder per
  publication; folder name = publication ID).
- **Core logic:**
  - empty folder → `OPEN_INACTIVE` (red flag: data contact hasn't acted)
  - non-empty folder → `OPEN_ACTIVE` (activity started; triggers manual QA)
- **System of record:** SQLite registry keeps all archives (open + closed),
  including "became active" date, last change, reminders, PID/URL, notes.
- **Operator layer:** generated Action Sheet TSV listing tasks; operator marks
  tasks done; script ingests it and updates SQLite (so SQLite is never edited
  by hand).
- **Outputs:** weekly report + reminder email drafts + completion email drafts.
- **Closure:** once Zenodo is published and the internal DB updated (manually
  for now), the folder can be removed and status becomes `CLOSED_*` (records
  remain in SQLite).

### Stage 2 — Read internal publication database

**Status:** in progress — connectivity testing; **blocked on IT host-grant** (as of 2026-05-05).

**Goal:** enrich and de-risk operations, without waiting on write access.
Sequenced ahead of Zenodo automation because article-level metadata (PI,
publication DOI, etc.) is needed to meaningfully fill out new Zenodo records
and to check whether a record already exists for a publication.

- Pull PI / data-contact details, publication DOI, metadata.
- Use DB info to improve reminder targeting and email-template filling.
- Cross-check expected folders vs observed folders.

#### Progress log

**2026-05-05 — Initial connectivity test**

Confirmed:
- IT granted read access via phpMyAdmin at
  `https://intranet.cicbiomagune.es/phpmyadmin/` (user-issued credentials).
- DB host/port reachable from the WSL workstation:
  `nc -vz intranet.cicbiomagune.es 3306` succeeded
  (resolves to `10.10.3.230`, port 3306 / native MySQL protocol).
- Local tooling installed: `mariadb-client` (system), `pymysql 1.1.3` in
  `.venv/`. PyMySQL chosen for its pure-Python, zero-system-deps fit with the
  project's lightweight ethos. Neither is committed to `pyproject.toml` yet —
  they're throwaway test deps until Stage 2 design begins.
- `~/.my.cnf` (mode 600) created on the workstation with the IT-issued user
  and password. Not in the repo.

Blocked:
- `mysql -e "SELECT VERSION();"` returns
  `ERROR 1045 (28000): Access denied for user 'rtasseff'@'bmg-rtasseff.cicbiomagune.int' (using password: YES)`.
- Credentials confirmed correct (same pair works via phpMyAdmin).
- Diagnosis: MySQL **host-grant restriction**. The user account is granted
  only from the phpMyAdmin host, not from the WSL workstation. Same root
  cause as the classic "phpMyAdmin works but direct mysql doesn't" gotcha.

Next action:
- Email IT requesting a grant for the same user from the workstation host
  (`'rtasseff'@'bmg-rtasseff.cicbiomagune.int'`) or, if hostname-based grants
  are awkward, the internal subnet (`'rtasseff'@'10.10.%'`). Same SELECT-only
  privilege scope as today.
- Once the grant is in place: re-run the `mysql` smoke test, then run the
  PyMySQL test against the same credentials. After both pass, capture
  schema (database name, relevant tables, `DESCRIBE` of each) into a scratch
  note and start the Stage 2 design plan (config schema, `pub_db.py` module,
  credential handling, dep promotion to `pyproject.toml`).

Fallback if IT cannot widen the grant:
- SSH tunnel from the workstation to a whitelisted host, connect to MySQL
  via the tunnel so the connection appears to originate from the allowed
  source. Adds a separate access ask (SSH on the intermediate host) and
  is less convenient for daily use; raise direct-grant first.

### Stage 3 — Zenodo automation (start with uploads)

**Status:** not started.

**Goal:** remove the most painful manual step (large-file drag/drop and Zenodo
UI errors). Builds on Stage 2 — uses publication metadata to populate Zenodo
record fields and detect pre-existing records.

- Automate uploads via the Zenodo API first (highest ROI).
- Later expand to create/edit draft records via API.
- **Enforce the DOI/PID rule:** the dataset must get a Zenodo-minted DOI; the
  paper DOI may only be linked as a related identifier in Zenodo metadata.

### Stage 4 — Write back to internal publication database

**Status:** not started; depends on IT access.

**Goal:** close the loop automatically (best case).

- Push Zenodo DOI/URL into the internal publication DB automatically.
- Potentially auto-transition `OPEN_ZENODO_PUBLISHED` → `OPEN_DB_UPDATED` →
  `CLOSED_DATA_ARCHIVED`.

---

## Cross-cutting / open ideas

Items that don't fit cleanly into a single stage. To be expanded over time —
this is the parking lot for "build out from there".

- _(none yet — add here as ideas come up)_

---

## Progress since MVP

Things shipped beyond the original Stage 1 scope, kept here so the roadmap
reflects reality:

- `oa action <pub_id> <task_code>` — single-archive mid-week updates without
  regenerating the action sheet.
- `done=1` with `pid`/`url` — fast-track to `OPEN_ZENODO_PUBLISHED` in one row.
- `done=2` — full-closure shortcut (`CLOSED_DATA_ARCHIVED` with PID,
  `CLOSED_EXCEPTION` without).
- `oa reopen <pub_id> --reason "..."` — bring a `CLOSED_*` archive back to an
  OPEN state when a PI finally responds.
- Manual-contact final-reminder flow — replaces the last templated reminder
  with a `contact_pi_manual` row (no draft generated).
