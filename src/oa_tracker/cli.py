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
