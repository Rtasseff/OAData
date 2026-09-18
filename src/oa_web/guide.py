"""What each pending action means for the operator, and which buttons record it.

One ``ActionSpec`` per task code the action sheet can emit. The wording
follows docs/sop.md §7–8; the buttons map onto the same task codes the
action sheet uses, so the web UI can never do something ``oa apply``
couldn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Button:
    choice: str             # posted back as "choice"
    label: str
    apply_code: str = ""    # task code applied; "" → the row's own code
    style: str = "primary"  # primary | danger | secondary
    needs_note: bool = False
    needs_pid_url: bool = False  # asks for the dataset DOI + URL (recorded as final_pid/final_url)
    confirm: str = ""       # browser confirm() text for irreversible steps


@dataclass(frozen=True)
class ActionSpec:
    title: str
    steps: tuple[str, ...]
    buttons: tuple[Button, ...]
    fields: tuple[str, ...] = ()        # extra inputs: "zenodo_code"
    email_stem: str = ""                # "{pub_id}"/"{n}" template of the draft's file stem
    note_hint: str = "Optional note (recorded in the audit log)"
    links: tuple[str, ...] = field(default=())  # "folder" | "zenodo" | "doi"


# Offered wherever the normal path would have the system create the
# Zenodo draft: the deposit was made by hand, or the data lives in another
# repository (collaborations). Applies archived_external: an exemption
# from OUR deposit, not from the process — the archive jumps to
# "Published — update database" and the completion email, institutional-DB
# entry and folder removal still follow as steps.
ELSEWHERE = Button(
    "elsewhere", "Record the DOI and URL — deposited elsewhere",
    apply_code="archived_external", style="secondary", needs_pid_url=True,
)
ELSEWHERE_STEP = (
    "If the data is already deposited somewhere else — a Zenodo record made "
    "by hand, or another repository (common in collaborations) — do not "
    "create a draft: record that deposit's DOI and URL instead under "
    "“Deposited elsewhere” below. The remaining steps (completion email, "
    "publication database, folder removal) then follow as usual."
)

_EMAIL_STEPS = (
    "Open the draft below (it opens in Outlook as a ready-to-send message), "
    "review it and send it.",
    "Then confirm here that it was sent — the draft and this action stop "
    "recurring once it is recorded.",
)

SPECS: dict[str, ActionSpec] = {
    "qa_pass": ActionSpec(
        title="Quality check the uploaded data",
        steps=(
            "Open the SharePoint folder and review what the data contact uploaded.",
            "The package must be: one .zip with the datasets, a README.txt beside "
            "the zip, and a version of the manuscript (.doc/.docx/.pdf) beside the zip.",
            "Pass → the system creates the Zenodo draft and uploads the package "
            "at the next automatic run; you review and publish it later. Fail → "
            "the archive stays where it is; say what is wrong in the note so it "
            "is on record (the data contact hears about it through the next "
            "reminder or your own email).",
            ELSEWHERE_STEP,
        ),
        buttons=(
            Button("pass", "QA pass — system creates the Zenodo draft"),
            Button("fail", "QA fail — keep waiting", apply_code="qa_hold",
                   style="danger", needs_note=True),
            ELSEWHERE,
        ),
        note_hint="Note — required for a fail: what is missing or wrong",
        links=("folder",),
    ),
    "remind_sent": ActionSpec(
        title="Send the reminder email",
        steps=_EMAIL_STEPS,
        buttons=(Button("done", "Email sent"),),
        email_stem="reminder_{pub_id}_{n}",
    ),
    "contact_pi_manual": ActionSpec(
        title="Contact the PI personally (maximum reminders reached)",
        steps=(
            "The scheduled reminders have run out. Contact the PI yourself — "
            "personal email, phone or in person. The past-due draft below can be "
            "used as-is or as a skeleton.",
            "Confirming logs the contact and brings this item back at the next "
            "reminder interval; it never closes the archive.",
            "To give up on the deposit instead, use “Close as exception” "
            "under Other actions, with a note explaining why.",
        ),
        buttons=(Button("done", "PI contacted"),),
        email_stem="reminder_{pub_id}_{n}_PASTDUE",
        note_hint="Optional note — what was said or agreed",
    ),
    "handover_sent": ActionSpec(
        title="Send the handover notice to the new data contact",
        steps=_EMAIL_STEPS,
        buttons=(Button("done", "Email sent"),),
        email_stem="handover_{pub_id}",
    ),
    "completion_sent": ActionSpec(
        title="Send the completion email (data archived)",
        steps=_EMAIL_STEPS,
        buttons=(Button("done", "Email sent"),),
        email_stem="completion_{pub_id}",
    ),
    "reject_done_sent": ActionSpec(
        title="Send the “not done yet” email",
        steps=_EMAIL_STEPS,
        buttons=(Button("done", "Email sent"),),
        email_stem="reject_done_{pub_id}",
    ),
    "reject_done": ActionSpec(
        title="Reject the Tracker “done” tick",
        steps=(
            "The data contact ticked “done” on the SharePoint Tracker, but it "
            "cannot be right (see the note — typically the folder is empty).",
            "Rejecting clears the tick (on the Tracker at the next automatic run) "
            "and prepares a “not done yet” email for you to send.",
        ),
        buttons=(Button("done", "Reject the tick", style="danger"),),
        links=("folder",),
    ),
    "zenodo_create_draft": ActionSpec(
        title="Create the Zenodo draft",
        steps=(
            "QA has passed. The next automatic run creates the draft on Zenodo "
            "(metadata from the publication database, a reserved DOI) and "
            "uploads the package — or create it now with the button. Nothing is "
            "published at this step.",
            ELSEWHERE_STEP,
        ),
        buttons=(Button("done", "Create the Zenodo draft now"), ELSEWHERE),
        links=("folder",),
    ),
    # Only offered when the system cannot create the draft itself (Zenodo
    # integration off, non-numeric publication id, or a record already on
    # file): the normal workflow never asks anyone to make a draft by hand.
    "zenodo_draft_created": ActionSpec(
        title="Zenodo draft made by hand",
        steps=(
            "The system cannot create this draft itself (Zenodo integration is "
            "off for this paper). If you created the deposit on Zenodo yourself, "
            "enter its numeric record id (e.g. 20268493 — not the DOI) so the "
            "tracker can find the DOI and URL later, then confirm.",
            ELSEWHERE_STEP,
        ),
        buttons=(Button("done", "Draft created on Zenodo"), ELSEWHERE),
        fields=("zenodo_code",),
        links=("folder",),
    ),
    "zenodo_validated": ActionSpec(
        title="Review the Zenodo draft and publish it",
        steps=(
            "Open the draft on Zenodo and check the metadata and the files.",
            "If it is good, click Publish on Zenodo. That is the permanent step "
            "and it is always yours.",
            "Then confirm here. The DOI and URL shown below are the ones the "
            "system reserved when it made the draft — confirming checks the "
            "record really is public and records them; nothing to type. If it "
            "is not published yet you get a message and nothing changes.",
        ),
        buttons=(Button("done", "Published on Zenodo — confirm"),),
        links=("zenodo", "folder", "expected"),
    ),
    "zenodo_publish": ActionSpec(
        title="Publish the validated Zenodo draft",
        steps=(
            "The draft has been validated. This publishes it on Zenodo through "
            "the API and mints the DOI — it cannot be undone.",
            "If you already clicked Publish on Zenodo yourself, do not use this "
            "button; ask the administrator to record it instead.",
        ),
        buttons=(Button(
            "done", "Publish on Zenodo now", style="danger",
            confirm="Publish this record on Zenodo? This mints a permanent DOI "
                    "and cannot be undone.",
        ),),
        links=("zenodo",),
    ),
    "zenodo_published": ActionSpec(
        title="Record the published Zenodo deposit",
        steps=(
            "Publish the draft on Zenodo, then record the dataset DOI "
            "(10.5281/zenodo.…) and the record URL. They are pre-filled when "
            "the tracker knows the record — check them against Zenodo.",
            "Use the dataset's DOI — never the paper's DOI.",
        ),
        buttons=(Button("done", "Record DOI and URL", needs_pid_url=True),),
        links=("zenodo",),
    ),
    "db_updated": ActionSpec(
        title="Enter the dataset into the institutional publication database",
        steps=(
            "Open this publication in the institutional publication database and "
            "enter the dataset DOI and URL shown below (repository: Zenodo).",
            "Confirm here once it is saved.",
        ),
        buttons=(Button("done", "Database updated"),),
        links=("doi", "pubdb"),
    ),
    "folder_removed": ActionSpec(
        title="Delete the SharePoint folder and close",
        steps=(
            "Everything is archived and recorded. Delete this publication's "
            "folder on SharePoint (the tracker never deletes anything itself).",
            "Confirming closes the archive as “data archived”.",
        ),
        buttons=(Button("done", "Folder deleted — close archive"),),
        links=("folder", "doi"),
    ),
    "closed_folder_removed": ActionSpec(
        title="Delete the SharePoint folder (archive already closed)",
        steps=(
            "This archive is closed but its SharePoint folder still exists. "
            "Delete the folder on SharePoint.",
            "Confirming is refused while the synced copy of the folder is still "
            "on this server — OneDrive can take a few minutes to catch up. The "
            "next automatic scan records the removal by itself either way.",
        ),
        buttons=(Button("done", "Folder deleted"),),
        links=("folder",),
    ),
    "close_publication_only": ActionSpec(
        title="Close as publication-only",
        steps=(
            "No data deposit is needed for this publication (see the note for "
            "why — usually there is no Open Access data mandate on its projects).",
            "Confirming closes the archive. Leave it alone if you would rather wait.",
        ),
        buttons=(Button("done", "Close as publication-only"),),
    ),
    "mandate_missing": ActionSpec(
        title="Confirm the Open Access mandate with the Project Office / IT",
        steps=(
            "The tracker could not work out from the publication database whether "
            "this paper's projects require open data.",
            "Ask the Project Office / IT. Acknowledging records that you asked "
            "(put the answer in the note); the item comes back until the database "
            "is fixed or you close the archive under Other actions.",
        ),
        buttons=(Button("done", "Acknowledge"),),
        note_hint="Note — who you asked and what they said",
    ),
}

# Always-available decisions, shown under "Other actions" on an OPEN paper.
OTHER: dict[str, ActionSpec] = {
    "close_exception": ActionSpec(
        title="Close as exception",
        steps=("Stops all tracking for this paper without a data deposit. "
               "A note explaining why is required.",),
        buttons=(Button("done", "Close as exception", style="danger", needs_note=True,
                        confirm="Close this archive as an exception?"),),
        note_hint="Why is this being closed? (required)",
    ),
    "close_publication_only": ActionSpec(
        title="Close as publication-only",
        steps=("The publication has no data to deposit.",),
        buttons=(Button("done", "Close as publication-only", style="danger",
                        confirm="Close this archive as publication-only?"),),
    ),
    "close_archived_external": ActionSpec(
        title="Close as archived elsewhere (everything already done)",
        steps=("All the data is in another repository AND the publication "
               "database is already updated and the folder removed. Record that "
               "repository's PID and URL; the archive closes at once as “data "
               "archived”. If those steps are still to do, use “Deposited "
               "elsewhere” on the QA / Zenodo action instead.",),
        buttons=(Button("done", "Close as archived elsewhere", style="danger",
                        needs_pid_url=True,
                        confirm="Close this archive as archived elsewhere?"),),
    ),
}

STATUS_LABELS = {
    "OPEN_INACTIVE": "Waiting for data",
    "OPEN_ACTIVE": "Data uploaded — QA",
    "OPEN_READY_FOR_ZENODO_DRAFT": "QA passed — create draft",
    "OPEN_ZENODO_DRAFT_CREATED": "Zenodo draft — review",
    "OPEN_ZENODO_DRAFT_VALIDATED": "Draft validated — publish",
    "OPEN_ZENODO_PUBLISHED": "Published — update database",
    "OPEN_DB_UPDATED": "Database updated — remove folder",
    "CLOSED_DATA_ARCHIVED": "Closed — data archived",
    "CLOSED_PUBLICATION_ONLY": "Closed — publication only",
    "CLOSED_EXCEPTION": "Closed — exception",
}

SHORT_LABELS = {
    "qa_pass": "QA check",
    "remind_sent": "Send reminder",
    "contact_pi_manual": "Contact PI",
    "handover_sent": "Send handover email",
    "completion_sent": "Send completion email",
    "reject_done_sent": "Send “not done” email",
    "reject_done": "Reject “done” tick",
    "zenodo_create_draft": "Create Zenodo draft",
    "zenodo_draft_created": "Create Zenodo draft",
    "zenodo_validated": "Review & publish on Zenodo",
    "zenodo_publish": "Publish on Zenodo",
    "zenodo_published": "Record Zenodo DOI",
    "db_updated": "Update publication DB",
    "folder_removed": "Delete folder & close",
    "closed_folder_removed": "Delete folder",
    "close_publication_only": "Close (publication only)",
    "mandate_missing": "Confirm mandate",
}
