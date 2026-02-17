"""Parse action_sheet.tsv, validate transitions, apply completed actions to DB."""

from __future__ import annotations

import csv
import re
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


def apply_actions(sheet_path: Path, config: Config) -> ApplyResult:
    """Read the action sheet, apply rows where done=1, and return results."""
    result = ApplyResult()
    now = _now()

    with open(sheet_path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    applied_indices: list[int] = []

    with db.get_connection(config.database) as conn:
        for i, row in enumerate(rows):
            done = row.get("done", "0").strip()
            if done not in ("1", "2"):
                result.skipped += 1
                continue

            pub_id = row["publication_id"].strip()
            task_code = row["task_code"].strip()
            pid = row.get("pid", "").strip()
            url = row.get("url", "").strip()
            note = row.get("note", "").strip()

            archive = db.get_archive(conn, pub_id)
            if archive is None:
                result.errors.append(f"Row {i+1}: publication {pub_id!r} not in database")
                continue

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
                        f"Row {i+1} ({pub_id}): done=2 with no PID; closing as CLOSED_EXCEPTION"
                    )

                db.update_archive_status(conn, pub_id, new_status, **extra_fields)
                db.insert_event(
                    conn, pub_id, "full_closure", old_status, new_status, "action_sheet",
                    pid=pid or None, url=url or None, note=note or None,
                )
                result.applied += 1
                applied_indices.append(i)
                continue

            # ── done=1 with PID/URL: fast-track to OPEN_ZENODO_PUBLISHED ──
            if (pid or url) and task_code not in ("remind_sent", "qa_hold"):
                if _looks_like_paper_doi(pid):
                    result.warnings.append(
                        f"Row {i+1} ({pub_id}): PID {pid!r} looks like a paper DOI, not a Zenodo DOI"
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
                    conn, pub_id, "fast_track_published", old_status, new_status, "action_sheet",
                    pid=pid or None, url=url or None, note=note or None,
                )
                result.applied += 1
                applied_indices.append(i)
                continue

            # ── done=1 standard flow ──────────────────────────────────

            # Validate transition
            try:
                new_status = st.validate_transition(old_status, task_code)
            except ValueError as e:
                result.errors.append(f"Row {i+1} ({pub_id}): {e}")
                continue

            # Warn if zenodo_published PID looks like a paper DOI
            if task_code == "zenodo_published" and pid and _looks_like_paper_doi(pid):
                result.warnings.append(
                    f"Row {i+1} ({pub_id}): PID {pid!r} looks like a paper DOI, not a Zenodo DOI"
                )

            # Handle remind_sent specially
            if task_code == "remind_sent":
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
                    conn, pub_id, task_code, old_status, old_status, "action_sheet",
                    note=note or None,
                )
                result.applied += 1
                applied_indices.append(i)
                continue

            # Handle qa_hold (no status change, just note)
            if task_code == "qa_hold":
                extra: dict[str, Any] = {}
                if note:
                    existing_notes = archive.get("notes") or ""
                    separator = "\n" if existing_notes else ""
                    extra["notes"] = f"{existing_notes}{separator}[{now}] {note}"
                db.upsert_archive(conn, publication_id=pub_id, **extra)
                db.insert_event(
                    conn, pub_id, task_code, old_status, old_status, "action_sheet",
                    note=note or None,
                )
                result.applied += 1
                applied_indices.append(i)
                continue

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
                    f"Row {i+1} ({pub_id}): No PID on record; closing as CLOSED_EXCEPTION instead of CLOSED_DATA_ARCHIVED"
                )

            db.update_archive_status(conn, pub_id, new_status, **extra_fields)
            db.insert_event(
                conn, pub_id, task_code, old_status, new_status, "action_sheet",
                pid=pid or None, url=url or None, note=note or None,
            )
            result.applied += 1
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
