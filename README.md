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

Edit `config.toml` in the project root:

```toml
[paths]
sharepoint_root = "./data/publications"   # path to synced SharePoint folders
database = "./oa_tracker.sqlite"
output_dir = "./output"
email_drafts_dir = "./output/email_drafts"
template_dir = "./templates"

[reminders]
first_reminder_days = 14      # days after activation before first reminder
reminder_interval_days = 7    # days between subsequent reminders
max_reminders = 5
```

All paths are resolved relative to the project root unless absolute.

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

`oa action` applies one task to one archive without editing the action sheet — for mid-week one-offs (a PI calls, a single archive needs closure, etc.). It runs through the same validation and shortcut logic as `oa apply`.

General form:

```
oa action <PUB_ID> <TASK> [--done 1|2] [--pid PID] [--url URL] [--note "..."]
                          [--email ADDR] [--name NAME] [--code CODE]
```

Global flags:

- `--done 1` (default) applies the task. `--done 2` is the full-closure shortcut (everything done including folder removal — closes as `CLOSED_DATA_ARCHIVED` if a PID is on record, else `CLOSED_EXCEPTION`).
- Supplying `--pid` or `--url` on a pipeline task fast-tracks the archive to `OPEN_ZENODO_PUBLISHED` (except for `remind_sent` / `qa_hold`).

**Pipeline tasks** (each requires the matching current status — see [`docs/sop.md`](docs/sop.md) §7):

- `qa_pass` — approve QA on uploaded data → `OPEN_READY_FOR_ZENODO_DRAFT`
  - `oa action <PUB_ID> qa_pass [--note "..."]`
- `qa_hold` — flag a QA issue; stays `OPEN_ACTIVE`
  - `oa action <PUB_ID> qa_hold --note "why it's on hold"`
- `zenodo_draft_created` — mark Zenodo draft as created → `OPEN_ZENODO_DRAFT_CREATED`
  - `oa action <PUB_ID> zenodo_draft_created [--note "..."]`
- `zenodo_validated` — mark Zenodo draft as validated → `OPEN_ZENODO_DRAFT_VALIDATED`
  - `oa action <PUB_ID> zenodo_validated [--note "..."]`
- `zenodo_published` — record a hand-published Zenodo record → `OPEN_ZENODO_PUBLISHED`
  - `oa action <PUB_ID> zenodo_published --pid PID [--url URL] [--note "..."]`
- `db_updated` — internal publication DB updated → `OPEN_DB_UPDATED`
  - `oa action <PUB_ID> db_updated [--note "..."]`
- `folder_removed` — SharePoint folder removed; close → `CLOSED_DATA_ARCHIVED`
  - `oa action <PUB_ID> folder_removed [--note "..."]`

**API-backed Zenodo tasks** (need `[zenodo]` enabled in `config.toml`; they call the Zenodo API):

- `zenodo_create_draft` — create the draft via the API (metadata + reserved DOI) → `OPEN_ZENODO_DRAFT_CREATED`
  - `oa action <PUB_ID> zenodo_create_draft`
- `zenodo_upload_files` — upload the folder's package (`*.zip` + `README*.txt`) to the draft; status unchanged
  - `oa action <PUB_ID> zenodo_upload_files`
- `zenodo_publish` — publish the validated draft via the API; mints the DOI and records it → `OPEN_ZENODO_PUBLISHED`
  - `oa action <PUB_ID> zenodo_publish`

**Closures** (from any `OPEN_*` status):

- `close_publication_only` — no data deposit needed by mandate → `CLOSED_PUBLICATION_ONLY`
  - `oa action <PUB_ID> close_publication_only [--note "..."]`
- `close_exception` — closed with exception → `CLOSED_EXCEPTION`
  - `oa action <PUB_ID> close_exception --note "why this is an exception"`

**No-status-change tasks** (logged to the audit trail; counters / acknowledgments only):

- `remind_sent` — record that a reminder was sent (increments counter)
  - `oa action <PUB_ID> remind_sent [--note "..."]`
- `contact_pi_manual` — record the final manual PI contact
  - `oa action <PUB_ID> contact_pi_manual [--note "..."]`
- `mandate_missing` — acknowledge that the OA mandate could not be derived
  - `oa action <PUB_ID> mandate_missing [--note "..."]`

**Operator-managed overrides** (CLI-only; never appear on the action sheet):

- `set_data_contact` — set the data contact; marks it operator-managed so scans won't overwrite
  - `oa action <PUB_ID> set_data_contact --email ADDR [--name NAME]`
- `reset_data_contact` — clear the override; next scan re-seeds from the central DB
  - `oa action <PUB_ID> reset_data_contact`
- `set_zenodo_code` — record the Zenodo record id (numeric, not the DOI)
  - `oa action <PUB_ID> set_zenodo_code --code RECORD_ID`
- `reset_zenodo_code` — clear the override; next scan re-seeds from the central DB
  - `oa action <PUB_ID> reset_zenodo_code`

Examples:

```bash
# PI confirmed mid-week; accept the uploaded data as-is
oa action 3249 qa_pass --note "PI confirmed; accepting as-is"

# Close as exception by directive
oa action 3105 close_exception --note "Skipped per leadership directive"

# PI delivered data externally; record the PID and fast-track to OPEN_ZENODO_PUBLISHED
oa action 3097 qa_pass --pid 10.5281/zenodo.42 --url https://zenodo.org/records/42

# Already fully done including folder removal
oa action 3086 qa_pass --done 2 --pid 10.5281/zenodo.99

# Set a non-default data contact
oa action 3092 set_data_contact --email "real.contact@example.org" --name "Real Contact"

# Record the Zenodo record id right after creating the draft
oa action 3092 set_zenodo_code --code "10298471"
```

See [`docs/sop.md`](docs/sop.md) §7 for task-code semantics and §8.5 for the full `oa action` walkthrough.

## Documentation

This README is a high-level orientation. The canonical reference for how to operate the tool — status model, task codes, weekly procedure, final-reminder handling, reopening a closed archive — is [`docs/sop.md`](docs/sop.md).

| File | What's in it | Read when |
|------|--------------|-----------|
| [`docs/sop.md`](docs/sop.md) | Standard operating procedure — status model, task code reference, weekly workflow, final-reminder handling, reopening closed archives. **Start here for day-to-day operation.** | You're running the tool and want the full walkthrough. |
| [`docs/techSpec.md`](docs/techSpec.md) | Technical specification — database schema, status values, task codes, transition rules, and the reasoning behind them. | You're modifying the code, the schema, or the status pipeline. |
| [`docs/summary.md`](docs/summary.md) | Project context and motivation — why this tool exists, who uses it, and the non-technical background. | You're new to the project or need to explain it to someone else. |

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
config.toml              # User-editable settings
templates/               # Email templates (reminder.txt, completion.txt)
src/oa_tracker/
    cli.py               # Typer CLI entry point
    config.py            # TOML config loading
    status.py            # Status constants, transition rules
    db.py                # SQLite schema and queries
    scanner.py           # Folder tree scanning
    sheet.py             # Action sheet generation
    actions.py           # Action sheet parsing and application
    report.py            # Weekly report generation
    emails.py            # Email draft generation
tests/                   # pytest test suite
docs/                    # SOP, tech spec, project summary
```
