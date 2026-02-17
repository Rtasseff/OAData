# CLAUDE.md — Project Instructions for Claude Code

## Project Overview

OA Archive Tracker (`oa`) — a CLI tool for tracking Open Access publication data archiving at CIC biomaGUNE. Python 3.12, raw sqlite3, Typer CLI, TOML config.

## Critical Safety Rule

**NEVER write to, modify, delete, or move any file or directory under `/mnt/c/`.** This path contains the Windows filesystem including the live SharePoint-synced publication folders that other people actively use. The scanner reads from this path — that is the only permitted interaction. All writes (database, reports, action sheets, email drafts) stay within the project directory under `/home/`.

If a task would require modifying anything on `/mnt/c/`, stop and ask the user to do it manually.

## Tech Stack

- Python 3.12+ (stdlib `sqlite3`, `tomllib`, `csv`, `pathlib`, `string.Template`)
- Typer (only external runtime dependency)
- pytest (dev dependency)
- Virtual environment at `.venv/`

## Project Layout

```
config.toml          — user settings (paths, reminder schedule)
templates/           — email templates (reminder.txt, completion.txt)
src/oa_tracker/
    cli.py           — Typer app: oa init/scan/report/sheet/apply/emails/status
    config.py        — load config.toml, resolve paths, Config dataclass
    status.py        — status constants, TRANSITIONS dict, validate_transition()
    db.py            — SQLite schema, get_connection(), CRUD helpers
    scanner.py       — walk sharepoint_root, detect new/active/missing folders
    sheet.py         — generate action_sheet.tsv from DB state
    actions.py       — parse TSV, validate transitions, apply to DB
    report.py        — generate weekly_report.md
    emails.py        — generate email drafts from templates
tests/
    conftest.py      — shared fixtures (tmp_db, tmp_sharepoint, test_config)
    test_*.py        — one test file per module
docs/
    summary.md       — project context and motivation
    techSpec.md      — technical spec (schema, statuses, task codes, transitions)
    sop.md           — standard operating procedure
```

## Key Design Decisions

- Operator never edits SQLite directly; all changes go through `action_sheet.tsv` → `oa apply`.
- Applied action rows are moved to `action_history.tsv` and removed from the active sheet.
- `string.Template` is used for email templates (`${placeholder}` syntax).
- The scanner is read-only against the folder tree — it only observes, never modifies.
- `folder_removed` without a PID on record closes as `CLOSED_EXCEPTION` with a warning.
- Zenodo PIDs are checked heuristically; a DOI without "zenodo" triggers a warning.

## Status Pipeline

```
OPEN_INACTIVE → OPEN_ACTIVE → OPEN_READY_FOR_ZENODO_DRAFT
→ OPEN_ZENODO_DRAFT_CREATED → OPEN_ZENODO_DRAFT_VALIDATED
→ OPEN_ZENODO_PUBLISHED → OPEN_DB_UPDATED → CLOSED_DATA_ARCHIVED
```

Wildcard closures from any OPEN status: `CLOSED_PUBLICATION_ONLY`, `CLOSED_EXCEPTION`.

Transitions are defined in `src/oa_tracker/status.py` (`TRANSITIONS` dict).

## Running Tests

```bash
source .venv/bin/activate
pytest
```

All tests use temporary directories and databases (via `tmp_path` fixtures). Tests should never touch the real database or SharePoint folders.

## Common Tasks

- **Adding a new status or task code**: update `src/oa_tracker/status.py` (constants, `TRANSITIONS`, `TASK_CODES`, `next_task_for_status`), then update `tests/test_sheet.py` and `tests/test_actions.py`.
- **Changing the DB schema**: update `_SCHEMA_SQL` in `src/oa_tracker/db.py` and bump `_SCHEMA_VERSION`.
- **Modifying email templates**: edit files in `templates/`. Placeholders use `${name}` syntax.
- **Adding a CLI command**: add to `src/oa_tracker/cli.py` using the Typer `@app.command()` pattern. Use lazy imports inside the function body.
