"""Walk SharePoint folder tree, detect new/active/missing folders, update DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from oa_tracker import db, pub_db, status as st
from oa_tracker.config import Config


@dataclass
class ScanResult:
    new_inactive: list[str] = field(default_factory=list)
    new_active: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped_non_numeric: list[str] = field(default_factory=list)
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
        if self.skipped_non_numeric:
            parts.append(
                f"  Skipped (non-numeric folder names): {len(self.skipped_non_numeric)}"
            )
            for name in self.skipped_non_numeric:
                parts.append(f"    - {name!r}")
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


def _bool_to_int(b: bool | None) -> int | None:
    """SQLite stores booleans as 0/1; preserve None for unknown."""
    if b is None:
        return None
    return 1 if b else 0


def _enrichment_kwargs(
    cached: pub_db.CachedPubFields,
    existing: dict[str, Any] | None,
    now: str,
) -> dict[str, Any]:
    """Translate enrichment + override state into upsert kwargs.

    Auto-refreshed columns are always written. Operator-managed columns
    (data_contact_*, zenodo_code) are re-seeded from the cache only when
    their *_overridden flag is 0 (or the row is new).
    """
    kw: dict[str, Any] = {
        "pub_title": cached.pub_title,
        "pub_doi": cached.pub_doi,
        "pub_journal": cached.pub_journal,
        "pub_year": cached.pub_year,
        "oa_paper_required": _bool_to_int(cached.oa_paper_required),
        "oa_data_required": _bool_to_int(cached.oa_data_required),
        "max_embargo_months": cached.max_embargo_months,
        "oa_mandate_source": cached.oa_mandate_source,
        "oa_mandate_missing": _bool_to_int(cached.oa_mandate_missing),
        "corresponding_author_name": cached.corresponding_author_name,
        "corresponding_author_email": cached.corresponding_author_email,
        "central_repository": cached.central_repository,
        "central_repository_code": cached.central_repository_code,
        "pub_db_last_refreshed_at": now,
    }

    data_contact_overridden = bool(existing and existing.get("data_contact_overridden"))
    if not data_contact_overridden:
        kw["data_contact_name"] = cached.corresponding_author_name
        # email column is always None from the central DB (no source path),
        # so default to literal 'TBD' until the operator overrides.
        kw["data_contact_email"] = cached.corresponding_author_email or "TBD"

    zenodo_overridden = bool(existing and existing.get("zenodo_code_overridden"))
    if not zenodo_overridden:
        kw["zenodo_code"] = cached.auto_zenodo_code

    return kw


def _new_archive_defaults() -> dict[str, Any]:
    """Defaults applied to new archive rows when pub-DB enrichment is unavailable."""
    return {
        "data_contact_email": "TBD",
        "data_contact_overridden": 0,
        "zenodo_code_overridden": 0,
    }


def scan_folders(config: Config) -> ScanResult:
    """Scan the SharePoint root and update the database."""
    result = ScanResult()
    now = _now()
    root = config.sharepoint_root

    if not root.is_dir():
        result.errors.append(f"SharePoint root not found: {root}")
        return result

    # Open one pub-DB connection for the scan. Failure is non-fatal: we
    # continue with stale cached fields. Per-publication failures inside
    # the loop are also caught so one bad lookup doesn't stop the scan.
    pub_conn = None
    try:
        pub_conn = pub_db.get_connection()
    except Exception as e:
        result.errors.append(f"pub-DB unreachable; cached fields not refreshed this scan: {e}")

    found_ids: set[str] = set()

    try:
        with db.get_connection(config.database) as conn:
            for folder in sorted(root.iterdir()):
                if not folder.is_dir():
                    continue

                pub_id = folder.name
                # Publication IDs in the central DB are integer-valued.
                # Anything that doesn't look like a publication ID (e.g.,
                # the SharePoint "Attachments" system folder) is flagged
                # for operator review rather than silently scanned.
                if not pub_id.isdigit():
                    result.skipped_non_numeric.append(pub_id)
                    continue
                found_ids.add(pub_id)
                has_files = _folder_has_files(folder)

                existing = db.get_archive(conn, pub_id)

                enriched: dict[str, Any] = {}
                if pub_conn is not None:
                    try:
                        cached = pub_db.enrich_archive(pub_conn, pub_id)
                        enriched = _enrichment_kwargs(cached, existing, now)
                    except Exception as e:
                        result.errors.append(f"pub-DB lookup failed for {pub_id}: {e}")

                if existing is None:
                    # New archive — fill in operator-managed defaults if
                    # enrichment didn't already provide them.
                    for k, v in _new_archive_defaults().items():
                        enriched.setdefault(k, v)

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
                            **enriched,
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
                            **enriched,
                        )
                        db.insert_event(
                            conn, pub_id, "new_inactive", None, st.OPEN_INACTIVE, "scanner"
                        )
                        result.new_inactive.append(pub_id)
                else:
                    # Existing folder — update last_seen, check transitions
                    updates: dict[str, Any] = {"last_seen_at": now, **enriched}

                    # Clear missing-folder flag if it was set
                    if existing["unexpected_missing_folder"]:
                        updates["unexpected_missing_folder"] = 0
                        updates["missing_folder_detected_at"] = None

                    if existing["status"] == st.OPEN_INACTIVE and has_files:
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
    finally:
        if pub_conn is not None:
            try:
                pub_conn.close()
            except Exception:
                pass

    return result
