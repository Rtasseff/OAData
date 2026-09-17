"""Generate weekly_report.md from current DB state."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from oa_tracker import db, status as st
from oa_tracker.checks import has_data_contact, missing_package_items, reject_reasons
from oa_tracker.config import Config


def _mandate_label(archive: dict[str, Any]) -> str:
    """Short mandate label for inline annotation."""
    if not archive.get("pub_db_last_refreshed_at"):
        return "mandate: not yet derived"
    if archive.get("oa_mandate_missing") == 1:
        return "mandate: MISSING"
    data_req = archive.get("oa_data_required")
    paper_req = archive.get("oa_paper_required")
    if data_req == 1:
        return "mandate: Open Data Required"
    if data_req == 0 and paper_req == 0:
        return "mandate: No OA"
    if paper_req == 1:
        return "mandate: paper-only"
    return "mandate: ambiguous"


def _now() -> datetime:
    return datetime.now()


def _done_tick_assessment(archive: dict[str, Any]) -> str:
    """One line on what a Tracker 'done' tick means for this archive."""
    reasons = reject_reasons(archive)
    if reasons:
        return f"reject — {'; '.join(reasons)} (reject_done on the sheet)"
    if archive["status"] == st.OPEN_ACTIVE:
        missing = missing_package_items(archive)
        if missing:
            return f"folder is missing {', '.join(missing)} — QA manually"
        return "package complete — auto-QC picks it up"
    return "folder empty but data not mandated — review"


def build_report(config: Config) -> str:
    """The weekly report as Markdown text, from the current DB state.
    Read-only — shared by ``generate_report`` (writes the file) and the
    web UI (renders it live)."""
    now = _now()
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")

    with db.get_connection(config.database) as conn:
        all_archives = db.get_all_archives(conn)
        open_archives = [a for a in all_archives if a["status"].startswith("OPEN_")]
        closed_archives = [a for a in all_archives if a["status"].startswith("CLOSED_")]

        # New this week
        new_this_week = [
            a for a in all_archives
            if a["first_seen_at"] and a["first_seen_at"] >= week_ago
        ]

        # Newly active this week
        newly_active = [
            a for a in all_archives
            if a["became_active_at"] and a["became_active_at"] >= week_ago
        ]

        # Stuck / long-idle (OPEN_ACTIVE for > 30 days with no change)
        stuck_threshold = (now - timedelta(days=30)).isoformat(timespec="seconds")
        stuck = [
            a for a in open_archives
            if a["status"] == st.OPEN_ACTIVE
            and a.get("became_active_at")
            and a["became_active_at"] < stuck_threshold
        ]

        # Reminders due — author-owned statuses only; past QA the stale
        # next_reminder_at is ignored everywhere else too (sheet, emails).
        reminders_due = [
            a for a in db.get_reminders_due(conn, now.isoformat(timespec="seconds"))
            if a["status"] in (st.OPEN_INACTIVE, st.OPEN_ACTIVE)
        ]

        # Pipeline view (by status)
        status_counts = Counter(a["status"] for a in open_archives)

        # Integrity warnings
        missing_folder = [a for a in open_archives if a["unexpected_missing_folder"]]

        # Mandate issues: every OPEN archive whose pub-DB classification
        # came back missing. The operator should confirm with PO/IT
        # before closing or pursuing.
        mandate_issues = [
            a for a in open_archives if a.get("oa_mandate_missing") == 1
        ]

        # Tracker 'done' ticks still waiting on us (author-owned statuses).
        done_ticked = [
            a for a in open_archives
            if a.get("user_done_flag")
            and a["status"] in (st.OPEN_INACTIVE, st.OPEN_ACTIVE)
        ]

        # Author-owned archives with nobody to write to.
        no_contact = [
            a for a in open_archives
            if a["status"] in (st.OPEN_INACTIVE, st.OPEN_ACTIVE)
            and not has_data_contact(a)
        ]

        # Closed archives whose SharePoint folder still exists.
        folder_cleanup = [
            a for a in closed_archives
            if db.get_pending_folder_cleanup(conn, a["publication_id"]) is not None
        ]

        # Recently closed
        recent_events = db.get_recent_events(conn, week_ago)
        # Transitions into CLOSED only (not events logged on closed archives).
        recently_closed_ids = {
            e["publication_id"] for e in recent_events
            if e["new_status"] and e["new_status"].startswith("CLOSED_")
            and e["old_status"] != e["new_status"]
        }
        recently_closed = [a for a in closed_archives if a["publication_id"] in recently_closed_ids]

    # Build report
    lines: list[str] = []
    lines.append(f"# Weekly Report — {now.strftime('%Y-%m-%d')}")
    lines.append("")

    # 1. New this week
    lines.append("## New This Week")
    if new_this_week:
        for a in new_this_week:
            lines.append(
                f"- **{a['publication_id']}** — {a['status']} (seen {a['first_seen_at']})"
            )
            lines.append(f"    - {_mandate_label(a)}")
    else:
        lines.append("_None_")
    lines.append("")

    # 2. Newly active
    lines.append("## Newly Active")
    if newly_active:
        for a in newly_active:
            lines.append(f"- **{a['publication_id']}** — active since {a['became_active_at']}")
            lines.append(f"    - {_mandate_label(a)}")
    else:
        lines.append("_None_")
    lines.append("")

    # 3. Stuck / long-idle
    lines.append("## Stuck / Long-Idle (OPEN_ACTIVE > 30 days)")
    if stuck:
        for a in stuck:
            days = (now - datetime.fromisoformat(a["became_active_at"])).days
            lines.append(f"- **{a['publication_id']}** — active for {days} days")
            lines.append(f"    - {_mandate_label(a)}")
    else:
        lines.append("_None_")
    lines.append("")

    # 4. Reminders due
    lines.append("## Reminders Due")
    if reminders_due:
        for a in reminders_due:
            lines.append(
                f"- **{a['publication_id']}** — reminder #{a['reminder_count'] + 1} "
                f"(due {a['next_reminder_at']})"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # 4b. Tracker 'done' ticks awaiting review
    lines.append("## Tracker 'Done' Ticked — Needs Review")
    if done_ticked:
        for a in done_ticked:
            lines.append(
                f"- **{a['publication_id']}** — {a['status']}; ticked "
                f"{a.get('user_done_at') or 'unknown'}: {_done_tick_assessment(a)}"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # 4c. Missing data contact
    lines.append("## Missing Data Contact")
    if no_contact:
        for a in no_contact:
            lines.append(
                f"- **{a['publication_id']}** — {a['status']}; data contact "
                f"{a.get('data_contact_email') or '(blank)'} — no reminders can go out; "
                f"`oa action {a['publication_id']} set_data_contact --email <email> "
                "--name <name>`"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # 5. Pipeline view
    lines.append("## Ready Queue (Pipeline View)")
    for s in st.PIPELINE_ORDER:
        count = status_counts.get(s, 0)
        lines.append(f"- {s}: {count}")
    lines.append("")

    # 6. Integrity warnings
    lines.append("## Integrity Warnings")
    if missing_folder:
        for a in missing_folder:
            lines.append(
                f"- **{a['publication_id']}** — folder missing since "
                f"{a.get('missing_folder_detected_at', 'unknown')}, status: {a['status']}"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # 7. Mandate issues — confirm with PO/IT
    lines.append("## Mandate Issues — confirm with PO/IT")
    if mandate_issues:
        for a in mandate_issues:
            lines.append(
                f"- **{a['publication_id']}** — {a['status']}; "
                f"no mandate derivable (refreshed "
                f"{a.get('pub_db_last_refreshed_at') or 'unknown'})"
            )
            trace = a.get("oa_mandate_source")
            if trace:
                lines.append(f"    - trace: `{trace}`")
    else:
        lines.append("_None_")
    lines.append("")

    # 7b. Close-out: closed archives whose folder still exists
    lines.append("## Closed — SharePoint Folder Still to Delete")
    if folder_cleanup:
        for a in folder_cleanup:
            lines.append(
                f"- **{a['publication_id']}** — {a['status']}; delete the SharePoint "
                "folder to finish the close-out"
            )
    else:
        lines.append("_None_")
    lines.append("")

    # 7. Recently closed
    lines.append("## Recently Closed")
    if recently_closed:
        for a in recently_closed:
            pid_info = f", PID: {a['final_pid']}" if a.get("final_pid") else ""
            lines.append(f"- **{a['publication_id']}** — {a['status']}{pid_info}")
    else:
        lines.append("_None_")
    lines.append("")

    # 8. Summary stats
    lines.append("## Summary")
    lines.append(f"- Total open: {len(open_archives)}")
    lines.append(f"- Total closed: {len(closed_archives)}")
    lines.append(f"- Total tracked: {len(all_archives)}")
    lines.append("")

    return "\n".join(lines)


def generate_report(config: Config) -> Path:
    """Generate a weekly report and return the file path."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = config.output_dir / "weekly_report.md"
    report_path.write_text(build_report(config))
    return report_path
