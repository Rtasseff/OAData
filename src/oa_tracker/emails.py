"""Generate email drafts from templates."""

from __future__ import annotations

from pathlib import Path
from string import Template

from oa_tracker import db, status as st
from oa_tracker.config import Config


def _load_template(template_dir: Path, name: str) -> Template:
    """Load a template file and return a string.Template."""
    path = template_dir / name
    return Template(path.read_text())


def generate_emails(config: Config) -> list[Path]:
    """Generate email drafts and return list of created file paths."""
    drafts_dir = config.email_drafts_dir
    drafts_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    reminder_tpl = _load_template(config.template_dir, "reminder.txt")
    completion_tpl = _load_template(config.template_dir, "completion.txt")

    with db.get_connection(config.database) as conn:
        # Reminder emails for archives with reminders due
        reminders_due = db.get_reminders_due(conn)
        for archive in reminders_due:
            pub_id = archive["publication_id"]
            n = archive["reminder_count"] + 1
            draft_path = drafts_dir / f"reminder_{pub_id}_{n}.txt"
            content = reminder_tpl.safe_substitute(
                publication_id=pub_id,
                reminder_number=n,
                became_active_at=archive.get("became_active_at") or "unknown",
                current_status=archive["status"],
            )
            draft_path.write_text(content)
            generated.append(draft_path)

        # Completion emails for recently published archives
        published = db.get_all_archives(conn, status_filter=st.OPEN_ZENODO_PUBLISHED)
        for archive in published:
            pub_id = archive["publication_id"]
            draft_path = drafts_dir / f"completion_{pub_id}.txt"
            content = completion_tpl.safe_substitute(
                publication_id=pub_id,
                final_pid=archive.get("final_pid") or "(pending)",
                final_url=archive.get("final_url") or "(pending)",
            )
            draft_path.write_text(content)
            generated.append(draft_path)

    return generated
