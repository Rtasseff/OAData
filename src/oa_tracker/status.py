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
    "qa_pass": {
        "description": "QA complete; ready for Zenodo draft",
        "changes_status": True,
    },
    "qa_hold": {
        "description": "QA not passed; add note and keep monitoring",
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
        "description": "Publish Zenodo record",
        "changes_status": True,
        "requires_pid": True,
    },
    "db_updated": {
        "description": "Update internal publication DB with dataset DOI/URL",
        "changes_status": True,
    },
    "folder_removed": {
        "description": "Remove SharePoint folder; close archive",
        "changes_status": True,
    },
    "close_publication_only": {
        "description": "Close as publication-only (no data deposit needed)",
        "changes_status": True,
    },
    "close_exception": {
        "description": "Close with exception (note strongly encouraged)",
        "changes_status": True,
    },
}

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
}


def validate_transition(current_status: str, task_code: str) -> str:
    """Return the new status for a given transition, or raise ValueError."""
    if task_code not in TASK_CODES:
        raise ValueError(f"Unknown task code: {task_code!r}")

    # remind_sent and qa_hold don't change status
    if task_code in ("remind_sent", "qa_hold"):
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
