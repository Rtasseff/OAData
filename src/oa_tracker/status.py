"""Status constants, task codes, and transition rules."""

from __future__ import annotations

# ── Status constants ──────────────────────────────────────────────────

OPEN_INACTIVE = "OPEN_INACTIVE"
OPEN_ACTIVE = "OPEN_ACTIVE"
OPEN_READY_FOR_ZENODO_DRAFT = "OPEN_READY_FOR_ZENODO_DRAFT"
OPEN_ZENODO_DRAFT_CREATED = "OPEN_ZENODO_DRAFT_CREATED"
OPEN_ZENODO_DRAFT_VALIDATED = "OPEN_ZENODO_DRAFT_VALIDATED"
OPEN_ZENODO_PUBLISHED = "OPEN_ZENODO_PUBLISHED"
OPEN_DB_UPDATED = "OPEN_DB_UPDATED"

CLOSED_DATA_ARCHIVED = "CLOSED_DATA_ARCHIVED"
CLOSED_PUBLICATION_ONLY = "CLOSED_PUBLICATION_ONLY"
CLOSED_EXCEPTION = "CLOSED_EXCEPTION"

ALL_STATUSES = {
    OPEN_INACTIVE,
    OPEN_ACTIVE,
    OPEN_READY_FOR_ZENODO_DRAFT,
    OPEN_ZENODO_DRAFT_CREATED,
    OPEN_ZENODO_DRAFT_VALIDATED,
    OPEN_ZENODO_PUBLISHED,
    OPEN_DB_UPDATED,
    CLOSED_DATA_ARCHIVED,
    CLOSED_PUBLICATION_ONLY,
    CLOSED_EXCEPTION,
}

OPEN_STATUSES = {s for s in ALL_STATUSES if s.startswith("OPEN_")}
CLOSED_STATUSES = {s for s in ALL_STATUSES if s.startswith("CLOSED_")}

# Ordered pipeline stages for display
PIPELINE_ORDER = [
    OPEN_INACTIVE,
    OPEN_ACTIVE,
    OPEN_READY_FOR_ZENODO_DRAFT,
    OPEN_ZENODO_DRAFT_CREATED,
    OPEN_ZENODO_DRAFT_VALIDATED,
    OPEN_ZENODO_PUBLISHED,
    OPEN_DB_UPDATED,
]

# ── Task codes ────────────────────────────────────────────────────────

TASK_CODES = {
    "remind_sent": {
        "description": "Send reminder email to data contact",
        "changes_status": False,
    },
    "contact_pi_manual": {
        "description": "MAX reminder reached; manually contact PI",
        "changes_status": False,
    },
    "qa_pass": {
        "description": "Review uploaded data and approve QA",
        "changes_status": True,
    },
    "qa_hold": {
        "description": "Flag QA issue; add note and keep monitoring",
        "changes_status": False,
    },
    "zenodo_draft_created": {
        "description": "Create Zenodo draft deposit",
        "changes_status": True,
    },
    "zenodo_validated": {
        "description": "Validate Zenodo draft metadata and files",
        "changes_status": True,
    },
    "zenodo_published": {
        "description": "Publish Zenodo record (enter PID and URL)",
        "changes_status": True,
        "requires_pid": True,
    },
    "db_updated": {
        "description": "Update internal publication DB with dataset DOI/URL",
        "changes_status": True,
    },
    "folder_removed": {
        "description": "Confirm SharePoint folder removed; close archive",
        "changes_status": True,
    },
    "close_publication_only": {
        "description": "Close as publication-only (no data deposit needed)",
        "changes_status": True,
    },
    "close_exception": {
        "description": "Close with exception (add note explaining why)",
        "changes_status": True,
    },
    "mandate_missing": {
        "description": "Confirm with PO/IT: OA mandate could not be derived",
        "changes_status": False,
    },
    # Operator-managed override commands. CLI-only (not emitted on the
    # action sheet); each writes an event for the audit log without
    # changing archive status.
    "set_data_contact": {
        "description": "Set the data-contact name/email and mark it as operator-managed",
        "changes_status": False,
    },
    "reset_data_contact": {
        "description": "Clear the data-contact override; auto-seed from corresponding author next scan",
        "changes_status": False,
    },
    "set_zenodo_code": {
        "description": "Set the Zenodo record code and mark it as operator-managed",
        "changes_status": False,
    },
    "reset_zenodo_code": {
        "description": "Clear the Zenodo-code override; auto-seed from central repository next scan",
        "changes_status": False,
    },
    # ── Parallel track: SharePoint List signals ──────────────────────
    # Emitted by `oa sharepoint sync` (the pull path) from user edits on
    # the List. Per feedback_no_auto_state_changes.md they route through
    # the action sheet first; promotion to auto-apply is per signal class.
    # See docs/sharepoint_list_design.md § Action routing.
    "propose_data_contact": {
        "description": "User suggested a different data contact (review, then set)",
        "changes_status": False,
    },
    "propose_exemption": {
        "description": "User proposed an exemption (review; closure depends on category)",
        "changes_status": False,
    },
    "propose_done": {
        "description": "User believes the archive is done (verify before closing)",
        "changes_status": False,
    },
    "user_note": {
        "description": "User left a note on the List (awareness only — recorded, no action)",
        "changes_status": False,
    },
    # "All data archived elsewhere" exemption: the data IS archived (just
    # not via our Zenodo pipeline), so it closes as CLOSED_DATA_ARCHIVED
    # with the EXTERNAL PID/URL recorded — counting in "data archived"
    # totals rather than as an exception. Requires both a PID and a URL.
    "close_archived_external": {
        "description": "Close as archived elsewhere (record external PID + URL)",
        "changes_status": True,
        "requires_pid": True,
    },
    # CLI-only corresponding-author override (mirrors set/reset_data_contact).
    # Lets the operator pin an "effective" corresponding author on rows
    # whose real one is external/blank, so the row surfaces in that
    # person's corresponding-author view on the List.
    "set_corresponding_author": {
        "description": "Set the corresponding-author name/email and mark it operator-managed",
        "changes_status": False,
    },
    "reset_corresponding_author": {
        "description": "Clear the corresponding-author override; auto-seed from the central DB next scan",
        "changes_status": False,
    },
}

# Override commands handled via dedicated paths in actions.py — they do
# not flow through _apply_row / validate_transition.
OVERRIDE_TASK_CODES = frozenset({
    "set_data_contact", "reset_data_contact",
    "set_zenodo_code", "reset_zenodo_code",
    "set_corresponding_author", "reset_corresponding_author",
})

# ── Transitions ───────────────────────────────────────────────────────
# Mapping: (current_status, task_code) → new_status

TRANSITIONS: dict[tuple[str, str], str] = {
    # Scanner-driven (used internally, not from action sheet)
    # (None, "new_inactive"): OPEN_INACTIVE,
    # (OPEN_INACTIVE, "became_active"): OPEN_ACTIVE,

    # Operator-driven
    (OPEN_ACTIVE, "qa_pass"): OPEN_READY_FOR_ZENODO_DRAFT,
    (OPEN_ACTIVE, "qa_hold"): OPEN_ACTIVE,
    (OPEN_READY_FOR_ZENODO_DRAFT, "zenodo_draft_created"): OPEN_ZENODO_DRAFT_CREATED,
    (OPEN_ZENODO_DRAFT_CREATED, "zenodo_validated"): OPEN_ZENODO_DRAFT_VALIDATED,
    (OPEN_ZENODO_DRAFT_VALIDATED, "zenodo_published"): OPEN_ZENODO_PUBLISHED,
    (OPEN_ZENODO_PUBLISHED, "db_updated"): OPEN_DB_UPDATED,
    (OPEN_DB_UPDATED, "folder_removed"): CLOSED_DATA_ARCHIVED,
}

# These can be applied from any OPEN status
_WILDCARD_TASKS = {
    "close_publication_only": CLOSED_PUBLICATION_ONLY,
    "close_exception": CLOSED_EXCEPTION,
    # Data archived in an external repository — closes as archived (the
    # external PID/URL is required and recorded by actions._apply_row).
    "close_archived_external": CLOSED_DATA_ARCHIVED,
}


def validate_transition(current_status: str, task_code: str) -> str:
    """Return the new status for a given transition, or raise ValueError."""
    if task_code not in TASK_CODES:
        raise ValueError(f"Unknown task code: {task_code!r}")

    # remind_sent, qa_hold, contact_pi_manual, and mandate_missing don't
    # change status via the standard transition path. (contact_pi_manual
    # is handled specially in apply_actions when done=1 without a PID,
    # which short-circuits into CLOSED_EXCEPTION.) mandate_missing is an
    # acknowledgment-only task; the row regenerates next scan unless the
    # underlying issue is fixed upstream or the operator changes the
    # task_code to an explicit closure.
    # propose_* are parallel-track acknowledgment signals: they record an
    # event/note for the operator but do not themselves move status.
    # (propose_exemption's eventual closure is applied by re-routing to a
    # concrete close_* code; the bare signal is a no-op until then.)
    if task_code in (
        "remind_sent", "qa_hold", "contact_pi_manual", "mandate_missing",
        "propose_data_contact", "propose_exemption", "propose_done", "user_note",
    ):
        return current_status

    # Check wildcard tasks (any OPEN → CLOSED)
    if task_code in _WILDCARD_TASKS:
        if current_status in OPEN_STATUSES:
            return _WILDCARD_TASKS[task_code]
        raise ValueError(
            f"Cannot apply {task_code!r} to {current_status!r} (not an OPEN status)"
        )

    # Check explicit transitions
    key = (current_status, task_code)
    if key in TRANSITIONS:
        return TRANSITIONS[key]

    raise ValueError(
        f"Invalid transition: {task_code!r} from {current_status!r}"
    )


def next_task_for_status(status: str) -> str | None:
    """Return the most likely next task code for a given status, or None."""
    mapping = {
        OPEN_ACTIVE: "qa_pass",
        OPEN_READY_FOR_ZENODO_DRAFT: "zenodo_draft_created",
        OPEN_ZENODO_DRAFT_CREATED: "zenodo_validated",
        OPEN_ZENODO_DRAFT_VALIDATED: "zenodo_published",
        OPEN_ZENODO_PUBLISHED: "db_updated",
        OPEN_DB_UPDATED: "folder_removed",
    }
    return mapping.get(status)
