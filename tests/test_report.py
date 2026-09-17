"""Tests for report generation module."""

from datetime import datetime, timedelta

from oa_tracker.db import get_connection, upsert_archive, insert_event
from oa_tracker.report import generate_report
from oa_tracker.status import OPEN_ACTIVE, OPEN_INACTIVE, CLOSED_DATA_ARCHIVED


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).isoformat(timespec="seconds")


def test_empty_report(test_config):
    path = generate_report(test_config)
    assert path.exists()
    content = path.read_text()
    assert "Weekly Report" in content
    assert "Total open: 0" in content


def test_report_new_this_week(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB001",
            folder_path="/tmp/pub001",
            first_seen_at=_now_iso(),
            last_seen_at=_now_iso(),
            status=OPEN_INACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB001" in content
    assert "New This Week" in content


def test_report_newly_active(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB002",
            folder_path="/tmp/pub002",
            first_seen_at=_days_ago(10),
            last_seen_at=_now_iso(),
            became_active_at=_now_iso(),
            status=OPEN_ACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB002" in content
    assert "Newly Active" in content


def test_report_stuck(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB003",
            folder_path="/tmp/pub003",
            first_seen_at=_days_ago(60),
            last_seen_at=_now_iso(),
            became_active_at=_days_ago(45),
            status=OPEN_ACTIVE,
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB003" in content
    assert "Stuck" in content


def test_report_missing_folder(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB004",
            folder_path="/tmp/pub004",
            first_seen_at=_days_ago(10),
            last_seen_at=_days_ago(2),
            status=OPEN_ACTIVE,
            unexpected_missing_folder=1,
            missing_folder_detected_at=_days_ago(2),
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB004" in content
    assert "Integrity Warnings" in content


def test_report_recently_closed(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(
            conn,
            publication_id="PUB005",
            folder_path="/tmp/pub005",
            first_seen_at=_days_ago(30),
            last_seen_at=_days_ago(1),
            status=CLOSED_DATA_ARCHIVED,
            final_pid="10.5281/zenodo.999",
        )
        insert_event(
            conn,
            publication_id="PUB005",
            action_code="folder_removed",
            old_status="OPEN_DB_UPDATED",
            new_status=CLOSED_DATA_ARCHIVED,
            source="action_sheet",
        )
    path = generate_report(test_config)
    content = path.read_text()
    assert "PUB005" in content
    assert "Recently Closed" in content


def test_report_pipeline_view(test_config):
    with get_connection(test_config.database) as conn:
        for i in range(3):
            upsert_archive(
                conn,
                publication_id=f"OPEN{i}",
                folder_path=f"/tmp/open{i}",
                first_seen_at=_days_ago(10),
                last_seen_at=_now_iso(),
                status=OPEN_ACTIVE,
            )
    path = generate_report(test_config)
    content = path.read_text()
    assert "OPEN_ACTIVE: 3" in content


# ── Stage 2: Mandate Issues section ───────────────────────────────────


def _enriched(db_path, pub_id, status, **enrichment):
    enrichment.setdefault("pub_db_last_refreshed_at", "2026-05-07T00:00:00")
    enrichment.setdefault("first_seen_at", "2026-01-01T00:00:00")
    enrichment.setdefault("last_seen_at", "2026-01-15T00:00:00")
    from oa_tracker.db import upsert_archive
    with get_connection(db_path) as conn:
        upsert_archive(
            conn,
            publication_id=pub_id,
            folder_path=f"/tmp/{pub_id}",
            status=status,
            **enrichment,
        )


def test_report_includes_mandate_issues_section_with_missing(test_config):
    from oa_tracker.status import OPEN_ACTIVE
    _enriched(
        test_config.database, "MAND1", OPEN_ACTIVE,
        oa_mandate_missing=1, oa_data_required=None, oa_paper_required=None,
        oa_mandate_source="proj=505:unknown",
    )
    path = generate_report(test_config)
    content = path.read_text()
    assert "## Mandate Issues" in content
    assert "MAND1" in content
    assert "no mandate derivable" in content
    assert "proj=505:unknown" in content


def test_report_mandate_issues_empty_when_no_missing(test_config):
    from oa_tracker.status import OPEN_ACTIVE
    _enriched(
        test_config.database, "OK1", OPEN_ACTIVE,
        oa_mandate_missing=0, oa_data_required=1, oa_paper_required=1,
    )
    path = generate_report(test_config)
    content = path.read_text()
    # Section header present but empty
    assert "## Mandate Issues" in content
    issues_section = content.split("## Mandate Issues")[1].split("##")[0]
    assert "_None_" in issues_section


def test_report_inline_annotation_shows_mandate_label(test_config):
    from oa_tracker.status import OPEN_ACTIVE
    _enriched(
        test_config.database, "ANN1", OPEN_ACTIVE,
        first_seen_at=_now_iso(), became_active_at=_now_iso(),
        oa_mandate_missing=0, oa_data_required=1, oa_paper_required=1,
    )
    path = generate_report(test_config)
    content = path.read_text()
    # Annotation appears under New This Week / Newly Active entries
    assert "mandate: Open Data Required" in content


def test_report_done_ticks_contacts_and_closed_folders(test_config):
    with get_connection(test_config.database) as conn:
        base = dict(first_seen_at=_days_ago(40), last_seen_at=_now_iso(),
                    oa_data_required=1, oa_mandate_missing=0,
                    pub_db_last_refreshed_at=_now_iso())
        upsert_archive(conn, publication_id="3259", folder_path="/tmp/3259",
                       status=OPEN_INACTIVE, user_done_flag=1,
                       user_done_at="2026-07-13T17:41:37",
                       data_contact_email="lcardo@cicbiomagune.es", **base)
        upsert_archive(conn, publication_id="3194", folder_path="/tmp/3194",
                       status=OPEN_ACTIVE, became_active_at=_days_ago(40),
                       user_done_flag=1, package_has_zip=1, package_has_readme=1,
                       package_has_manuscript=0,
                       data_contact_email="pblesio@cicbiomagune.es", **base)
        upsert_archive(conn, publication_id="3316", folder_path="/tmp/3316",
                       status=OPEN_INACTIVE, data_contact_email="TBD", **base)
        upsert_archive(conn, publication_id="3296", folder_path="/tmp/3296",
                       status="CLOSED_PUBLICATION_ONLY", **base)
        insert_event(conn, "3296", "closed_folder_present",
                     "CLOSED_PUBLICATION_ONLY", "CLOSED_PUBLICATION_ONLY", "scanner")

    content = generate_report(test_config).read_text()
    done = content.split("## Tracker 'Done' Ticked — Needs Review")[1].split("##")[0]
    assert "**3259**" in done and "reject — the publication folder is still empty" in done
    assert "**3194**" in done and "missing manuscript" in done
    contacts = content.split("## Missing Data Contact")[1].split("##")[0]
    assert "**3316**" in contacts and "**3259**" not in contacts
    cleanup = content.split("## Closed — SharePoint Folder Still to Delete")[1].split("##")[0]
    assert "**3296**" in cleanup


def test_report_new_sections_empty(test_config):
    content = generate_report(test_config).read_text()
    for heading in ("## Tracker 'Done' Ticked — Needs Review", "## Missing Data Contact",
                    "## Closed — SharePoint Folder Still to Delete"):
        assert content.split(heading)[1].split("##")[0].strip() == "_None_"


def test_recently_closed_ignores_events_on_already_closed_archives(test_config):
    with get_connection(test_config.database) as conn:
        upsert_archive(conn, publication_id="3249", folder_path="/tmp/3249",
                       first_seen_at=_days_ago(200), last_seen_at=_days_ago(60),
                       status=CLOSED_DATA_ARCHIVED)
        insert_event(conn, "3249", "completion_sent",
                     CLOSED_DATA_ARCHIVED, CLOSED_DATA_ARCHIVED, "cli")
    content = generate_report(test_config).read_text()
    closed = content.split("## Recently Closed")[1].split("##")[0]
    assert "3249" not in closed


def test_reminders_due_lists_only_author_owned_statuses(test_config):
    with get_connection(test_config.database) as conn:
        for pid, status in (("3302", OPEN_ACTIVE), ("3194", "OPEN_ZENODO_DRAFT_CREATED")):
            upsert_archive(conn, publication_id=pid, folder_path=f"/tmp/{pid}",
                           first_seen_at=_days_ago(90), last_seen_at=_now_iso(),
                           status=status, next_reminder_at=_days_ago(5))
    content = generate_report(test_config).read_text()
    due = content.split("## Reminders Due")[1].split("##")[0]
    assert "**3302**" in due and "3194" not in due
