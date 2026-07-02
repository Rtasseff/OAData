"""Tests for scanner module."""

import sqlite3

import pytest

from oa_tracker import pub_db
from oa_tracker.db import (
    _SCHEMA_VERSION,
    _V1_TO_V2_ALTERS,
    get_archive,
    get_connection,
    init_db,
    upsert_archive,
)
from oa_tracker.scanner import scan_folders


def test_scan_empty_root(test_config):
    result = scan_folders(test_config)
    assert result.summary == "  No folders found."


def test_scan_new_inactive_folder(test_config):
    (test_config.sharepoint_root / "1001").mkdir()
    result = scan_folders(test_config)
    assert "1001" in result.new_inactive

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "1001")
        assert archive["status"] == "OPEN_INACTIVE"


def test_scan_new_active_folder(test_config):
    pub_dir = test_config.sharepoint_root / "1002"
    pub_dir.mkdir()
    (pub_dir / "data.zip").write_text("content")

    result = scan_folders(test_config)
    assert "1002" in result.new_active

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "1002")
        assert archive["status"] == "OPEN_ACTIVE"
        assert archive["became_active_at"] is not None
        assert archive["next_reminder_at"] is not None


def test_scan_activation(test_config):
    pub_dir = test_config.sharepoint_root / "1003"
    pub_dir.mkdir()

    # First scan: inactive
    result = scan_folders(test_config)
    assert "1003" in result.new_inactive

    # Add a file
    (pub_dir / "readme.txt").write_text("hello")

    # Second scan: activated
    result = scan_folders(test_config)
    assert "1003" in result.activated

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "1003")
        assert archive["status"] == "OPEN_ACTIVE"


def _register_placeholder(config, pub_id, folder, status, **extra):
    """Bootstrap a non-numeric placeholder archive, as the operator does."""
    with get_connection(config.database) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=str(folder),
            first_seen_at="2026-07-01T00:00:00",
            last_seen_at="2026-07-01T00:00:00",
            status=status,
            **extra,
        )


def test_scan_skips_unregistered_non_numeric_folder(test_config):
    # A non-numeric folder with no archive row (e.g. the SharePoint
    # "Attachments" system folder) is junk: reported, never tracked.
    (test_config.sharepoint_root / "Attachments").mkdir()
    result = scan_folders(test_config)
    assert "Attachments" in result.skipped_non_numeric
    with get_connection(test_config.database) as conn:
        assert get_archive(conn, "Attachments") is None


def test_scan_tracks_registered_placeholder_without_enrichment(test_config, monkeypatch):
    folder = test_config.sharepoint_root / "SMN-1"
    folder.mkdir()
    (folder / "raw_data.zip").write_text("x")
    _register_placeholder(
        test_config, "SMN-1", folder, "OPEN_ZENODO_PUBLISHED",
        final_pid="10.5281/zenodo.21108962", pub_title="Placeholder title",
        data_contact_overridden=1,
    )

    # Enrichment must never run for a placeholder (it isn't in the central DB).
    def _boom(_conn, pub_id):
        raise AssertionError(f"enrichment ran for placeholder {pub_id}")

    monkeypatch.setattr(pub_db, "enrich_archive", _boom)

    result = scan_folders(test_config)

    assert "SMN-1" not in result.skipped_non_numeric
    assert "SMN-1" not in result.missing
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "SMN-1")
        assert a["status"] == "OPEN_ZENODO_PUBLISHED"          # unchanged
        assert a["unexpected_missing_folder"] == 0             # not falsely missing
        assert a["pub_title"] == "Placeholder title"           # operator data intact
        assert a["final_pid"] == "10.5281/zenodo.21108962"


def test_scan_activates_registered_placeholder_when_files_appear(test_config):
    folder = test_config.sharepoint_root / "SMN-2"
    folder.mkdir()
    _register_placeholder(test_config, "SMN-2", folder, "OPEN_INACTIVE")

    # Empty folder → stays inactive, tracked (not missing).
    result = scan_folders(test_config)
    assert "SMN-2" in result.unchanged
    assert "SMN-2" not in result.missing
    with get_connection(test_config.database) as conn:
        assert get_archive(conn, "SMN-2")["status"] == "OPEN_INACTIVE"

    # Data uploaded → activates.
    (folder / "raw.zip").write_text("data")
    result = scan_folders(test_config)
    assert "SMN-2" in result.activated
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "SMN-2")
        assert a["status"] == "OPEN_ACTIVE"
        assert a["became_active_at"] is not None


def test_scan_missing_folder(test_config):
    pub_dir = test_config.sharepoint_root / "1004"
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("stuff")

    scan_folders(test_config)

    # Remove the folder
    (pub_dir / "data.txt").unlink()
    pub_dir.rmdir()

    result = scan_folders(test_config)
    assert "1004" in result.missing

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "1004")
        assert archive["unexpected_missing_folder"] == 1
        assert archive["missing_folder_detected_at"] is not None


def test_scan_missing_folder_reappears(test_config):
    pub_dir = test_config.sharepoint_root / "1005"
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("stuff")
    scan_folders(test_config)

    # Remove
    (pub_dir / "data.txt").unlink()
    pub_dir.rmdir()
    scan_folders(test_config)

    # Reappear
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("back")
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "1005")
        assert archive["unexpected_missing_folder"] == 0


def test_scan_nonexistent_root(test_config):
    import shutil
    shutil.rmtree(test_config.sharepoint_root)
    result = scan_folders(test_config)
    assert len(result.errors) == 1


# ── Stage 2: pub-DB enrichment behavior ──────────────────────────────


def _enrich_with(monkeypatch, **fields):
    """Replace pub_db.enrich_archive with a stub returning the given CachedPubFields."""
    base = dict(
        pub_title=None, pub_doi=None, pub_journal=None, pub_year=None,
        oa_paper_required=None, oa_data_required=None,
        max_embargo_months=None, oa_mandate_source=None,
        oa_mandate_missing=False,
        corresponding_author_name=None, corresponding_author_email=None,
        central_repository=None, central_repository_code=None,
        auto_zenodo_code=None,
    )
    base.update(fields)
    cached = pub_db.CachedPubFields(**base)
    monkeypatch.setattr(pub_db, "enrich_archive", lambda _c, _p: cached)


def test_scan_populates_cached_fields_for_new_archive(test_config, monkeypatch):
    _enrich_with(
        monkeypatch,
        pub_title="A real publication", pub_doi="10.1/test", pub_journal="Small",
        pub_year=2025,
        oa_paper_required=True, oa_data_required=True, max_embargo_months=0,
        oa_mandate_source="proj=1410:data(0mo)", oa_mandate_missing=False,
        corresponding_author_name="Author Name",
        central_repository="Zenodo", central_repository_code="999",
        auto_zenodo_code="999",
    )
    pub_dir = test_config.sharepoint_root / "3092"
    pub_dir.mkdir()
    (pub_dir / "data.zip").write_text("content")

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "3092")
    assert a["pub_title"] == "A real publication"
    assert a["pub_doi"] == "10.1/test"
    assert a["pub_year"] == 2025
    assert a["oa_paper_required"] == 1
    assert a["oa_data_required"] == 1
    assert a["max_embargo_months"] == 0
    assert a["oa_mandate_missing"] == 0
    assert a["central_repository"] == "Zenodo"
    assert a["pub_db_last_refreshed_at"] is not None


def test_scan_seeds_data_contact_from_corresponding_author(test_config, monkeypatch):
    _enrich_with(monkeypatch, corresponding_author_name="Foo Bar")
    pub_dir = test_config.sharepoint_root / "1100"
    pub_dir.mkdir()

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1100")
    # Name is seeded from the central DB; email is always TBD until operator sets.
    assert a["data_contact_name"] == "Foo Bar"
    assert a["data_contact_email"] == "TBD"
    assert a["data_contact_overridden"] == 0


def test_scan_data_contact_email_tbd_when_no_central_author(test_config, monkeypatch):
    _enrich_with(monkeypatch, corresponding_author_name=None)
    pub_dir = test_config.sharepoint_root / "1101"
    pub_dir.mkdir()
    (pub_dir / "x.txt").write_text("x")

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1101")
    assert a["data_contact_name"] is None
    assert a["data_contact_email"] == "TBD"


def test_scan_seeds_zenodo_code_only_when_central_repo_is_zenodo(test_config, monkeypatch):
    _enrich_with(monkeypatch, auto_zenodo_code="ABC")
    pub_dir = test_config.sharepoint_root / "1102"
    pub_dir.mkdir()
    (pub_dir / "y.txt").write_text("y")

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1102")
    assert a["zenodo_code"] == "ABC"


def test_scan_does_not_overwrite_overridden_data_contact(test_config, monkeypatch):
    """If operator has set data_contact_overridden=1, scan must not re-seed."""
    _enrich_with(monkeypatch, corresponding_author_name="Original Author")
    pub_dir = test_config.sharepoint_root / "1103"
    pub_dir.mkdir()
    scan_folders(test_config)

    # Simulate operator override
    with get_connection(test_config.database) as conn:
        conn.execute(
            "UPDATE archives SET data_contact_name=?, data_contact_email=?, data_contact_overridden=1 "
            "WHERE publication_id='1103'",
            ("Operator Set", "ops@example.org"),
        )

    # Re-scan with a different cached author — should NOT touch the override
    _enrich_with(monkeypatch, corresponding_author_name="Different Author")
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1103")
    assert a["data_contact_name"] == "Operator Set"
    assert a["data_contact_email"] == "ops@example.org"
    # Cache itself still refreshes — only the operator-managed copy is preserved
    assert a["corresponding_author_name"] == "Different Author"


def test_scan_does_not_overwrite_overridden_zenodo_code(test_config, monkeypatch):
    _enrich_with(monkeypatch, auto_zenodo_code="111")
    pub_dir = test_config.sharepoint_root / "1104"
    pub_dir.mkdir()
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        conn.execute(
            "UPDATE archives SET zenodo_code=?, zenodo_code_overridden=1 WHERE publication_id='1104'",
            ("operator_override",),
        )

    _enrich_with(monkeypatch, auto_zenodo_code="222")
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1104")
    assert a["zenodo_code"] == "operator_override"


def test_scan_does_not_overwrite_overridden_corresponding_author(test_config, monkeypatch):
    """An operator-pinned effective corresponding author survives a rescan."""
    _enrich_with(monkeypatch, corresponding_author_name="DB Author",
                 corresponding_author_email="dbauthor@cicbiomagune.es")
    pub_dir = test_config.sharepoint_root / "1105"
    pub_dir.mkdir()
    scan_folders(test_config)

    # Operator pins an effective CA (e.g. the real one is external/blank).
    with get_connection(test_config.database) as conn:
        conn.execute(
            "UPDATE archives SET corresponding_author_name=?, "
            "corresponding_author_email=?, corresponding_author_overridden=1 "
            "WHERE publication_id='1105'",
            ("Effective PI", "pi@cicbiomagune.es"),
        )

    # Re-scan with a different cached author — the override must hold.
    _enrich_with(monkeypatch, corresponding_author_name="Changed Author",
                 corresponding_author_email="changed@cicbiomagune.es")
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1105")
    assert a["corresponding_author_name"] == "Effective PI"
    assert a["corresponding_author_email"] == "pi@cicbiomagune.es"


def test_scan_continues_when_pub_db_unreachable(test_config, monkeypatch):
    """A connection failure adds an error but the scan still runs."""
    def _fail():
        raise ConnectionError("simulated MySQL outage")
    monkeypatch.setattr(pub_db, "get_connection", _fail)

    pub_dir = test_config.sharepoint_root / "1105"
    pub_dir.mkdir()
    (pub_dir / "f.txt").write_text("f")

    result = scan_folders(test_config)

    assert any("pub-DB unreachable" in e for e in result.errors)
    assert "1105" in result.new_active

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1105")
    # Cached fields stay NULL when pub-DB is down; archive still gets baseline defaults.
    assert a["status"] == "OPEN_ACTIVE"
    assert a["pub_title"] is None
    assert a["pub_db_last_refreshed_at"] is None
    assert a["data_contact_email"] == "TBD"
    assert a["data_contact_overridden"] == 0


def test_scan_per_pub_lookup_failure_does_not_stop_other_archives(test_config, monkeypatch):
    """A bad enrichment for one archive shouldn't break others."""
    calls = {"n": 0}

    def _flaky(_conn, pub_id):
        calls["n"] += 1
        if pub_id == "9001":
            raise RuntimeError("boom")
        return pub_db.CachedPubFields(
            pub_title=f"title-{pub_id}", pub_doi=None, pub_journal=None, pub_year=None,
            oa_paper_required=None, oa_data_required=None,
            max_embargo_months=None, oa_mandate_source=None,
            oa_mandate_missing=False,
            corresponding_author_name=None, corresponding_author_email=None,
            central_repository=None, central_repository_code=None,
            auto_zenodo_code=None,
        )
    monkeypatch.setattr(pub_db, "enrich_archive", _flaky)

    (test_config.sharepoint_root / "9001").mkdir()
    (test_config.sharepoint_root / "9002").mkdir()
    result = scan_folders(test_config)

    assert "9001" in result.new_inactive
    assert "9002" in result.new_inactive
    assert any("9001" in e for e in result.errors)

    with get_connection(test_config.database) as conn:
        good = get_archive(conn, "9002")
        bad = get_archive(conn, "9001")
    assert good["pub_title"] == "title-9002"
    assert bad["pub_title"] is None  # enrichment failed but row still created


# ── Stage 2: schema migration ────────────────────────────────────────


def test_migration_v1_to_v2_adds_columns(tmp_path):
    """init_db on an existing v1 database should add v2 columns."""
    # Simulate a v1 database by hand (only the columns from the v1 schema).
    db_path = tmp_path / "v1.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE archives (
            publication_id          TEXT PRIMARY KEY,
            folder_path             TEXT NOT NULL,
            first_seen_at           TEXT NOT NULL,
            became_active_at        TEXT,
            last_seen_at            TEXT NOT NULL,
            last_changed_at         TEXT,
            status                  TEXT NOT NULL,
            final_pid               TEXT,
            final_url               TEXT,
            notes                   TEXT,
            last_notified_at        TEXT,
            reminder_count          INTEGER NOT NULL DEFAULT 0,
            next_reminder_at        TEXT,
            unexpected_missing_folder INTEGER NOT NULL DEFAULT 0,
            missing_folder_detected_at TEXT
        );
        CREATE TABLE events (
            event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            publication_id  TEXT NOT NULL,
            action_code     TEXT NOT NULL,
            old_status      TEXT,
            new_status      TEXT,
            pid             TEXT,
            url             TEXT,
            note            TEXT,
            source          TEXT NOT NULL
        );
        INSERT INTO schema_version (version) VALUES (1);
        INSERT INTO archives (publication_id, folder_path, first_seen_at, last_seen_at, status)
            VALUES ('LEGACY1', '/x', '2024-01-01', '2024-01-01', 'OPEN_INACTIVE');
    """)
    conn.commit()
    conn.close()

    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Schema version updated
        ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert ver == _SCHEMA_VERSION

        # All v2 columns now exist on archives
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(archives)")}
        for stmt in _V1_TO_V2_ALTERS:
            # extract the column name after "ADD COLUMN "
            col = stmt.split("ADD COLUMN ")[1].split()[0]
            assert col in cols, f"missing column after migration: {col}"

        # Existing row preserved
        row = conn.execute("SELECT * FROM archives WHERE publication_id='LEGACY1'").fetchone()
        assert row is not None
        assert row["status"] == "OPEN_INACTIVE"
        # New columns default to NULL or 0 for existing rows
        assert row["pub_title"] is None
        assert row["data_contact_overridden"] == 0
        assert row["zenodo_code_overridden"] == 0
    finally:
        conn.close()


def test_scan_schedules_initial_reminder_for_new_inactive_folder(test_config):
    """A newly-detected empty folder must get next_reminder_at set so it
    actually shows up on the action sheet — otherwise it sits forever."""
    (test_config.sharepoint_root / "1010").mkdir()
    scan_folders(test_config)
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1010")
    assert a["status"] == "OPEN_INACTIVE"
    assert a["next_reminder_at"] is not None  # scheduled, not NULL


def test_scan_backfills_reminder_for_legacy_inactive(test_config):
    """OPEN_INACTIVE archive with NULL next_reminder_at and reminder_count=0
    (created before the fix) gets backfilled from first_seen_at on next scan."""
    pub_dir = test_config.sharepoint_root / "1011"
    pub_dir.mkdir()

    # Insert directly to simulate a pre-fix archive with NULL next_reminder_at
    with get_connection(test_config.database) as conn:
        conn.execute("""
            INSERT INTO archives (publication_id, folder_path, first_seen_at,
                                  last_seen_at, status, reminder_count,
                                  next_reminder_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
        """, ("1011", str(pub_dir), "2026-01-01T00:00:00",
              "2026-01-15T00:00:00", "OPEN_INACTIVE", 0))

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1011")
    assert a["next_reminder_at"] is not None
    # Backfilled from first_seen_at, not from now — should be old (due).
    assert a["next_reminder_at"].startswith("2026-01-")


def test_scan_does_not_backfill_reminder_when_count_is_nonzero(test_config):
    """If reminder_count > 0, the archive is in manual-contact territory
    or otherwise managed — don't backfill, leave next_reminder_at as-is."""
    pub_dir = test_config.sharepoint_root / "1012"
    pub_dir.mkdir()
    with get_connection(test_config.database) as conn:
        conn.execute("""
            INSERT INTO archives (publication_id, folder_path, first_seen_at,
                                  last_seen_at, status, reminder_count,
                                  next_reminder_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
        """, ("1012", str(pub_dir), "2026-01-01T00:00:00",
              "2026-01-15T00:00:00", "OPEN_INACTIVE", 3))

    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "1012")
    assert a["next_reminder_at"] is None  # left alone


def test_scan_skips_non_numeric_folder_with_warning(test_config):
    """SharePoint system folders (e.g. 'Attachments') are not real
    publication IDs; the scanner should skip them and surface them in
    the result's skipped_non_numeric list so the operator sees them."""
    (test_config.sharepoint_root / "Attachments").mkdir()
    (test_config.sharepoint_root / "3092").mkdir()
    (test_config.sharepoint_root / "PUB-WITH-DASH").mkdir()

    result = scan_folders(test_config)

    assert "Attachments" in result.skipped_non_numeric
    assert "PUB-WITH-DASH" in result.skipped_non_numeric
    assert "3092" not in result.skipped_non_numeric
    assert "3092" in result.new_inactive  # numeric → still scanned

    # Summary mentions the skipped folders so it lands on the terminal
    assert "non-numeric" in result.summary.lower()
    assert "Attachments" in result.summary

    # No archive row was created for the skipped folder
    with get_connection(test_config.database) as conn:
        assert get_archive(conn, "Attachments") is None
        assert get_archive(conn, "PUB-WITH-DASH") is None
        assert get_archive(conn, "3092") is not None


def test_init_db_is_idempotent(tmp_path):
    """Calling init_db twice should be a no-op for a fresh v2 DB."""
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # Should still have exactly one schema_version row at v2
        rows = conn.execute("SELECT version FROM schema_version ORDER BY rowid").fetchall()
        assert [r[0] for r in rows] == [_SCHEMA_VERSION]
    finally:
        conn.close()


# ── v4 automation: package detection ─────────────────────────────────

def test_scan_detects_zip_and_readme(test_config):
    from oa_tracker.scanner import scan_folders
    from oa_tracker.db import get_archive, get_connection

    folder = test_config.sharepoint_root / "4001"
    folder.mkdir()
    (folder / "Datasets_articleDOI-x.zip").write_bytes(b"zipdata")
    (folder / "README.txt").write_text("readme")

    scan_folders(test_config)
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "4001")
    assert a["package_has_zip"] == 1
    assert a["package_has_readme"] == 1
    assert a["package_checked_at"]


def test_scan_detects_readme_inside_zip(test_config):
    import io
    import zipfile
    from oa_tracker.scanner import scan_folders
    from oa_tracker.db import get_archive, get_connection

    folder = test_config.sharepoint_root / "4002"
    folder.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Datasets_ZIP/README.txt", "inside")
        zf.writestr("Datasets_ZIP/data.csv", "a,b")
    (folder / "Datasets_articleDOI-y.zip").write_bytes(buf.getvalue())

    scan_folders(test_config)
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "4002")
    assert a["package_has_zip"] == 1
    assert a["package_has_readme"] == 1


def test_scan_flags_incomplete_package(test_config):
    from oa_tracker.scanner import scan_folders
    from oa_tracker.db import get_archive, get_connection

    folder = test_config.sharepoint_root / "4003"
    folder.mkdir()
    (folder / "loose_data.csv").write_text("a,b")

    scan_folders(test_config)
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "4003")
    assert a["package_has_zip"] == 0
    assert a["package_has_readme"] == 0


def test_rescan_updates_package_state(test_config):
    from oa_tracker.scanner import scan_folders
    from oa_tracker.db import get_archive, get_connection

    folder = test_config.sharepoint_root / "4004"
    folder.mkdir()
    (folder / "loose_data.csv").write_text("a,b")
    scan_folders(test_config)

    (folder / "data.zip").write_bytes(b"z")
    (folder / "readme.TXT").write_text("case-insensitive")
    scan_folders(test_config)
    with get_connection(test_config.database) as conn:
        a = get_archive(conn, "4004")
    assert a["package_has_zip"] == 1
    assert a["package_has_readme"] == 1
