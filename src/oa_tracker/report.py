"""Generate weekly_report.md from current DB state."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from oa_tracker import db, status as st
from oa_tracker.config import Config


def _now() -> datetime:
    return datetime.now()


def generate_report(config: Config) -> Path:
    """Generate a weekly report and return the file path."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    now = _now()
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    report_path = config.output_dir / "weekly_report.md"

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

        # Reminders due
        reminders_due = db.get_reminders_due(conn, now.isoformat(timespec="seconds"))

        # Pipeline view (by status)
        status_counts = Counter(a["status"] for a in open_archives)

        # Integrity warnings
        missing_folder = [a for a in open_archives if a["unexpected_missing_folder"]]

        # Recently closed
        recent_events = db.get_recent_events(conn, week_ago)
        recently_closed_ids = {
            e["publication_id"] for e in recent_events
            if e["new_status"] and e["new_status"].startswith("CLOSED_")
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
            lines.append(f"- **{a['publication_id']}** — {a['status']} (seen {a['first_seen_at']})")
    else:
        lines.append("_None_")
    lines.append("")

    # 2. Newly active
    lines.append("## Newly Active")
    if newly_active:
        for a in newly_active:
            lines.append(f"- **{a['publication_id']}** — active since {a['became_active_at']}")
    else:
        lines.append("_None_")
    lines.append("")

    # 3. Stuck / long-idle
    lines.append("## Stuck / Long-Idle (OPEN_ACTIVE > 30 days)")
    if stuck:
        for a in stuck:
            days = (now - datetime.fromisoformat(a["became_active_at"])).days
            lines.append(f"- **{a['publication_id']}** — active for {days} days")
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

    report_path.write_text("\n".join(lines))
    return report_path
