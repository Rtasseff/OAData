"""CLI entry point for the OA Archive Tracker."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from oa_tracker.config import load_config

app = typer.Typer(help="OA Archive Tracker — manage Open Access publication data archiving.")

ConfigOption = typer.Option(None, "--config", "-c", help="Path to config.toml")
DbOption = typer.Option(None, "--db", help="Override database path")


def _get_config(config_path: Optional[str], db_path: Optional[str] = None):
    cfg = load_config(
        config_path=Path(config_path) if config_path else None,
    )
    if db_path:
        cfg.database = Path(db_path).resolve()
    return cfg


@app.command()
def init(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Initialize the database and default config."""
    from oa_tracker.db import init_db

    cfg = _get_config(config, db)
    init_db(cfg.database)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.email_drafts_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Database initialized at {cfg.database}")
    typer.echo(f"Output directory: {cfg.output_dir}")


@app.command()
def scan(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Scan SharePoint folders and update the registry."""
    from oa_tracker.scanner import scan_folders

    cfg = _get_config(config, db)
    result = scan_folders(cfg)
    typer.echo("Scan complete:")
    typer.echo(result.summary)


@app.command()
def report(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Generate the weekly report."""
    from oa_tracker.report import generate_report

    cfg = _get_config(config, db)
    path = generate_report(cfg)
    typer.echo(f"Report generated: {path}")


@app.command()
def sheet(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Generate the operator action sheet (TSV)."""
    from oa_tracker.sheet import generate_sheet

    cfg = _get_config(config, db)
    path = generate_sheet(cfg)
    typer.echo(f"Action sheet generated: {path}")


@app.command(name="apply")
def apply_cmd(
    path: str = typer.Argument(..., help="Path to the action sheet TSV"),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Apply completed actions from the action sheet to the database."""
    from oa_tracker.actions import apply_actions

    cfg = _get_config(config, db)
    result = apply_actions(Path(path), cfg)
    typer.echo(result.summary)


@app.command()
def emails(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Generate email drafts (reminders + completion notices)."""
    from oa_tracker.emails import generate_emails

    cfg = _get_config(config, db)
    paths = generate_emails(cfg)
    if paths:
        typer.echo(f"Generated {len(paths)} email draft(s):")
        for p in paths:
            typer.echo(f"  {p}")
    else:
        typer.echo("No email drafts to generate.")


@app.command()
def action(
    pub_id: str = typer.Argument(..., help="Publication ID to act on"),
    task_code: str = typer.Argument(..., help="Task code (e.g. qa_pass, qa_hold, close_exception, zenodo_published)"),
    done: int = typer.Option(1, "--done", help="1 = apply the task; 2 = full closure"),
    pid: str = typer.Option("", "--pid", help="Dataset PID / DOI"),
    url: str = typer.Option("", "--url", help="Dataset URL"),
    note: str = typer.Option("", "--note", help="Free-text note, recorded in the audit log"),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Apply a single action to one archive without going through the action sheet.

    Use this for mid-week one-offs — a PI calls, you decide to move one
    archive forward or close it out. The semantics match what a single
    row on the action sheet would do (validate_transition, fast-track
    when a PID is supplied, done=2 full closure, qa_hold / remind_sent
    / contact_pi_manual special handling).
    """
    from oa_tracker import status as st
    from oa_tracker.actions import apply_single

    if done not in (1, 2):
        typer.echo(f"--done must be 1 or 2 (got {done})")
        raise typer.Exit(2)
    if task_code not in st.TASK_CODES:
        typer.echo(
            f"Unknown task_code {task_code!r}. Valid codes: "
            f"{', '.join(sorted(st.TASK_CODES))}"
        )
        raise typer.Exit(2)

    cfg = _get_config(config, db)
    result, old_status, new_status = apply_single(
        cfg, pub_id, task_code, done=done, pid=pid, url=url, note=note,
    )

    for w in result.warnings:
        typer.echo(f"Warning: {w}")
    for e in result.errors:
        typer.echo(f"Error: {e}")

    if result.errors:
        raise typer.Exit(1)
    if not result.applied:
        typer.echo("Nothing applied.")
        raise typer.Exit(1)

    if old_status != new_status:
        typer.echo(f"Applied {task_code} on {pub_id}: {old_status} → {new_status}")
    else:
        typer.echo(f"Applied {task_code} on {pub_id} (status unchanged: {old_status})")


@app.command()
def reopen(
    pub_id: str = typer.Argument(..., help="Publication ID to reopen"),
    reason: str = typer.Option(..., "--reason", help="Why the archive is being reopened (recorded in audit log)"),
    to: Optional[str] = typer.Option(None, "--to", help="Target OPEN status (default: auto-detect from folder state)"),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Reopen a CLOSED archive back to an OPEN status.

    Use this when a previously-closed archive needs to be worked again —
    e.g. the PI finally responded with data after a CLOSED_EXCEPTION.
    The reason is recorded in the audit log; reminder counters are reset.
    """
    from datetime import datetime, timedelta
    from oa_tracker import status as st
    from oa_tracker.db import (
        get_archive,
        get_connection,
        insert_event,
        upsert_archive,
    )

    cfg = _get_config(config, db)

    with get_connection(cfg.database) as conn:
        archive = get_archive(conn, pub_id)
        if archive is None:
            typer.echo(f"No archive found for {pub_id!r}")
            raise typer.Exit(1)

        old_status = archive["status"]
        if old_status not in st.CLOSED_STATUSES:
            typer.echo(f"Archive {pub_id} is {old_status}, not CLOSED — nothing to reopen.")
            raise typer.Exit(1)

        if to is not None:
            if to not in st.OPEN_STATUSES:
                typer.echo(f"--to {to!r} is not an OPEN status. Valid: {sorted(st.OPEN_STATUSES)}")
                raise typer.Exit(2)
            new_status = to
        else:
            folder = Path(archive["folder_path"])
            has_files = folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))
            new_status = st.OPEN_ACTIVE if has_files else st.OPEN_INACTIVE

        now = datetime.now().isoformat(timespec="seconds")
        updates: dict = {
            "publication_id": pub_id,
            "status": new_status,
            "reminder_count": 0,
            "last_notified_at": None,
            "next_reminder_at": None,
        }
        if new_status == st.OPEN_ACTIVE:
            updates["became_active_at"] = now
            updates["last_changed_at"] = now
            updates["next_reminder_at"] = (
                datetime.now() + timedelta(days=cfg.reminders.first_reminder_days)
            ).isoformat(timespec="seconds")

        upsert_archive(conn, **updates)
        insert_event(
            conn, pub_id, "reopened", old_status, new_status, "cli",
            note=reason,
        )

    typer.echo(f"Reopened {pub_id}: {old_status} → {new_status}")
    typer.echo(f"Reason: {reason}")


@app.command()
def status(
    pub_id: Optional[str] = typer.Argument(None, help="Publication ID to look up"),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Show status of one or all archives."""
    from oa_tracker.db import get_all_archives, get_archive, get_connection

    cfg = _get_config(config, db)
    with get_connection(cfg.database) as conn:
        if pub_id:
            archive = get_archive(conn, pub_id)
            if archive is None:
                typer.echo(f"No archive found for {pub_id!r}")
                raise typer.Exit(1)
            typer.echo(f"Publication: {archive['publication_id']}")
            typer.echo(f"  Status:        {archive['status']}")
            typer.echo(f"  First seen:    {archive['first_seen_at']}")
            typer.echo(f"  Active since:  {archive['became_active_at'] or '-'}")
            typer.echo(f"  Last seen:     {archive['last_seen_at']}")
            typer.echo(f"  Last changed:  {archive['last_changed_at'] or '-'}")
            typer.echo(f"  PID:           {archive['final_pid'] or '-'}")
            typer.echo(f"  URL:           {archive['final_url'] or '-'}")
            typer.echo(f"  Reminders:     {archive['reminder_count']}")
            typer.echo(f"  Missing folder:{' YES' if archive['unexpected_missing_folder'] else ' No'}")
            if archive.get("notes"):
                typer.echo(f"  Notes:         {archive['notes']}")
        else:
            archives = get_all_archives(conn)
            if not archives:
                typer.echo("No archives tracked yet.")
                return
            typer.echo(f"{'Publication ID':<25} {'Status':<35} {'PID'}")
            typer.echo("-" * 80)
            for a in archives:
                typer.echo(f"{a['publication_id']:<25} {a['status']:<35} {a.get('final_pid') or '-'}")
