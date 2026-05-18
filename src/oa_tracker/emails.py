"""Generate email drafts and the Zenodo cheat sheet from templates."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any

from oa_tracker import db, status as st
from oa_tracker.config import Config


# Window for re-generating completion drafts after closure. This catches
# archives that used the done=2 shortcut and bypassed OPEN_ZENODO_PUBLISHED
# entirely — the operator still needs a completion email to send to the
# data contact, and the draft is regenerated each weekly run until the
# closure ages past this window.
_RECENT_CLOSURE_DAYS = 14


_STATUS_FRIENDLY = {
    st.OPEN_INACTIVE: "Inactive (no files yet uploaded)",
    st.OPEN_ACTIVE: "Active (files uploaded, awaiting QA)",
    st.OPEN_READY_FOR_ZENODO_DRAFT: "QA passed (ready to create Zenodo draft)",
    st.OPEN_ZENODO_DRAFT_CREATED: "Zenodo draft created",
    st.OPEN_ZENODO_DRAFT_VALIDATED: "Zenodo draft validated (ready to publish)",
    st.OPEN_ZENODO_PUBLISHED: "Published on Zenodo",
    st.OPEN_DB_UPDATED: "Published on Zenodo and recorded in internal DB",
    st.CLOSED_DATA_ARCHIVED: "Closed (data archived)",
    st.CLOSED_PUBLICATION_ONLY: "Closed (publication only)",
    st.CLOSED_EXCEPTION: "Closed (exception)",
}

# Statuses that warrant a Zenodo cheat sheet — the operator is about to
# (or already has) created/validated a draft and benefits from the
# consolidated metadata.
_CHEAT_STATUSES = {
    st.OPEN_READY_FOR_ZENODO_DRAFT,
    st.OPEN_ZENODO_DRAFT_CREATED,
    st.OPEN_ZENODO_DRAFT_VALIDATED,
}

_PROTOCOL_URL = (
    "https://biomagune.sharepoint.com/:w:/s/ResearchDataManagement/"
    "IQBZr-ga4BCNQpXNesqWrKkIAbQ64o7l1RYH3iBm0fEgd-0?e=5IhaD6"
)

_SHAREPOINT_FOLDER_BASE = (
    "https://biomagune.sharepoint.com/sites/PublicationsData/"
    "Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FPublicationsData"
    "%2FShared%20Documents%2F"
)


def _load_template(template_dir: Path, name: str) -> Template:
    return Template((template_dir / name).read_text())


def _friendly_status(status: str) -> str:
    return _STATUS_FRIENDLY.get(status, status)


def _flags_description(archive: dict[str, Any]) -> str:
    """Render the OA-mandate flags as a single line for emails/reports.

    Reflects the Stage 2 classification — same source of truth as the
    action sheet. Pre-Stage-2 archives (no enrichment timestamp) report
    the requirement as not-yet-determined so emails don't claim more
    than we know.
    """
    if not archive.get("pub_db_last_refreshed_at"):
        return "(mandate classification pending — pub-DB not yet queried for this archive)"

    if archive.get("oa_mandate_missing") == 1:
        return "(mandate not yet determined — needs PO/IT confirmation)"

    data_req = archive.get("oa_data_required")
    paper_req = archive.get("oa_paper_required")
    embargo = archive.get("max_embargo_months")
    embargo_str = "" if embargo is None else f" (max embargo: {embargo} months)"

    if data_req == 1:
        return f"Open Data Required{embargo_str}"
    if data_req == 0 and paper_req == 0:
        return "No OA required by mandate"
    if paper_req == 1:
        return f"Paper OA required, data not required by mandate{embargo_str}"
    return "(mandate signal ambiguous)"


def _data_required(archive: dict[str, Any]) -> bool:
    """True only when the cached classification says data is required.

    Pre-Stage-2 archives (no refresh timestamp) are treated as
    data-required for back-compat: existing reminder behavior is
    preserved until they go through one enrichment scan.
    """
    if not archive.get("pub_db_last_refreshed_at"):
        return True
    return archive.get("oa_data_required") == 1


def _cheat_template_vars(archive: dict[str, Any], now_str: str) -> dict[str, str]:
    """Build the substitution map for the Zenodo cheat sheet."""
    def _or_none(v):
        return str(v) if v not in (None, "", "TBD") else "(none)"

    central_repo = archive.get("central_repository")
    central_code = archive.get("central_repository_code")
    central_str = (
        f"{central_repo}"
        + (f" (code: {central_code})" if central_code else "")
        if central_repo
        else "(none recorded centrally)"
    )

    return {
        "publication_id": archive["publication_id"],
        "publication_title": archive.get("pub_title") or "unknown",
        "publication_doi": archive.get("pub_doi") or "unknown",
        "publication_journal": archive.get("pub_journal") or "unknown",
        "publication_year": str(archive.get("pub_year") or "unknown"),
        "data_contact_name": archive.get("data_contact_name") or "unknown",
        "data_contact_email": archive.get("data_contact_email") or "TBD",
        "oa_paper_required": "Yes" if archive.get("oa_paper_required") == 1
        else ("No" if archive.get("oa_paper_required") == 0 else "Unknown"),
        "oa_data_required": "Yes" if archive.get("oa_data_required") == 1
        else ("No" if archive.get("oa_data_required") == 0 else "Unknown"),
        "max_embargo_months": (
            str(archive["max_embargo_months"])
            if archive.get("max_embargo_months") is not None
            else "n/a"
        ),
        "mandate_trace": archive.get("oa_mandate_source") or "(none)",
        "central_repository_summary": central_str,
        "zenodo_code": _or_none(archive.get("zenodo_code")),
        "folder_path": archive.get("folder_path") or "(unknown)",
        "protocol_url": _PROTOCOL_URL,
        "generated_at": now_str,
    }


def _common_template_vars(archive: dict[str, Any]) -> dict[str, str]:
    """Variables shared by reminder + completion templates."""
    return {
        "publication_id": archive["publication_id"],
        "publication_title": archive.get("pub_title") or "(title pending)",
        "data_contact_name": archive.get("data_contact_name") or "data contact",
        "data_contact_email": archive.get("data_contact_email") or "TBD",
        "oa_status": _friendly_status(archive["status"]),
        "flags": _flags_description(archive),
        "current_status": archive["status"],
        "became_active_at": archive.get("became_active_at") or "unknown",
        "sharepoint_folder_url": _SHAREPOINT_FOLDER_BASE + archive["publication_id"],
        "protocol_url": _PROTOCOL_URL,
    }


def generate_emails(config: Config) -> list[Path]:
    """Generate reminder/completion drafts and Zenodo cheat sheets."""
    drafts_dir = config.email_drafts_dir
    drafts_dir.mkdir(parents=True, exist_ok=True)
    cheat_dir = config.output_dir / "zenodo_cheat"
    cheat_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    now_str = datetime.now().isoformat(timespec="seconds")

    reminder_tpl = _load_template(config.template_dir, "reminder.txt")
    completion_tpl = _load_template(config.template_dir, "completion.txt")
    cheat_tpl_path = config.template_dir / "zenodo_cheat.txt"
    cheat_tpl = Template(cheat_tpl_path.read_text()) if cheat_tpl_path.exists() else None

    with db.get_connection(config.database) as conn:
        reminders_due = db.get_reminders_due(conn)
        max_rem = config.reminders.max_reminders
        for archive in reminders_due:
            # Suppress reminders when the central mandate says data isn't
            # actually required — same suppression rule the action sheet
            # uses. The operator still sees the archive on the sheet.
            if not _data_required(archive):
                continue
            # Manual-contact stage: no automated reminder; the action
            # sheet emits a contact_pi_manual row instead.
            if (archive.get("reminder_count") or 0) >= max_rem - 1:
                continue
            pub_id = archive["publication_id"]
            n = archive["reminder_count"] + 1
            draft_path = drafts_dir / f"reminder_{pub_id}_{n}.txt"
            vars_ = _common_template_vars(archive)
            vars_["reminder_number"] = str(n)
            content = reminder_tpl.safe_substitute(**vars_)
            draft_path.write_text(content)
            generated.append(draft_path)

        def _write_completion_draft(archive: dict[str, Any]) -> None:
            pub_id = archive["publication_id"]
            draft_path = drafts_dir / f"completion_{pub_id}.txt"
            vars_ = _common_template_vars(archive)
            vars_["final_pid"] = archive.get("final_pid") or "(pending)"
            vars_["final_url"] = archive.get("final_url") or "(pending)"
            content = completion_tpl.safe_substitute(**vars_)
            draft_path.write_text(content)
            generated.append(draft_path)

        # 1) Archives published on Zenodo but not yet closed — operator
        # is mid-flow and needs the email to send out.
        for archive in db.get_all_archives(conn, status_filter=st.OPEN_ZENODO_PUBLISHED):
            _write_completion_draft(archive)

        # 2) Archives that were fully closed (CLOSED_DATA_ARCHIVED) in the
        # recent window. Covers the done=2 shortcut path where the
        # archive jumps straight to closed without going through
        # OPEN_ZENODO_PUBLISHED. We use the events log to find the
        # closure timestamp because `last_changed_at` isn't always
        # updated on closure events (full_closure / folder_removed don't
        # touch it). After _RECENT_CLOSURE_DAYS the draft stops
        # regenerating; if the operator still needs it later they can
        # craft the email by hand from the archive's recorded
        # final_pid/final_url.
        cutoff = (datetime.now() - timedelta(days=_RECENT_CLOSURE_DAYS)).isoformat(
            timespec="seconds"
        )
        recent_close_events = db.get_recent_events(conn, cutoff)
        recently_closed_pubs = {
            e["publication_id"] for e in recent_close_events
            if e["new_status"] == st.CLOSED_DATA_ARCHIVED
        }
        for archive in db.get_all_archives(conn, status_filter=st.CLOSED_DATA_ARCHIVED):
            if archive["publication_id"] not in recently_closed_pubs:
                continue
            if not archive.get("final_pid"):
                continue  # nothing to communicate to the data contact
            _write_completion_draft(archive)

        # Zenodo cheat sheets — one per archive in any draft-stage status.
        if cheat_tpl is not None:
            for status in _CHEAT_STATUSES:
                for archive in db.get_all_archives(conn, status_filter=status):
                    pub_id = archive["publication_id"]
                    cheat_path = cheat_dir / f"{pub_id}.txt"
                    content = cheat_tpl.safe_substitute(
                        **_cheat_template_vars(archive, now_str)
                    )
                    cheat_path.write_text(content)
                    generated.append(cheat_path)

    return generated
