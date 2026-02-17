"""Walk SharePoint folder tree, detect new/active/missing folders, update DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from oa_tracker import db, status as st
from oa_tracker.config import Config


@dataclass
class ScanResult:
    new_inactive: list[str] = field(default_factory=list)
    new_active: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = []
        if self.new_inactive:
            parts.append(f"  New (inactive): {len(self.new_inactive)}")
        if self.new_active:
            parts.append(f"  New (active):   {len(self.new_active)}")
        if self.activated:
            parts.append(f"  Activated:      {len(self.activated)}")
        if self.changed:
            parts.append(f"  Changed:        {len(self.changed)}")
        if self.missing:
            parts.append(f"  Missing:        {len(self.missing)}")
        if self.unchanged:
            parts.append(f"  Unchanged:      {len(self.unchanged)}")
        if self.errors:
            parts.append(f"  Errors:         {len(self.errors)}")
        return "\n".join(parts) if parts else "  No folders found."


def _folder_has_files(folder: Path) -> bool:
    """Check if a folder contains any files (recursively)."""
    try:
        return any(p.is_file() for p in folder.rglob("*"))
    except PermissionError:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compute_next_reminder(config: Config, became_active_at: str | None) -> str | None:
    """Compute the first reminder datetime based on when the folder became active."""
    if not became_active_at:
        return None
    active_dt = datetime.fromisoformat(became_active_at)
    return (active_dt + timedelta(days=config.reminders.first_reminder_days)).isoformat(timespec="seconds")


def scan_folders(config: Config) -> ScanResult:
    """Scan the SharePoint root and update the database."""
    result = ScanResult()
    now = _now()
    root = config.sharepoint_root

    if not root.is_dir():
        result.errors.append(f"SharePoint root not found: {root}")
        return result

    # Collect all publication folders (immediate subdirectories)
    found_ids: set[str] = set()

    with db.get_connection(config.database) as conn:
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue

            pub_id = folder.name
            found_ids.add(pub_id)
            has_files = _folder_has_files(folder)

            existing = db.get_archive(conn, pub_id)

            if existing is None:
                # New folder
                if has_files:
                    next_reminder = _compute_next_reminder(config, now)
                    db.upsert_archive(
                        conn,
                        publication_id=pub_id,
                        folder_path=str(folder),
                        first_seen_at=now,
                        became_active_at=now,
                        last_seen_at=now,
                        last_changed_at=now,
                        status=st.OPEN_ACTIVE,
                        next_reminder_at=next_reminder,
                    )
                    db.insert_event(
                        conn, pub_id, "new_active", None, st.OPEN_ACTIVE, "scanner"
                    )
                    result.new_active.append(pub_id)
                else:
                    db.upsert_archive(
                        conn,
                        publication_id=pub_id,
                        folder_path=str(folder),
                        first_seen_at=now,
                        last_seen_at=now,
                        status=st.OPEN_INACTIVE,
                    )
                    db.insert_event(
                        conn, pub_id, "new_inactive", None, st.OPEN_INACTIVE, "scanner"
                    )
                    result.new_inactive.append(pub_id)
            else:
                # Existing folder — update last_seen, check transitions
                updates: dict = {"last_seen_at": now}

                # Clear missing-folder flag if it was set
                if existing["unexpected_missing_folder"]:
                    updates["unexpected_missing_folder"] = 0
                    updates["missing_folder_detected_at"] = None

                if existing["status"] == st.OPEN_INACTIVE and has_files:
                    # Became active
                    updates["status"] = st.OPEN_ACTIVE
                    updates["became_active_at"] = now
                    updates["last_changed_at"] = now
                    updates["next_reminder_at"] = _compute_next_reminder(config, now)
                    db.upsert_archive(conn, publication_id=pub_id, **updates)
                    db.insert_event(
                        conn, pub_id, "became_active", st.OPEN_INACTIVE, st.OPEN_ACTIVE, "scanner"
                    )
                    result.activated.append(pub_id)
                elif has_files and existing["status"] != st.OPEN_INACTIVE:
                    # Still active — just update timestamps
                    db.upsert_archive(conn, publication_id=pub_id, **updates)
                    result.changed.append(pub_id)
                else:
                    db.upsert_archive(conn, publication_id=pub_id, **updates)
                    result.unchanged.append(pub_id)

        # Check for missing folders (OPEN archives not found in scan)
        open_archives = db.get_open_archives(conn)
        for archive in open_archives:
            pid = archive["publication_id"]
            if pid not in found_ids and not archive["unexpected_missing_folder"]:
                db.upsert_archive(
                    conn,
                    publication_id=pid,
                    unexpected_missing_folder=1,
                    missing_folder_detected_at=now,
                )
                db.insert_event(
                    conn, pid, "folder_missing", archive["status"], archive["status"], "scanner",
                    note="Folder not found during scan",
                )
                result.missing.append(pid)

    return result
