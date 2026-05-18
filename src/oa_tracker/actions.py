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
    if task_code in ("qa_hold", "mandate_missing"):
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
