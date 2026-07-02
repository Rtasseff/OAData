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
    cli.py           — Typer app: oa init/scan/report/sheet/apply/emails/status/auto
    config.py        — load config.toml, resolve paths, Config dataclass
    status.py        — status constants, TRANSITIONS dict, validate_transition()
    db.py            — SQLite schema, get_connection(), CRUD helpers
    scanner.py       — walk sharepoint_root, detect new/active/missing folders + package (.zip/README)
    sheet.py         — generate action_sheet.tsv from DB state
    actions.py       — parse TSV, validate transitions, apply to DB (incl. Zenodo API codes)
    report.py        — generate weekly_report.md
    emails.py        — generate email drafts from templates
    zenodo.py        — Zenodo InvenioRDM API client + metadata builder (Stages 2.5/3)
    auto.py          — unattended automation engine behind `oa auto` (cron entry point)
tests/
    conftest.py      — shared fixtures (tmp_db, tmp_sharepoint, test_config)
    test_*.py        — one test file per module
docs/
    summary.md            — project context and motivation
    techSpec.md           — technical spec (schema, statuses, task codes, transitions)
    sop.md                — standard operating procedure
    roadmap.md            — single source of truth for the staged automation plan (Stages 1, 2, 2.5, 3, 4, plus the parallel SharePoint List track)
    mandate_classification.md — how Stage 2 derives OA mandates from the central DB
    zenodo_design.md      — Stage 2.5 / 3 design (API surface, metadata mapping, module layout)
    sharepoint_list_design.md — Parallel track design (List columns, views, identity mapping, propose_* action routing, sync module)
```

## Key Design Decisions

- Operator never edits SQLite directly; all changes go through `action_sheet.tsv` → `oa apply`. New code paths and new input sources also route through the action sheet at the start (validation phase), then graduate to auto-apply once an operator has seen them behave correctly — see `feedback_no_auto_state_changes.md` in the memory store.
- Promoted auto-apply classes run via `oa auto` (`auto.py`), gated per class in `[automation]`; every automatic action uses the same `apply_single` path with `source="auto"` in the audit log. Zenodo publish and disputed QC decisions are never automated.
- Zenodo work targets the current InvenioRDM API (`/api/records`); the dataset's DOI is reserved at draft creation and the paper DOI only ever appears as an `ispublishedin` related identifier. Token in `~/.zenodorc` (never in the repo). Sandbox-first: `zenodo_env` on the archive pins which instance a draft lives on.
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
