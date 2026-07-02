"""The unattended automation engine behind ``oa auto``.

One scheduled run does, in order (each stage guarded — a failure is
recorded in the digest and the remaining stages still run):

1. **Scan** the folder tree (folder states + package detection + pub-DB
   enrichment — all existing scanner behavior).
2. **Pull** the SharePoint List: persist the "I think this is done" tick
   to the archive row, auto-apply the promoted signal classes (data-contact
   reassignments, categorized exemptions with evidence, user notes), and
   route everything else to ``sharepoint_proposals.tsv`` for the operator
   exactly as before.
3. **Advance** archives:
   - auto-QC: OPEN_ACTIVE + Tracker "done" + detected package
     (``.zip`` + ``README.txt``) + data-required mandate → ``qa_pass``;
   - Zenodo: READY archives get a draft (metadata + reserved DOI) and the
     package files uploaded — then STOP: validation and publish stay
     operator-confirmed;
   - closure: OPEN_DB_UPDATED + folder gone + PID on record →
     ``folder_removed`` (CLOSED_DATA_ARCHIVED).
4. **Push** fresh statuses back to the List (+ closed-row reconcile).

Everything applied automatically goes through the same ``apply_single``
path the operator uses, with ``source="auto"`` in the audit log, and is
listed in the run digest (``output/auto_digest.md``).

Mismatch cases deliberately NOT automated (per operator decision
2026-07-02): "done" ticked but no package, and package present but no
"done" tick. Both surface on the action sheet with explanatory notes and
in package-aware reminder text — the operator (or the data contact)
resolves them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from oa_tracker import db, status as st
from oa_tracker.config import Config


@dataclass
class AutoRunResult:
    started_at: str = ""
    scan_summary: str = ""
    auto_applied: list[str] = field(default_factory=list)
    manual_rows: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    awaiting_operator: list[str] = field(default_factory=list)
    user_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sharepoint_pushed: str = ""

    @property
    def summary(self) -> str:
        parts = [f"Auto-applied: {len(self.auto_applied)}",
                 f"Routed to operator: {len(self.manual_rows)}",
                 f"Mismatches flagged: {len(self.mismatches)}",
                 f"Errors: {len(self.errors)}"]
        return "; ".join(parts)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Stage 2: SharePoint pull + routing ───────────────────────────────

@dataclass
class _SpContext:
    client: object
    site_id: str
    list_id: str
    name_for: dict
    items: dict


def _pull_sharepoint(config: Config, result: AutoRunResult) -> _SpContext | None:
    """Pull user edits, auto-apply promoted classes, route the rest to the
    proposals TSV. Returns the Graph context for the later push stage."""
    from oa_tracker import sharepoint as sp_mod
    from oa_tracker.actions import apply_single, set_data_contact
    from oa_tracker.sheet import SHEET_COLUMNS, proposal_row

    sp = sp_mod.load_settings(config)
    client = sp_mod.GraphClient(sp, interactive=False)
    site_id = client.get_site_id(sp.site)
    lst = sp_mod.get_list(client, site_id, sp.list_name)
    if lst is None:
        result.errors.append(
            f"SharePoint list {sp.list_name!r} not provisioned — run `oa sharepoint provision`."
        )
        return None
    list_id = lst["id"]
    name_for = sp_mod.resolve_names(client, site_id, list_id)
    items = sp_mod.fetch_items(client, site_id, list_id, name_for[sp_mod.D_PUBID])
    user_details = client.resolve_user_details(site_id)
    pulled = sp_mod.pull_proposals(list(items.values()), name_for, user_details)

    gates = config.automation
    tsv_rows: list[dict] = []

    with db.get_connection(config.database) as conn:
        archives = {a["publication_id"]: a for a in db.get_all_archives(conn)}
        # Persist the done-tick state (set OR cleared) for the sheet/engine.
        for pi in pulled:
            arch = archives.get(pi.pub_id)
            if arch is None:
                continue
            new_flag = 1 if pi.proposed_done else 0
            if (arch.get("user_done_flag") or 0) != new_flag:
                db.upsert_archive(
                    conn, publication_id=pi.pub_id,
                    user_done_flag=new_flag,
                    user_done_at=_now() if new_flag else None,
                )
                db.insert_event(
                    conn, pi.pub_id, "user_done_flag", arch["status"], arch["status"],
                    "sharepoint",
                    note=("Tracker 'I think this is done' ticked" if new_flag
                          else "Tracker 'done' tick cleared"),
                )
                arch["user_done_flag"] = new_flag

    # Route each proposal: auto-apply promoted classes, TSV for the rest.
    for pi in pulled:
        arch_status_changed = False
        auto_ok = True
        for prop in pi.proposals:
            routed_auto = False
            if prop.task_code == "propose_data_contact" and gates.auto_apply_data_contact \
                    and prop.contact_email:
                r = set_data_contact(config, pi.pub_id, email=prop.contact_email,
                                     name=prop.contact_name or None)
                if r.applied and not r.errors:
                    result.auto_applied.append(
                        f"{pi.pub_id}: data contact → {prop.contact_name or prop.contact_email}"
                    )
                    routed_auto = True
                else:
                    result.errors.extend(r.errors)
            elif prop.task_code in ("close_publication_only", "close_exception",
                                    "close_archived_external") and gates.auto_apply_exemptions:
                r, old_s, new_s = apply_single(
                    config, pi.pub_id, prop.task_code, done=1,
                    pid=prop.pid, url=prop.url,
                    note=f"[auto] {prop.note}",
                )
                if r.applied and not r.errors:
                    result.auto_applied.append(
                        f"{pi.pub_id}: exemption applied ({prop.task_code}) — {old_s} → {new_s}"
                    )
                    routed_auto = True
                    arch_status_changed = True
                else:
                    result.errors.extend(r.errors)
            elif prop.task_code == "propose_done":
                # Handled via the persisted user_done_flag + package check in
                # the advance stage; no TSV row — mismatches surface on the
                # sheet. Just record it in the digest.
                result.auto_applied.append(
                    f"{pi.pub_id}: 'done' tick recorded (auto-QC decides on next lines)"
                )
                routed_auto = True

            if not routed_auto:
                auto_ok = False
                with db.get_connection(config.database) as conn:
                    arch = db.get_archive(conn, pi.pub_id)
                tsv_rows.append(proposal_row(
                    pi.pub_id, arch, prop.task_code, prop.task_text,
                    prop.note, prop.pid, prop.url,
                ))
                result.manual_rows.append(f"{pi.pub_id}: {prop.task_code} — {prop.note[:100]}")

        if pi.user_notes:
            if gates.auto_apply_user_notes:
                r, _, _ = apply_single(config, pi.pub_id, "user_note", done=1,
                                       note=pi.user_notes)
                if r.applied and not r.errors:
                    result.user_notes.append(f"{pi.pub_id}: {pi.user_notes}")
                else:
                    result.errors.extend(r.errors)
            else:
                with db.get_connection(config.database) as conn:
                    arch = db.get_archive(conn, pi.pub_id)
                tsv_rows.append(proposal_row(
                    pi.pub_id, arch, "user_note",
                    "User note (awareness only — no action needed)", pi.user_notes,
                ))

        # Stamp the row: processed when everything auto-applied cleanly,
        # pending when at least one signal awaits the operator.
        status_label = (
            sp_mod.REQUEST_STATUS_PROCESSED
            if (pi.proposals and auto_ok)
            else None  # default behavior: pending when proposals, else sig only
        )
        try:
            sp_mod.write_proposal_feedback(
                client, site_id, list_id, name_for, pi, request_status=status_label,
            )
        except Exception as e:  # feedback failure shouldn't lose the pull
            result.errors.append(f"{pi.pub_id}: feedback stamp failed: {e}")

    if tsv_rows:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        ppath = config.output_dir / "sharepoint_proposals.tsv"
        write_header = not ppath.exists()
        with open(ppath, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
            if write_header:
                w.writeheader()
            w.writerows(tsv_rows)

    return _SpContext(client, site_id, list_id, name_for, items)


def _push_sharepoint(config: Config, ctx: _SpContext, result: AutoRunResult) -> None:
    """Push fresh statuses out and reconcile closed rows (post-advance)."""
    from oa_tracker import sharepoint as sp_mod

    sp = sp_mod.load_settings(config)
    now = _now()
    with db.get_connection(config.database) as conn:
        archives = db.get_open_archives(conn)
    email_to_lookup = ctx.client.resolve_users(ctx.site_id)
    push = sp_mod.push_archives(
        ctx.client, ctx.site_id, ctx.list_id, sp, ctx.name_for,
        email_to_lookup, archives, now,
    )
    result.sharepoint_pushed = f"created {push.created}, updated {push.updated}"
    result.errors.extend(push.errors)

    open_ids = {a["publication_id"] for a in archives}
    items = sp_mod.fetch_items(ctx.client, ctx.site_id, ctx.list_id,
                               ctx.name_for[sp_mod.D_PUBID])
    non_open = [pid for pid in items if pid not in open_ids]
    archive_by_id: dict = {}
    if non_open:
        with db.get_connection(config.database) as conn:
            for pid in non_open:
                archive_by_id[pid] = db.get_archive(conn, pid)
    rec = sp_mod.reconcile_closed_rows(
        ctx.client, ctx.site_id, ctx.list_id, sp, ctx.name_for, items,
        archive_by_id, now,
    )
    result.errors.extend(rec.warnings)


# ── Stage 3: advance archives ────────────────────────────────────────

def package_complete(archive: dict) -> bool:
    return bool(archive.get("package_has_zip")) and bool(archive.get("package_has_readme"))


def _data_required(archive: dict) -> bool:
    return (
        archive.get("oa_data_required") == 1
        and archive.get("oa_mandate_missing") != 1
    )


def _advance(config: Config, result: AutoRunResult) -> None:
    from oa_tracker.actions import apply_single

    gates = config.automation

    with db.get_connection(config.database) as conn:
        archives = db.get_open_archives(conn)

    # 3a. Close out finished archives whose folder was removed.
    if gates.auto_close_on_folder_removed:
        for a in archives:
            if (a["status"] == st.OPEN_DB_UPDATED
                    and a.get("unexpected_missing_folder")
                    and a.get("final_pid")):
                r, old_s, new_s = apply_single(
                    config, a["publication_id"], "folder_removed", done=1,
                    note="[auto] folder removed after DB update; closing",
                )
                if r.applied and not r.errors:
                    result.auto_applied.append(
                        f"{a['publication_id']}: closed ({old_s} → {new_s})"
                    )
                else:
                    result.errors.extend(r.errors)

    # 3b. Auto-QC: Tracker "done" + package + data-required mandate.
    if gates.auto_qa_pass:
        for a in archives:
            if a["status"] != st.OPEN_ACTIVE or not a.get("user_done_flag"):
                continue
            if package_complete(a) and _data_required(a):
                r, old_s, new_s = apply_single(
                    config, a["publication_id"], "qa_pass", done=1,
                    note="[auto] QC: Tracker 'done' confirmed and package "
                         "(.zip + README.txt) detected in folder",
                )
                if r.applied and not r.errors:
                    result.auto_applied.append(
                        f"{a['publication_id']}: qa_pass ({old_s} → {new_s})"
                    )
                else:
                    result.errors.extend(r.errors)
            elif not package_complete(a):
                missing = []
                if not a.get("package_has_zip"):
                    missing.append(".zip")
                if not a.get("package_has_readme"):
                    missing.append("README.txt")
                result.mismatches.append(
                    f"{a['publication_id']}: user says done but folder is missing "
                    f"{' and '.join(missing)} — reminder text asks them to package; "
                    "QA manually if the contents are actually fine"
                )
            else:
                result.mismatches.append(
                    f"{a['publication_id']}: user says done but the mandate "
                    "classification isn't data-required — review on the sheet"
                )

    # Note the reverse mismatch for the digest (sheet + reminders carry it too).
    for a in archives:
        if (a["status"] == st.OPEN_ACTIVE and package_complete(a)
                and not a.get("user_done_flag")):
            result.mismatches.append(
                f"{a['publication_id']}: package (.zip + README) detected but no "
                "Tracker 'done' tick — QA manually or wait for confirmation"
            )

    # 3c. Zenodo drafts + uploads for READY archives.
    if config.zenodo.enabled and gates.auto_zenodo_draft:
        with db.get_connection(config.database) as conn:
            ready = db.get_all_archives(conn, status_filter=st.OPEN_READY_FOR_ZENODO_DRAFT)
        for a in ready:
            pub_id = a["publication_id"]
            if not pub_id.isdigit():
                result.awaiting_operator.append(
                    f"{pub_id}: ready for Zenodo but is a placeholder (no central-DB "
                    "metadata) — create the draft manually"
                )
                continue
            if a.get("zenodo_code"):
                result.awaiting_operator.append(
                    f"{pub_id}: ready for Zenodo but already has code "
                    f"{a['zenodo_code']} — advance manually (zenodo_draft_created)"
                )
                continue
            r, old_s, new_s = apply_single(
                config, pub_id, "zenodo_create_draft", done=1,
            )
            if r.applied and not r.errors:
                result.auto_applied.append(f"{pub_id}: Zenodo draft created ({old_s} → {new_s})")
                if gates.auto_zenodo_upload:
                    r2, _, _ = apply_single(config, pub_id, "zenodo_upload_files", done=1)
                    if r2.applied and not r2.errors:
                        result.auto_applied.append(f"{pub_id}: package uploaded to draft")
                    else:
                        result.errors.extend(r2.errors or [f"{pub_id}: upload did not apply"])
            else:
                result.errors.extend(r.errors or [f"{pub_id}: draft creation did not apply"])

    # 3d. Retry uploads for drafts created earlier whose upload never
    # succeeded (idempotent — checksummed against the draft's files).
    if config.zenodo.enabled and gates.auto_zenodo_upload:
        with db.get_connection(config.database) as conn:
            created = db.get_all_archives(conn, status_filter=st.OPEN_ZENODO_DRAFT_CREATED)
            for a in created:
                pub_id = a["publication_id"]
                if not a.get("zenodo_code") or a.get("zenodo_env") != config.zenodo.environment:
                    continue
                create_ev = db.get_last_event(conn, pub_id, "zenodo_create_draft")
                upload_ev = db.get_last_event(conn, pub_id, "zenodo_upload_files")
                if create_ev is None:
                    continue  # draft made by hand — uploads are the operator's call
                if upload_ev is not None and upload_ev["ts"] >= create_ev["ts"]:
                    continue  # already uploaded since creation
                r, _, _ = apply_single(config, pub_id, "zenodo_upload_files", done=1)
                if r.applied and not r.errors:
                    result.auto_applied.append(f"{pub_id}: package uploaded to draft (retry)")
                else:
                    result.errors.extend(r.errors or [f"{pub_id}: upload retry did not apply"])

    # Operator worklist for the digest.
    with db.get_connection(config.database) as conn:
        for a in db.get_open_archives(conn):
            pub_id, s = a["publication_id"], a["status"]
            if s == st.OPEN_ZENODO_DRAFT_CREATED and a.get("zenodo_code"):
                from oa_tracker import zenodo as z
                url = z.record_ui_url(config.zenodo, a["zenodo_code"]) \
                    if config.zenodo.enabled else f"record {a['zenodo_code']}"
                result.awaiting_operator.append(
                    f"{pub_id}: validate the Zenodo draft ({url}), then apply zenodo_validated"
                )
            elif s == st.OPEN_ZENODO_DRAFT_VALIDATED:
                result.awaiting_operator.append(
                    f"{pub_id}: validated — publish via the sheet's zenodo_publish row "
                    "(mints the DOI; operator-confirmed by design)"
                )
            elif s == st.OPEN_ZENODO_PUBLISHED:
                result.awaiting_operator.append(
                    f"{pub_id}: published — update the internal DB, then db_updated"
                )
            elif s == st.OPEN_DB_UPDATED and not a.get("unexpected_missing_folder"):
                result.awaiting_operator.append(
                    f"{pub_id}: DB updated — remove the SharePoint folder "
                    "(auto-closes on the next run)"
                )


# ── Orchestration + digest ───────────────────────────────────────────

def run_auto(config: Config) -> AutoRunResult:
    """Run the full unattended cycle. Never raises for per-stage failures —
    everything lands in the digest."""
    from oa_tracker.scanner import scan_folders

    result = AutoRunResult(started_at=_now())

    # Unattended runs must never trip over a pending schema migration —
    # init_db is idempotent and brings the DB to the current version.
    try:
        db.init_db(config.database)
    except Exception as e:
        result.errors.append(f"database init/migration failed: {e}")
        return result

    try:
        scan = scan_folders(config)
        result.scan_summary = scan.summary
        result.errors.extend(scan.errors)
    except Exception as e:
        result.errors.append(f"scan failed: {e}")

    ctx = None
    if config.sharepoint.enabled:
        try:
            ctx = _pull_sharepoint(config, result)
        except Exception as e:
            result.errors.append(f"SharePoint pull failed: {e}")

    try:
        _advance(config, result)
    except Exception as e:
        result.errors.append(f"advance stage failed: {e}")

    if ctx is not None:
        try:
            _push_sharepoint(config, ctx, result)
        except Exception as e:
            result.errors.append(f"SharePoint push failed: {e}")

    return result


def write_digest(config: Config, result: AutoRunResult) -> Path:
    """Write the operator-facing run digest (overwritten each run; the
    events table and action_history.tsv are the durable audit trail)."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.output_dir / "auto_digest.md"

    def section(title: str, lines: list[str], empty: str) -> str:
        body = "\n".join(f"- {ln}" for ln in lines) if lines else f"_{empty}_"
        return f"## {title}\n\n{body}\n"

    md = [
        f"# oa auto — run digest\n",
        f"Run started: {result.started_at}  ",
        f"Finished: {_now()}\n",
        "## Scan\n",
        "```",
        result.scan_summary or "(no scan output)",
        "```\n",
        section("Applied automatically", result.auto_applied, "nothing this run"),
        section("Needs your decision (also on the action sheet / proposals TSV)",
                result.manual_rows + result.mismatches, "nothing waiting"),
        section("Operator worklist (pipeline states only you can advance)",
                result.awaiting_operator, "pipeline is idle"),
        section("User notes from the Tracker", result.user_notes, "none"),
        section("Errors", result.errors, "none"),
    ]
    if result.sharepoint_pushed:
        md.append(f"\nSharePoint push: {result.sharepoint_pushed}\n")
    path.write_text("\n".join(md))

    # Append one line to the rolling log so cron runs leave a visible trail.
    log = config.output_dir / "auto_log.txt"
    with open(log, "a") as f:
        f.write(f"{result.started_at}  {result.summary}\n")
    return path
