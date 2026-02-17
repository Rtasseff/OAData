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

All commands accept `--config` / `-c` and `--db` overrides.

## Weekly Workflow

1. **`oa scan`** — pick up new folders and folder activity changes.
2. **`oa report`** — review `output/weekly_report.md` for new items, stuck archives, reminders due, and integrity warnings.
3. **`oa sheet`** — generate `output/action_sheet.tsv`.
4. **Do manual work** — QA review, Zenodo draft/publish, internal DB update, folder removal.
5. **Edit the TSV** — set `done=1` on completed rows, paste PID/URL if relevant, add optional notes.
6. **`oa apply output/action_sheet.tsv`** — updates the database, logs events, moves applied rows to `action_history.tsv`.
7. **`oa emails`** — generate reminder and completion email drafts in `output/email_drafts/`.

## Status Pipeline

```
OPEN_INACTIVE  →  OPEN_ACTIVE  →  OPEN_READY_FOR_ZENODO_DRAFT
  →  OPEN_ZENODO_DRAFT_CREATED  →  OPEN_ZENODO_DRAFT_VALIDATED
  →  OPEN_ZENODO_PUBLISHED  →  OPEN_DB_UPDATED  →  CLOSED_DATA_ARCHIVED
```

Special closures from any OPEN status: `CLOSED_PUBLICATION_ONLY`, `CLOSED_EXCEPTION`.

See `docs/sop.md` for the full operating procedure and `docs/techSpec.md` for the technical specification.

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
