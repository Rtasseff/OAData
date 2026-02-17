"""Generate action_sheet.tsv from current DB state."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

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
            first_seen = archive.get("first_seen_at") or ""
            next_rem = archive.get("next_reminder_at") or ""
            rem_count = str(archive.get("reminder_count") or 0)

            # Add reminder task if due
            if pub_id in reminders_due:
                rows.append({
                    "publication_id": pub_id,
                    "current_status": cur_status,
                    "task_code": "remind_sent",
                    "task_text": st.TASK_CODES["remind_sent"]["description"],
                    "first_seen_at": first_seen,
                    "next_reminder_at": next_rem,
                    "reminder_count": rem_count,
                    "done": "0",
                    "pid": "",
                    "url": "",
                    "note": "",
                })

            # Add the next pipeline task
            next_task = st.next_task_for_status(cur_status)
            if next_task:
                meta = st.TASK_CODES[next_task]
                rows.append({
                    "publication_id": pub_id,
                    "current_status": cur_status,
                    "task_code": next_task,
                    "task_text": meta["description"],
                    "first_seen_at": first_seen,
                    "next_reminder_at": next_rem,
                    "reminder_count": rem_count,
                    "done": "0",
                    "pid": "",
                    "url": "",
                    "note": "",
                })

    with open(sheet_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    return sheet_path
