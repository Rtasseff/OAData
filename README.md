# OA Archive Tracker

CLI tool for tracking Open Access publication data archiving workflows at CIC biomaGUNE.

The `oa` command monitors locally-synced SharePoint publication folders, maintains a SQLite registry, generates weekly reports and action sheets, and produces email drafts. The operator interacts via a TSV action sheet — never editing SQLite directly.

## Requirements

- Python 3.12+

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This registers the `oa` CLI command.

## Quick Start

```bash
# 1. Initialize database and output directories
oa init

# 2. Edit config.toml to set sharepoint_root to your synced folder path

# 3. Scan folders to populate the registry
oa scan

# 4. Generate the action sheet
oa sheet

# 5. Open output/action_sheet.tsv, set done=1 for completed tasks,
#    fill in pid/url/note as needed

# 6. Apply your changes
oa apply output/action_sheet.tsv

# 7. Generate the weekly report
oa report

# 8. Generate email drafts
oa emails
```

## Configuration

Everything lives in `config.toml` at the project root, which is
self-documenting (each section carries its own comments). Sections:
`[paths]` (folder/DB/output locations), `[reminders]` (cadence),
`[sharepoint]` (List sync), `[zenodo]` (API environment + defaults),
`[automation]` (per-signal auto-apply gates for `oa auto`), `[email]`
(sender identity + draft format). Paths are resolved relative to the
project root unless absolute.

Credentials never live in the repo: central-DB password in `~/.my.cnf`,
Zenodo tokens in `~/.zenodorc`, Microsoft refresh-token cache in
`~/.oa_sharepoint_token.json` (all mode 600).

## Commands

| Command | Description |
|---------|-------------|
| `oa init` | Initialize database and output directories |
| `oa scan` | Scan SharePoint folders, detect new/active/missing |
| `oa sheet` | Generate `action_sheet.tsv` with pending tasks |
| `oa apply <path>` | Apply completed actions from the TSV to the database |
| `oa report` | Generate `weekly_report.md` |
| `oa emails` | Generate email drafts (reminders + completion notices) |
| `oa status [PUB_ID]` | Show status of one or all archives |
| `oa action <PUB_ID> <TASK> [...]` | Apply a single task to one archive without editing the sheet |
| `oa reopen <PUB_ID> --reason "..."` | Reopen a CLOSED archive back to an OPEN status |
| `oa auto` | Full unattended cycle for cron: scan → SharePoint sync → auto-advance → sheet/emails/report → `output/auto_digest.md` (see `scripts/run_auto.sh`) |

All commands accept `--config` / `-c` and `--db` overrides.

### Single-archive actions (`oa action`)

`oa action <PUB_ID> <TASK> [--done 1|2] [--pid] [--url] [--note] [--email] [--name] [--code]`
applies one task to one archive without editing the action sheet — for
mid-week one-offs. It runs through the same validation and shortcut logic
as `oa apply`.

Task-code families (one-line orientation only — **semantics, flags, and
worked examples live in [`docs/sop.md`](docs/sop.md) §7 and §8.5**, the
single reference for operating rules):

| Family | Codes |
|--------|-------|
| Pipeline (manual steps) | `qa_pass`, `qa_hold`, `zenodo_draft_created`, `zenodo_validated`, `zenodo_published`, `db_updated`, `folder_removed` |
| Zenodo API (needs `[zenodo]` enabled; done=1 performs the call) | `zenodo_create_draft`, `zenodo_upload_files`, `zenodo_publish` |
| Closures (any OPEN status) | `close_publication_only`, `close_exception`, `close_archived_external` |
| Audit-only acknowledgments | `remind_sent`, `contact_pi_manual`, `mandate_missing`, `propose_*`, `user_note` |
| CLI-only overrides (survive scans) | `set/reset_data_contact`, `set/reset_zenodo_code`, `set/reset_corresponding_author` |

## Documentation map

This README is high-level orientation only. **This table is the single
map of which document owns what** — each topic has exactly one canonical
home; everything else links to it rather than restating it.
(`CLAUDE.md` at the project root holds the agent-facing instructions —
safety rules, conventions, common tasks — and is not operator reading.)

| File | Canonical for | Read when |
|------|--------------|-----------|
| [`docs/rollout_checklist.md`](docs/rollout_checklist.md) | **Current working checklist** — sandbox validation, pending decisions, IT asks, cleanup. Temporary; delete when done. | **Right now**, until the automation rollout is finished. |
| [`docs/sop.md`](docs/sop.md) | **Operating the tool** — status model, task-code semantics, `oa auto` cadence, weekly session, final-reminder handling, reopening. | You're running the tool. **Start here.** |
| [`docs/techSpec.md`](docs/techSpec.md) | **Internals** — DB schema (per version), transition rules, apply semantics, CLI surface. Points at `db.py` / `status.py` as the executable truth. | You're changing code or schema. |
| [`docs/roadmap.md`](docs/roadmap.md) | **The plan and its history** — stages, decisions, dated progress log. | You want to know why something is the way it is, or what's next. |
| [`docs/zenodo_design.md`](docs/zenodo_design.md) | **Zenodo integration design** (Stages 2.5/3) + implementation deltas. | You're touching `zenodo.py` or the metadata rules. |
| [`docs/sharepoint_list_design.md`](docs/sharepoint_list_design.md) | **SharePoint List track design** — columns, views, identity mapping, action routing. | You're touching `sharepoint.py` or the List. |
| [`docs/mandate_classification.md`](docs/mandate_classification.md) | **How OA mandates are derived** from the central DB. | A mandate flag looks wrong. |
| [`docs/email_from_office_address.md`](docs/email_from_office_address.md) | **Sending from the Project Office address** — the exact IT ask (EAC + PowerShell, doc-cited), operator setup, future Graph auto-send notes. | You're setting up or debugging office-address sending. |
| [`docs/summary.md`](docs/summary.md) | Non-technical project context for outsiders. | Explaining the project to someone new. |
| `publication_archive_protocol.docx` | **The user-facing protocol** (what data contacts are told). Maintained in Word/SharePoint; the SOP and reminder texts must stay consistent with it. | Changing what we ask users to do. |
| [`docs/pub_db_access_handoff.md`](docs/pub_db_access_handoff.md), [`docs/sharepoint_demo_runbook.md`](docs/sharepoint_demo_runbook.md) | Historical/aux references (DB access handoff; List demo walkthrough). | Rarely. |

At a glance, the OPEN → CLOSED pipeline is:

```
OPEN_INACTIVE  →  OPEN_ACTIVE  →  OPEN_READY_FOR_ZENODO_DRAFT
  →  OPEN_ZENODO_DRAFT_CREATED  →  OPEN_ZENODO_DRAFT_VALIDATED
  →  OPEN_ZENODO_PUBLISHED  →  OPEN_DB_UPDATED  →  CLOSED_DATA_ARCHIVED
```

Wildcard closures from any OPEN status: `CLOSED_PUBLICATION_ONLY`, `CLOSED_EXCEPTION`. See [`docs/sop.md`](docs/sop.md) §6 for what each status means and §7 for how to move between them.

## Testing

```bash
pip install pytest
pytest
```

## Project Structure

```
config.toml              # User-editable settings (self-documenting)
templates/               # Email templates (reminder, completion, zenodo cheat)
scripts/run_auto.sh      # Cron wrapper for `oa auto` (flock + logging)
src/oa_tracker/
    cli.py               # Typer CLI entry point
    config.py            # TOML config loading
    status.py            # Status constants, task codes, transition rules
    db.py                # SQLite schema (versioned migrations) and queries
    scanner.py           # Folder tree scanning + package detection
    sheet.py             # Action sheet generation
    actions.py           # Action application (incl. Zenodo API task codes)
    report.py            # Weekly report generation
    emails.py            # Email draft generation
    pub_db.py            # Read-only central publication DB (MariaDB) access
    sharepoint.py        # SharePoint List sync via Microsoft Graph
    zenodo.py            # Zenodo API client + metadata builder
    auto.py              # Unattended automation engine (`oa auto`)
tests/                   # pytest test suite
docs/                    # See "Documentation map" above
```
