"""Parse action_sheet.tsv, validate transitions, apply completed actions to DB."""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from oa_tracker import db, status as st
from oa_tracker.config import Config


@dataclass
class ApplyResult:
    applied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"Applied: {self.applied}", f"Skipped: {self.skipped}"]
        if self.warnings:
            parts.append(f"Warnings: {len(self.warnings)}")
            for w in self.warnings:
                parts.append(f"  - {w}")
        if self.errors:
            parts.append(f"Errors: {len(self.errors)}")
            for e in self.errors:
                parts.append(f"  - {e}")
        return "\n".join(parts)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _looks_like_paper_doi(pid: str) -> bool:
    """Heuristic: Zenodo dataset DOIs contain 'zenodo'; paper DOIs typically don't."""
    if not pid:
        return False
    pid_lower = pid.lower()
    # Zenodo DOIs look like 10.5281/zenodo.XXXXXXX
    if "zenodo" in pid_lower:
        return False
    # If it looks like a DOI but has no zenodo, it's likely a paper DOI
    if re.match(r"10\.\d{4,}/", pid_lower):
        return True
    return False


def _apply_row(
    conn: sqlite3.Connection,
    row: dict,
    now: str,
    config: Config,
    source: str,
    result: ApplyResult,
    row_label: str,
) -> tuple[bool, str | None, str | None]:
    """Apply one action row to the database.

    Returns (applied, old_status, new_status). Mutates `result` with the
    applied/skipped count, warnings, and errors.

    `source` is written into the events table and `row_label` prefixes
    any warning/error messages — "Row 5" for the sheet path, "Action"
    for one-off CLI invocations.
    """
    done = row.get("done", "0").strip()
    if done not in ("1", "2"):
        result.skipped += 1
        return (False, None, None)

    pub_id = row["publication_id"].strip()
    task_code = row["task_code"].strip()
    pid = (row.get("pid") or "").strip().strip("-")
    url = (row.get("url") or "").strip().strip("-")
    note = (row.get("note") or "").strip()

    archive = db.get_archive(conn, pub_id)
    if archive is None:
        result.errors.append(f"{row_label}: publication {pub_id!r} not in database")
        return (False, None, None)

    old_status = archive["status"]

    # ── done=2: full closure shortcut ─────────────────────────
    if done == "2":
        extra_fields: dict[str, Any] = {}
        if pid:
            extra_fields["final_pid"] = pid
        if url:
            extra_fields["final_url"] = url
        if note:
            existing_notes = archive.get("notes") or ""
            separator = "\n" if existing_notes else ""
            extra_fields["notes"] = f"{existing_notes}{separator}[{now}] {note}"

        has_pid = pid or archive.get("final_pid")
        if has_pid:
            new_status = st.CLOSED_DATA_ARCHIVED
        else:
            new_status = st.CLOSED_EXCEPTION
            result.warnings.append(
                f"{row_label} ({pub_id}): done=2 with no PID; closing as CLOSED_EXCEPTION"
            )

        db.update_archive_status(conn, pub_id, new_status, **extra_fields)
        db.insert_event(
            conn, pub_id, "full_closure", old_status, new_status, source,
            pid=pid or None, url=url or None, note=note or None,
        )
        result.applied += 1
        return (True, old_status, new_status)

    # ── close_archived_external: data archived in an external repo ──
    # The "ALL data deposited in another archive" exemption. The data is
    # archived (just not via our Zenodo pipeline), so this closes as
    # CLOSED_DATA_ARCHIVED with the EXTERNAL PID/URL recorded — it counts
    # in "data archived" totals, not as an exception. Handled before the
    # generic fast-track below so the external PID isn't mistaken for a
    # Zenodo publish. Requires both a PID and a URL.
    if task_code == "close_archived_external":
        if old_status not in st.OPEN_STATUSES:
            result.errors.append(
                f"{row_label} ({pub_id}): close_archived_external needs an OPEN "
                f"status, not {old_status}"
            )
            return (False, old_status, None)
        if not pid or not url:
            result.errors.append(
                f"{row_label} ({pub_id}): close_archived_external requires both a "
                f"PID and a URL (the external archive's)"
            )
            return (False, old_status, None)
        extra_fields = {"final_pid": pid, "final_url": url}
        if note:
            existing_notes = archive.get("notes") or ""
            separator = "\n" if existing_notes else ""
            extra_fields["notes"] = f"{existing_notes}{separator}[{now}] {note}"
        db.update_archive_status(conn, pub_id, st.CLOSED_DATA_ARCHIVED, **extra_fields)
        db.insert_event(
            conn, pub_id, "close_archived_external", old_status,
            st.CLOSED_DATA_ARCHIVED, source, pid=pid, url=url, note=note or None,
        )
        result.applied += 1
        return (True, old_status, st.CLOSED_DATA_ARCHIVED)

    # ── done=1 with PID/URL: fast-track to OPEN_ZENODO_PUBLISHED ──
    if (pid or url) and task_code not in ("remind_sent", "qa_hold"):
        if _looks_like_paper_doi(pid):
            result.warnings.append(
                f"{row_label} ({pub_id}): PID {pid!r} looks like a paper DOI, not a Zenodo DOI"
            )

        extra_fields = {}
        if pid:
            extra_fields["final_pid"] = pid
        if url:
            extra_fields["final_url"] = url
        if note:
            existing_notes = archive.get("notes") or ""
            separator = "\n" if existing_notes else ""
            extra_fields["notes"] = f"{existing_notes}{separator}[{now}] {note}"

        new_status = st.OPEN_ZENODO_PUBLISHED
        db.update_archive_status(conn, pub_id, new_status, **extra_fields)
        db.insert_event(
            conn, pub_id, "fast_track_published", old_status, new_status, source,
            pid=pid or None, url=url or None, note=note or None,
        )
        result.applied += 1
        return (True, old_status, new_status)

    # ── done=1 on contact_pi_manual (no PID/URL): close as exception ──
    # With a PID or URL present, the fast-track block above already
    # promoted the archive to OPEN_ZENODO_PUBLISHED. Without one, the
    # operator's manual PI contact did not yield a deposit, so we
    # close as non-compliant. Use the operator's note when supplied;
    # otherwise a standard note makes the closure reason explicit in
    # the audit log.
    if task_code == "contact_pi_manual":
        close_note = note or (
            "No response after max reminders and manual PI contact; "
            "closed as non-compliant with OA policy."
        )
        extra_fields = {}
        existing_notes = archive.get("notes") or ""
        separator = "\n" if existing_notes else ""
        extra_fields["notes"] = f"{existing_notes}{separator}[{now}] {close_note}"

        db.update_archive_status(
            conn, pub_id, st.CLOSED_EXCEPTION, **extra_fields
        )
        db.insert_event(
            conn, pub_id, "contact_pi_manual", old_status,
            st.CLOSED_EXCEPTION, source, note=close_note,
        )
        result.applied += 1
        return (True, old_status, st.CLOSED_EXCEPTION)

    # ── done=1 standard flow ──────────────────────────────────

    # Validate transition
    try:
        new_status = st.validate_transition(old_status, task_code)
    except ValueError as e:
        result.errors.append(f"{row_label} ({pub_id}): {e}")
        return (False, old_status, None)

    # API-backed Zenodo codes: the apply IS the API call (create draft /
    # upload files / publish). Terminal API failures become row errors —
    # no status change happens unless the call succeeded.
    if task_code in ("zenodo_create_draft", "zenodo_upload_files", "zenodo_publish"):
        return _apply_zenodo_row(
            conn, archive, task_code, new_status, note, now, config,
            source, result, row_label,
        )

    # Warn if zenodo_published PID looks like a paper DOI
    if task_code == "zenodo_published" and pid and _looks_like_paper_doi(pid):
        result.warnings.append(
            f"{row_label} ({pub_id}): PID {pid!r} looks like a paper DOI, not a Zenodo DOI"
        )

    # Handle remind_sent specially
    if task_code == "remind_sent":
        # Safety net: if the archive's status has already advanced past
        # the data-collection stage (e.g. qa_pass earlier in this same
        # batch moved it to OPEN_READY_FOR_ZENODO_DRAFT), don't tick
        # the reminder counter — the data is in and there's no one to
        # remind. The action sheet emits qa_pass before remind_sent so
        # this case happens naturally on the qa-passes-clean path.
        if old_status not in (st.OPEN_INACTIVE, st.OPEN_ACTIVE):
            result.warnings.append(
                f"{row_label} ({pub_id}): skipping remind_sent — status is "
                f"{old_status} (no longer waiting for data)"
            )
            result.skipped += 1
            return (False, old_status, None)
        count = archive["reminder_count"] + 1
        next_reminder: str | None = None
        if count < config.reminders.max_reminders:
            next_reminder = (
                datetime.now() + timedelta(days=config.reminders.reminder_interval_days)
            ).isoformat(timespec="seconds")
        db.upsert_archive(
            conn,
            publication_id=pub_id,
            last_notified_at=now,
            reminder_count=count,
            next_reminder_at=next_reminder,
        )
        db.insert_event(
            conn, pub_id, task_code, old_status, old_status, source,
            note=note or None,
        )
        result.applied += 1
        return (True, old_status, old_status)

    # Handle qa_hold and mandate_missing (no status change, optional note).
    # mandate_missing is acknowledgment-only: the row regenerates on the
    # next scan unless the upstream mandate becomes derivable or the
    # operator changes the task_code to an explicit closure.
    # propose_* parallel-track signals are likewise acknowledgment-only
    # for now: they log an event (and any note) for operator awareness
    # without changing status. The SharePoint sync module (step 6) gives
    # them their real routing — propose_data_contact → set the contact,
    # propose_exemption → re-route to the category's concrete close_* code.
    # user_note is a pure awareness signal: applying it records the user's
    # List note to the archive's notes (a durable, audited record) with no
    # status change.
    if task_code in (
        "qa_hold", "mandate_missing",
        "propose_data_contact", "propose_exemption", "propose_done", "user_note",
    ):
        extra: dict[str, Any] = {}
        if note:
            existing_notes = archive.get("notes") or ""
            separator = "\n" if existing_notes else ""
            extra["notes"] = f"{existing_notes}{separator}[{now}] {note}"
        if extra:
            db.upsert_archive(conn, publication_id=pub_id, **extra)
        db.insert_event(
            conn, pub_id, task_code, old_status, old_status, source,
            note=note or None,
        )
        result.applied += 1
        return (True, old_status, old_status)

    # Standard status transition
    extra_fields = {}
    if pid:
        extra_fields["final_pid"] = pid
    if url:
        extra_fields["final_url"] = url
    if note:
        existing_notes = archive.get("notes") or ""
        separator = "\n" if existing_notes else ""
        extra_fields["notes"] = f"{existing_notes}{separator}[{now}] {note}"

    # folder_removed without PID → CLOSED_EXCEPTION instead of CLOSED_DATA_ARCHIVED
    if task_code == "folder_removed" and not archive.get("final_pid") and not pid:
        new_status = st.CLOSED_EXCEPTION
        result.warnings.append(
            f"{row_label} ({pub_id}): No PID on record; closing as CLOSED_EXCEPTION instead of CLOSED_DATA_ARCHIVED"
        )

    db.update_archive_status(conn, pub_id, new_status, **extra_fields)
    db.insert_event(
        conn, pub_id, task_code, old_status, new_status, source,
        pid=pid or None, url=url or None, note=note or None,
    )
    result.applied += 1
    return (True, old_status, new_status)


def _append_note(archive: dict, note: str, now: str) -> str:
    existing_notes = archive.get("notes") or ""
    separator = "\n" if existing_notes else ""
    return f"{existing_notes}{separator}[{now}] {note}"


def _apply_zenodo_row(
    conn: sqlite3.Connection,
    archive: dict,
    task_code: str,
    new_status: str,
    note: str,
    now: str,
    config: Config,
    source: str,
    result: ApplyResult,
    row_label: str,
) -> tuple[bool, str | None, str | None]:
    """Perform the Zenodo API side effect for an apply row, then record it.

    The status is written only after the API call succeeds, so a failed
    call leaves the archive exactly where it was (safe to re-apply).
    """
    from oa_tracker import zenodo

    pub_id = archive["publication_id"]
    old_status = archive["status"]
    zset = config.zenodo
    if not zset.enabled:
        result.errors.append(
            f"{row_label} ({pub_id}): [zenodo] is not enabled in config.toml — "
            "either enable it or use the manual codes "
            "(zenodo_draft_created / zenodo_published)"
        )
        return (False, old_status, None)

    try:
        client = zenodo.get_client(zset)

        if task_code == "zenodo_create_draft":
            if archive.get("zenodo_code"):
                result.errors.append(
                    f"{row_label} ({pub_id}): already has zenodo_code "
                    f"{archive['zenodo_code']!r} — refusing to create a second draft. "
                    "Run `oa action ... reset_zenodo_code` first if that code is stale."
                )
                return (False, old_status, None)
            extras = zenodo.fetch_publication_extras(pub_id) if pub_id.isdigit() else {}
            payload = zenodo.build_record_payload(
                archive, zset,
                abstract=extras.get("abstract"),
                author_with_affiliation=extras.get("author_with_affiliation"),
                author_fallback=extras.get("author"),
                extra_biomagune_names=(
                    [extras["first_author_name"]] if extras.get("first_author_name") else []
                ),
            )
            draft = zenodo.create_draft(client, payload)
            event_note = (
                f"Zenodo draft {draft.record_id} created on {zset.environment}"
                + (f"; reserved DOI {draft.doi}" if draft.doi else "; DOI reserve failed (minted at publish)")
                + f" — {zenodo.summarize_payload(payload)}"
            )
            extra_fields: dict[str, Any] = {
                "zenodo_code": draft.record_id,
                "zenodo_code_overridden": 1,   # protect from scan re-seed
                "zenodo_env": zset.environment,
                "notes": _append_note(archive, note or event_note, now),
            }
            if draft.doi:
                extra_fields["zenodo_doi"] = draft.doi
            db.update_archive_status(conn, pub_id, new_status, **extra_fields)
            db.insert_event(
                conn, pub_id, task_code, old_status, new_status, source,
                pid=draft.doi, url=zenodo.record_ui_url(zset, draft.record_id),
                note=event_note,
            )
            result.applied += 1
            return (True, old_status, new_status)

        code = archive.get("zenodo_code")
        if not code:
            result.errors.append(
                f"{row_label} ({pub_id}): no zenodo_code on record — create the "
                "draft first (zenodo_create_draft)"
            )
            return (False, old_status, None)
        env = archive.get("zenodo_env")
        if env and env != zset.environment:
            result.errors.append(
                f"{row_label} ({pub_id}): draft {code} lives on {env!r} but config "
                f"environment is {zset.environment!r} — refusing to touch the wrong instance"
            )
            return (False, old_status, None)
        if not env:
            result.warnings.append(
                f"{row_label} ({pub_id}): zenodo_env unknown for code {code} "
                f"(set manually?) — assuming {zset.environment!r}"
            )

        if task_code == "zenodo_upload_files":
            from pathlib import Path as _P
            res = zenodo.upload_files(client, str(code), _P(archive["folder_path"]), zset)
            if not res.ok:
                result.errors.append(f"{row_label} ({pub_id}): upload failed — {res.summary}")
                return (False, old_status, None)
            db.upsert_archive(
                conn, publication_id=pub_id,
                notes=_append_note(archive, note or f"Zenodo upload: {res.summary}", now),
            )
            db.insert_event(
                conn, pub_id, task_code, old_status, old_status, source,
                note=res.summary,
            )
            result.applied += 1
            return (True, old_status, old_status)

        # zenodo_publish — the operator-confirmed, DOI-minting step.
        published = zenodo.publish(client, str(code))
        extra_fields = {
            "final_pid": published["doi"],
            "final_url": published["html_url"],
            "zenodo_doi": published["doi"],
            "notes": _append_note(
                archive, note or f"Published on Zenodo ({zset.environment}): {published['doi']}", now
            ),
        }
        db.update_archive_status(conn, pub_id, new_status, **extra_fields)
        db.insert_event(
            conn, pub_id, task_code, old_status, new_status, source,
            pid=published["doi"], url=published["html_url"],
            note=f"published on {zset.environment}",
        )
        result.applied += 1
        return (True, old_status, new_status)

    except zenodo.ZenodoError as e:
        result.errors.append(f"{row_label} ({pub_id}): Zenodo [{e.kind}] {e}")
        return (False, old_status, None)


def apply_actions(sheet_path: Path, config: Config) -> ApplyResult:
    """Read the action sheet, apply rows where done=1 or 2, and return results."""
    result = ApplyResult()
    now = _now()

    with open(sheet_path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    applied_indices: list[int] = []

    with db.get_connection(config.database) as conn:
        for i, row in enumerate(rows):
            applied, _, _ = _apply_row(
                conn, row, now, config, "action_sheet", result, f"Row {i+1}"
            )
            if applied:
                applied_indices.append(i)

    # Append applied rows to action_history.tsv
    if applied_indices:
        history_path = config.output_dir / "action_history.tsv"
        write_header = not history_path.exists()
        with open(history_path, "a", newline="") as hf:
            fieldnames = list(rows[0].keys()) + ["applied_at"]
            writer = csv.DictWriter(hf, fieldnames=fieldnames, delimiter="\t")
            if write_header:
                writer.writeheader()
            for idx in applied_indices:
                row_copy = dict(rows[idx])
                row_copy["applied_at"] = now
                writer.writerow(row_copy)

    # Rewrite the action sheet without applied rows
    remaining = [row for i, row in enumerate(rows) if i not in set(applied_indices)]
    with open(sheet_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [], delimiter="\t")
        writer.writeheader()
        writer.writerows(remaining)

    return result


def _archive_or_error(conn: sqlite3.Connection, pub_id: str, result: ApplyResult) -> dict | None:
    archive = db.get_archive(conn, pub_id)
    if archive is None:
        result.errors.append(f"Action: publication {pub_id!r} not in database")
    return archive


def set_data_contact(
    config: Config, pub_id: str, email: str, name: str | None = None
) -> ApplyResult:
    """Override the data-contact name/email and mark it as operator-managed.

    Subsequent scans preserve these values until ``reset_data_contact`` is
    called (which clears the override flag, letting the next scan re-seed
    from the central corresponding-author lookup).
    """
    result = ApplyResult()
    if not email:
        result.errors.append("set_data_contact requires --email")
        return result
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        updates = {
            "publication_id": pub_id,
            "data_contact_email": email,
            "data_contact_overridden": 1,
        }
        if name is not None:
            updates["data_contact_name"] = name
        db.upsert_archive(conn, **updates)
        db.insert_event(
            conn, pub_id, "set_data_contact",
            archive["status"], archive["status"], "cli",
            note=f"data_contact set to {name or '?'} <{email}>",
        )
        result.applied += 1
    return result


def reset_data_contact(config: Config, pub_id: str) -> ApplyResult:
    """Clear the data-contact override; the next scan re-seeds from the central DB."""
    result = ApplyResult()
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        db.upsert_archive(
            conn,
            publication_id=pub_id,
            data_contact_overridden=0,
        )
        db.insert_event(
            conn, pub_id, "reset_data_contact",
            archive["status"], archive["status"], "cli",
            note="data_contact override cleared",
        )
        result.applied += 1
    return result


def set_corresponding_author(
    config: Config, pub_id: str, email: str, name: str | None = None
) -> ApplyResult:
    """Pin an 'effective' corresponding author (operator-managed).

    For papers whose real corresponding author is external/blank, this
    lets the operator point the row at the right biomaGUNE person so it
    surfaces in that person's corresponding-author view on the SharePoint
    List. Subsequent scans preserve these values until
    ``reset_corresponding_author`` clears the override flag. Mirrors
    ``set_data_contact``.
    """
    result = ApplyResult()
    if not email:
        result.errors.append("set_corresponding_author requires --email")
        return result
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        updates = {
            "publication_id": pub_id,
            "corresponding_author_email": email,
            "corresponding_author_overridden": 1,
        }
        if name is not None:
            updates["corresponding_author_name"] = name
        db.upsert_archive(conn, **updates)
        db.insert_event(
            conn, pub_id, "set_corresponding_author",
            archive["status"], archive["status"], "cli",
            note=f"corresponding_author set to {name or '?'} <{email}>",
        )
        result.applied += 1
    return result


def reset_corresponding_author(config: Config, pub_id: str) -> ApplyResult:
    """Clear the corresponding-author override; the next scan re-seeds from the central DB."""
    result = ApplyResult()
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        db.upsert_archive(
            conn,
            publication_id=pub_id,
            corresponding_author_overridden=0,
        )
        db.insert_event(
            conn, pub_id, "reset_corresponding_author",
            archive["status"], archive["status"], "cli",
            note="corresponding_author override cleared",
        )
        result.applied += 1
    return result


def set_zenodo_code(config: Config, pub_id: str, code: str) -> ApplyResult:
    """Override the Zenodo record code (operator-managed, preserved across scans)."""
    result = ApplyResult()
    if not code:
        result.errors.append("set_zenodo_code requires --code")
        return result
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        db.upsert_archive(
            conn,
            publication_id=pub_id,
            zenodo_code=code,
            zenodo_code_overridden=1,
        )
        db.insert_event(
            conn, pub_id, "set_zenodo_code",
            archive["status"], archive["status"], "cli",
            note=f"zenodo_code set to {code}",
        )
        result.applied += 1
    return result


def reset_zenodo_code(config: Config, pub_id: str) -> ApplyResult:
    """Clear the Zenodo-code override; the next scan re-seeds from the central DB."""
    result = ApplyResult()
    with db.get_connection(config.database) as conn:
        archive = _archive_or_error(conn, pub_id, result)
        if archive is None:
            return result
        db.upsert_archive(
            conn,
            publication_id=pub_id,
            zenodo_code_overridden=0,
        )
        db.insert_event(
            conn, pub_id, "reset_zenodo_code",
            archive["status"], archive["status"], "cli",
            note="zenodo_code override cleared",
        )
        result.applied += 1
    return result


def apply_single(
    config: Config,
    pub_id: str,
    task_code: str,
    done: int = 1,
    pid: str = "",
    url: str = "",
    note: str = "",
) -> tuple[ApplyResult, str | None, str | None]:
    """Apply a single action to one archive, as invoked from the CLI.

    Runs the same per-row logic used by apply_actions, but without TSV
    parsing / history append / sheet rewriting. Returns the accumulated
    ApplyResult plus the (old_status, new_status) tuple so the caller
    can report the transition.
    """
    result = ApplyResult()
    now = _now()
    row = {
        "publication_id": pub_id,
        "task_code": task_code,
        "done": str(done),
        "pid": pid,
        "url": url,
        "note": note,
    }
    with db.get_connection(config.database) as conn:
        _, old_status, new_status = _apply_row(
            conn, row, now, config, "cli", result, "Action"
        )
    return result, old_status, new_status
