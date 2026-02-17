"""CLI entry point for the OA Archive Tracker."""

import typer

app = typer.Typer(help="OA Archive Tracker — manage Open Access publication data archiving.")


@app.command()
def init():
    """Initialize the database and default config."""
    typer.echo("init: not yet implemented")


@app.command()
def scan():
    """Scan SharePoint folders and update the registry."""
    typer.echo("scan: not yet implemented")


@app.command()
def report():
    """Generate the weekly report."""
    typer.echo("report: not yet implemented")


@app.command()
def sheet():
    """Generate the operator action sheet (TSV)."""
    typer.echo("sheet: not yet implemented")


@app.command()
def apply(path: str = typer.Argument(..., help="Path to the action sheet TSV")):
    """Apply completed actions from the action sheet to the database."""
    typer.echo("apply: not yet implemented")


@app.command()
def emails():
    """Generate email drafts (reminders + completion notices)."""
    typer.echo("emails: not yet implemented")


@app.command()
def status(pub_id: str = typer.Argument(None, help="Publication ID to look up")):
    """Show status of one or all archives."""
    typer.echo("status: not yet implemented")
