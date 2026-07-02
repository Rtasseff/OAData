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
    from oa_tracker.emails import generate_emails, pending_response_pubs

    cfg = _get_config(config, db)
    paths = generate_emails(cfg)
    if paths:
        typer.echo(f"Generated {len(paths)} email draft(s):")
        for p in paths:
            typer.echo(f"  {p}")
    else:
        typer.echo("No email drafts to generate.")

    pending = pending_response_pubs(cfg)
    if pending:
        typer.echo(
            f"Note: {len(pending)} publication(s) have an un-applied Tracker response; "
            "any due reminders for them are held until you apply or decline them in "
            f"sharepoint_proposals.tsv: {', '.join(sorted(pending))}"
        )


@app.command()
def action(
    pub_id: str = typer.Argument(..., help="Publication ID to act on"),
    task_code: str = typer.Argument(..., help="Task code (e.g. qa_pass, qa_hold, close_exception, zenodo_published, set_data_contact)"),
    done: int = typer.Option(1, "--done", help="1 = apply the task; 2 = full closure"),
    pid: str = typer.Option("", "--pid", help="Dataset PID / DOI"),
    url: str = typer.Option("", "--url", help="Dataset URL"),
    note: str = typer.Option("", "--note", help="Free-text note, recorded in the audit log"),
    email: str = typer.Option("", "--email", help="Email for set_data_contact"),
    name: str = typer.Option("", "--name", help="Name for set_data_contact"),
    code: str = typer.Option("", "--code", help="Code for set_zenodo_code"),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Apply a single action to one archive without going through the action sheet.

    Use this for mid-week one-offs — a PI calls, you decide to move one
    archive forward or close it out. The semantics match what a single
    row on the action sheet would do (validate_transition, fast-track
    when a PID is supplied, done=2 full closure, qa_hold / remind_sent
    / contact_pi_manual / mandate_missing special handling).

    Operator-override task codes (``set_data_contact``,
    ``reset_data_contact``, ``set_zenodo_code``, ``reset_zenodo_code``)
    are dispatched to dedicated handlers — they don't appear on the
    action sheet but they reuse the same audit-log pattern.
    """
    from oa_tracker import status as st
    from oa_tracker.actions import (
        apply_single, set_data_contact, reset_data_contact,
        set_zenodo_code, reset_zenodo_code,
        set_corresponding_author, reset_corresponding_author,
    )

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

    # Dispatch operator-override task codes to their dedicated handlers.
    if task_code in st.OVERRIDE_TASK_CODES:
        if task_code == "set_data_contact":
            result = set_data_contact(cfg, pub_id, email=email, name=(name or None))
            ok_msg = f"Set data_contact on {pub_id}: {name or '?'} <{email}>"
        elif task_code == "reset_data_contact":
            result = reset_data_contact(cfg, pub_id)
            ok_msg = f"Reset data_contact override on {pub_id}; next scan will re-seed from the central DB."
        elif task_code == "set_zenodo_code":
            result = set_zenodo_code(cfg, pub_id, code=code)
            ok_msg = f"Set zenodo_code on {pub_id}: {code}"
        elif task_code == "reset_zenodo_code":
            result = reset_zenodo_code(cfg, pub_id)
            ok_msg = f"Reset zenodo_code override on {pub_id}; next scan will re-seed from the central DB."
        elif task_code == "set_corresponding_author":
            result = set_corresponding_author(cfg, pub_id, email=email, name=(name or None))
            ok_msg = f"Set corresponding_author on {pub_id}: {name or '?'} <{email}>"
        else:  # reset_corresponding_author
            result = reset_corresponding_author(cfg, pub_id)
            ok_msg = f"Reset corresponding_author override on {pub_id}; next scan will re-seed from the central DB."

        for e in result.errors:
            typer.echo(f"Error: {e}")
        if result.errors:
            raise typer.Exit(1)
        if not result.applied:
            typer.echo("Nothing applied.")
            raise typer.Exit(1)
        typer.echo(ok_msg)
        return

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


@app.command()
def auto(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Run the full unattended cycle (designed for cron).

    scan → SharePoint pull (auto-apply promoted signals, route the rest)
    → advance archives (auto-QC, Zenodo draft + upload, closures)
    → SharePoint push → regenerate sheet/emails/report → write the digest.

    Never prompts: an expired SharePoint token fails that stage with a
    clear message instead of blocking on a device-code prompt. Zenodo
    publishing is never automatic — validated drafts wait for you.
    """
    from oa_tracker.auto import run_auto, write_digest
    from oa_tracker.emails import generate_emails
    from oa_tracker.report import generate_report
    from oa_tracker.sheet import generate_sheet

    cfg = _get_config(config, db)
    if not cfg.automation.enabled:
        typer.echo("[automation] is not enabled in config.toml — nothing to do.")
        typer.echo("Set `enabled = true` under [automation] to turn on `oa auto`.")
        raise typer.Exit(1)

    result = run_auto(cfg)

    # Regenerate the operator artifacts from the post-run state.
    try:
        generate_sheet(cfg)
    except Exception as e:
        result.errors.append(f"sheet generation failed: {e}")
    try:
        generate_emails(cfg)
    except Exception as e:
        result.errors.append(f"email generation failed: {e}")
    try:
        generate_report(cfg)
    except Exception as e:
        result.errors.append(f"report generation failed: {e}")

    digest = write_digest(cfg, result)
    typer.echo(result.summary)
    typer.echo(f"Digest: {digest}")
    if result.errors:
        typer.echo("Errors this run:")
        for e in result.errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(1)


# ── SharePoint List parallel track ───────────────────────────────────

sharepoint_app = typer.Typer(help="SharePoint List sync (parallel track).")
app.add_typer(sharepoint_app, name="sharepoint")


@sharepoint_app.command("provision")
def sharepoint_provision(
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Create (or verify) the SharePoint List and its columns. Idempotent.

    First run is interactive (device-code sign-in); the refresh token is
    cached so scheduled runs don't re-prompt.
    """
    from oa_tracker import sharepoint as sp_mod

    cfg = _get_config(config, db)
    sp = sp_mod.load_settings(cfg)
    client = sp_mod.GraphClient(sp)
    site_id = client.get_site_id(sp.site)
    try:
        _, web_url, name_for = sp_mod.ensure_list(client, site_id, sp)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(f"List ready ({len(name_for)} columns): {sp.list_name}")
    if web_url:
        typer.echo(f"  {web_url}")


@sharepoint_app.command("sync")
def sharepoint_sync(
    read_only: bool = typer.Option(
        False, "--read-only", help="Diff against the list and write nothing (prototype mode)."
    ),
    config: Optional[str] = ConfigOption,
    db: Optional[str] = DbOption,
):
    """Push system-owned columns for all OPEN archives to the SharePoint List.

    ``--read-only`` fetches the list and reports what a push WOULD change
    (writing a scratch JSON), without touching SharePoint.
    """
    import csv
    import json
    from datetime import datetime
    from oa_tracker import sharepoint as sp_mod
    from oa_tracker.db import (
        get_archive, get_connection, get_open_archives, insert_event, upsert_archive,
    )
    from oa_tracker.sheet import SHEET_COLUMNS, proposal_row

    cfg = _get_config(config, db)
    sp = sp_mod.load_settings(cfg)
    with get_connection(cfg.database) as conn:
        archives = get_open_archives(conn)

    client = sp_mod.GraphClient(sp)
    site_id = client.get_site_id(sp.site)

    if read_only:
        # Genuinely read-only: do NOT provision and write nothing back.
        lst = sp_mod.get_list(client, site_id, sp.list_name)
        if lst is None:
            typer.echo(f"List {sp.list_name!r} isn't provisioned yet.")
            typer.echo("Run `oa sharepoint provision` first (read-only won't create it).")
            raise typer.Exit(1)
        list_id = lst["id"]
        name_for = sp_mod.resolve_names(client, site_id, list_id)
        pubid = name_for.get(sp_mod.D_PUBID)
        existing = sp_mod.fetch_items(client, site_id, list_id, pubid) if pubid else {}
        diff = sp_mod.diff_against_list(archives, existing, sp)
        pulled = sp_mod.pull_proposals(list(existing.values()), name_for) if pubid else []
        waiting = sum(len(p.proposals) for p in pulled)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        out = cfg.output_dir / "sharepoint_state.json"
        out.write_text(json.dumps(
            {"open_archives": len(archives), "on_list": len(existing),
             "diff": diff, "pending_proposals": waiting}, indent=2
        ))
        typer.echo("Read-only — nothing written to SharePoint.")
        typer.echo(f"  would create: {len(diff['would_create'])}")
        typer.echo(f"  would update: {len(diff['would_update'])}")
        typer.echo(f"  would remove (closed since last sync): {len(diff['would_remove'])}")
        typer.echo(f"  new user proposals waiting: {waiting}")
        typer.echo(f"  state written to {out}")
        return

    # Outbound: push system-owned columns.
    list_id, web_url, name_for = sp_mod.ensure_list(client, site_id, sp)
    email_to_lookup = client.resolve_users(site_id)
    now = datetime.now().isoformat(timespec="seconds")
    result = sp_mod.push_archives(
        client, site_id, list_id, sp, name_for, email_to_lookup, archives, now
    )
    typer.echo(result.summary)

    # Inbound: pull user edits → reviewable action rows; stamp status back.
    # user_details (LookupId → name/email) lets a "suggest a new data contact"
    # proposal name the person and pre-fill the set_data_contact command.
    user_details = client.resolve_user_details(site_id)
    by_id = {a["publication_id"]: a for a in archives}
    items = sp_mod.fetch_items(client, site_id, list_id, name_for[sp_mod.D_PUBID])
    pulled = sp_mod.pull_proposals(list(items.values()), name_for, user_details)

    # Persist the "I think this is done" tick (set or cleared) on the
    # archive row — the automation engine and the action sheet cross-check
    # it against the detected folder package.
    with get_connection(cfg.database) as conn:
        for pi in pulled:
            arch = by_id.get(pi.pub_id)
            if arch is None:
                continue
            new_flag = 1 if pi.proposed_done else 0
            if (arch.get("user_done_flag") or 0) != new_flag:
                upsert_archive(
                    conn, publication_id=pi.pub_id,
                    user_done_flag=new_flag,
                    user_done_at=now if new_flag else None,
                )
                insert_event(
                    conn, pi.pub_id, "user_done_flag", arch["status"], arch["status"],
                    "sharepoint",
                    note=("Tracker 'I think this is done' ticked" if new_flag
                          else "Tracker 'done' tick cleared"),
                )

    rows = []
    for pi in pulled:
        arch = by_id.get(pi.pub_id)
        for prop in pi.proposals:
            rows.append(proposal_row(pi.pub_id, arch, prop.task_code, prop.task_text,
                                     prop.note, prop.pid, prop.url))
        # A free-text List note is awareness-only, but it must be DURABLE —
        # a scheduled sync's stdout goes nowhere. Emit it as a `user_note`
        # row so it lands in the proposals file like everything else and is
        # recorded to the archive's notes on apply.
        if pi.user_notes:
            rows.append(proposal_row(pi.pub_id, arch, "user_note",
                                     "User note (awareness only — no action needed)",
                                     pi.user_notes))
    if rows:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        ppath = cfg.output_dir / "sharepoint_proposals.tsv"
        write_header = not ppath.exists()
        with open(ppath, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
            if write_header:
                w.writeheader()
            w.writerows(rows)
        typer.echo(f"Pulled {len(rows)} row(s) → {ppath}")
        typer.echo(f"  review, set done=1 on the ones to apply, then: oa apply {ppath}")
    else:
        typer.echo("No new user proposals.")
    # Stamp IngestedSig (+ RequestStatus where actionable) so edits aren't re-emitted.
    for pi in pulled:
        sp_mod.write_proposal_feedback(client, site_id, list_id, name_for, pi)

    # Reconcile rows whose archive closed since the last sync: relabel to the
    # closed status once ("show Done"), then remove on the following sync (or
    # keep, when sync_closed=true). Open rows were handled by the push above;
    # rows with no matching archive are left untouched.
    open_ids = set(by_id)
    non_open = [pid for pid in items if pid not in open_ids]
    archive_by_id: dict = {}
    if non_open:
        with get_connection(cfg.database) as conn:
            for pid in non_open:
                archive_by_id[pid] = get_archive(conn, pid)
    rec = sp_mod.reconcile_closed_rows(
        client, site_id, list_id, sp, name_for, items, archive_by_id, now
    )
    if rec.relabeled or rec.removed or rec.warnings:
        typer.echo(rec.summary)

    notes = [(pi.pub_id, pi.user_notes) for pi in pulled if pi.user_notes]
    if notes:
        typer.echo("User notes (awareness only — saved as user_note rows in the file):")
        for pub_id, n in notes:
            typer.echo(f"  {pub_id}: {n}")
    if web_url:
        typer.echo(f"  {web_url}")
