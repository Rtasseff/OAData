"""Generate action_sheet.tsv from current DB state."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from oa_tracker import db, status as st
from oa_tracker.config import Config

SHEET_COLUMNS = [
    "publication_id",
    "current_status",
    "task_code",
    "task_text",
    "first_seen_at",
    "next_reminder_at",
    "reminder_count",
    "done",
    "pid",
    "url",
    "note",
]


def _mandate_classification(archive: dict[str, Any]) -> tuple[str, str]:
    """Derive the action-sheet treatment for an archive from its cached
    pub-DB flags.

    Returns ``(category, auto_note)``. Categories:

    - ``"mandate_missing"`` — no rule applied; emit a single
      ``mandate_missing`` row asking the operator to confirm with
      PO/IT before doing anything else.
    - ``"close_no_oa"`` — explicit No-OA (paper and data both not
      required); emit a single ``close_publication_only`` row.
    - ``"paper_only"`` — paper required but no data; the standard
      pipeline rows still emit (operator decides QA/close), with a
      note flagging the paper-only nature.
    - ``"data_required"`` — standard flow, no auto-note.
    - ``"unclassified"`` — pub-DB has never enriched this archive
      (e.g., never reached, or this is a legacy v1 row pre-migration).
      Treat as standard flow so the tracker still works.
    """
    refreshed = archive.get("pub_db_last_refreshed_at")
    if not refreshed:
        return ("unclassified", "")

    if archive.get("oa_mandate_missing") == 1:
        return (
            "mandate_missing",
            "No mandate found in cff_oaMandate or AEI rule — "
            "investigate before closing.",
        )

    data_req = archive.get("oa_data_required")
    paper_req = archive.get("oa_paper_required")

    if data_req == 0 and paper_req == 0:
        return (
            "close_no_oa",
            "No OA mandate on linked project(s); no data archiving required.",
        )

    if data_req != 1 and paper_req == 1:
        # data_req is 0 (paper-only on every project) or NULL with a
        # paper-only signal — treated identically: workflow continues
        # but the operator should know data isn't actually mandated.
        return (
            "paper_only",
            "PAPER ONLY: data not required by mandate; "
            "processing as if data were required.",
        )

    if data_req == 1:
        return ("data_required", "")

    # Anything else (mix of `no_oa` + `unknown` contributions, or any
    # state where we can't conclude data_req=1 or data_req=0+paper_req=0
    # or paper-only) is ambiguous. Surface it like a missing mandate so
    # the operator can confirm with PO/IT before doing anything.
    return (
        "mandate_missing",
        "Mandate signal ambiguous (mixed no-OA and unknown projects) — "
        "confirm with PO/IT before closing or pursuing.",
    )


def _row(archive: dict[str, Any], task_code: str, task_text: str, note: str = "") -> dict[str, str]:
    """Build a sheet row dict with the standard column population."""
    return {
        "publication_id": archive["publication_id"],
        "current_status": archive["status"],
        "task_code": task_code,
        "task_text": task_text,
        "first_seen_at": archive.get("first_seen_at") or "",
        "next_reminder_at": archive.get("next_reminder_at") or "",
        "reminder_count": str(archive.get("reminder_count") or 0),
        "done": "0",
        "pid": "",
        "url": "",
        "note": note,
    }


def generate_sheet(config: Config) -> Path:
    """Generate action_sheet.tsv for all OPEN archives and return the file path."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = config.output_dir / "action_sheet.tsv"
    now_str = datetime.now().isoformat(timespec="seconds")

    rows: list[dict[str, str]] = []

    with db.get_connection(config.database) as conn:
        open_archives = db.get_open_archives(conn)
        reminders_due = {
            a["publication_id"] for a in db.get_reminders_due(conn, now_str)
        }

        for archive in open_archives:
            pub_id = archive["publication_id"]
            cur_status = archive["status"]
            category, auto_note = _mandate_classification(archive)

            # Mandate-missing and explicit no-OA archives produce a single
            # actionable row each — nothing else (no pipeline progression,
            # no reminders) until the operator addresses the situation.
            if category == "mandate_missing":
                rows.append(_row(
                    archive,
                    "mandate_missing",
                    st.TASK_CODES["mandate_missing"]["description"],
                    note=auto_note,
                ))
                continue

            if category == "close_no_oa":
                rows.append(_row(
                    archive,
                    "close_publication_only",
                    st.TASK_CODES["close_publication_only"]["description"],
                    note=auto_note,
                ))
                continue

            # Paper-only archives that sit empty (OPEN_INACTIVE) have no
            # natural next action — reminders are suppressed because the
            # mandate doesn't require data, and there's no pipeline row
            # to emit. Surface a close_publication_only row so the
            # operator can choose to close (or leave done=0 to wait).
            # Once the folder activates (OPEN_ACTIVE), we fall through
            # to the normal pipeline path with the paper-only side note.
            if category == "paper_only" and cur_status == st.OPEN_INACTIVE:
                rows.append(_row(
                    archive,
                    "close_publication_only",
                    st.TASK_CODES["close_publication_only"]["description"],
                    note=(
                        "PAPER ONLY mandate: data not required and folder still empty — "
                        "consider closing as publication-only."
                    ),
                ))
                continue

            # Reminders fire only when data is actually required by mandate
            # (or when we don't have classification info — legacy rows
            # behave as before so existing flows aren't broken).
            allow_reminders = category in ("data_required", "unclassified")
            if pub_id in reminders_due and allow_reminders:
                reached_max = (
                    archive.get("reminder_count") or 0
                ) >= config.reminders.max_reminders - 1
                task = "contact_pi_manual" if reached_max else "remind_sent"
                rows.append(_row(
                    archive, task, st.TASK_CODES[task]["description"],
                ))

            next_task = st.next_task_for_status(cur_status)
            if next_task:
                meta = st.TASK_CODES[next_task]
                rows.append(_row(
                    archive, next_task, meta["description"], note=auto_note,
                ))

    with open(sheet_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    return sheet_path
