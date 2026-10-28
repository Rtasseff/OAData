"""What each pending action means for the operator, and which buttons record it.

One ``ActionSpec`` per task code the action sheet can emit. The wording
follows docs/sop.md §7–8; the buttons map onto the same task codes the
action sheet uses, so the web UI can never do something ``oa apply``
couldn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from oa_tracker import sharepoint as sp
from oa_tracker import status as st
from oa_tracker.mailer import DRAFT_STEMS


@dataclass(frozen=True)
class Button:
    choice: str             # posted back as "choice"
    label: str
    apply_code: str = ""    # task code applied; "" → the row's own code
    style: str = "primary"  # primary | danger | secondary
    needs_note: bool = False
    needs_pid_url: bool = False  # asks for the dataset DOI + URL (recorded as final_pid/final_url)
    confirm: str = ""       # browser confirm() text for irreversible steps
    alt_summary: str = ""   # needs_pid_url beside other buttons: the fold-out's heading
    prefill: bool = False   # pre-fill DOI + URL from the Zenodo record on file
    background: bool = False  # starts a background job (the upload) instead of applying a code
    # Set on buttons that only RECORD something the operator did outside the
    # system (sent an email, published on Zenodo, deleted the folder…): the
    # question the "have you done this?" popup asks. Such buttons are green
    # with a ✓; buttons that make the system act keep their own style.
    did: str = ""
    sends: bool = False     # sends the step's email draft from the office address, then records the row


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
    alt_summary="Deposited elsewhere (not by the system) — record its DOI and URL",
    did="Is the data already deposited in that repository, under the DOI and URL "
        "you entered?",
)
ELSEWHERE_STEP = (
    "If the data is already deposited somewhere else — a Zenodo record made "
    "by hand, or another repository (common in collaborations) — do not "
    "create a draft: record that deposit's DOI and URL instead under "
    "“Deposited elsewhere” below. The remaining steps (completion email, "
    "publication database, folder removal) then follow as usual."
)

_EMAIL_STEPS = (
    "Read the email below and change anything you need: “Send now” sends "
    "what the boxes hold, from the office address, and records it as sent.",
    "Or download the draft, send it from Outlook, then press “Email sent”. "
    "Once it is recorded, the draft and this action stop recurring.",
    "The 07:00 run sends this kind of email by itself when its switch is on "
    "([automation] auto_send_*), except for a paper at QA: those wait here "
    "for you.",
)

# "Send now" on the email steps (oa_tracker.mailer); "Email sent" stays for
# an email sent from Outlook.
SEND = Button("send", "Send now", sends=True)
EMAIL_SENT = Button("done", "Email sent", did="Have you sent this email from Outlook?")

UPLOAD = ActionSpec(
    title="Upload the data to the Zenodo draft",
    steps=(
        "The draft record exists on Zenodo (its DOI is reserved) but holds no "
        "data yet. The package to upload — the .zip, README.txt and manuscript "
        "from the SharePoint folder — is listed below. Any other file in the "
        "folder is listed separately and is not uploaded unless you tick it "
        "(e.g. a README saved under another name).",
        "Automatic: the next automatic run uploads it, or press “Upload now” to "
        "start it straight away. It runs in the background; this page shows when "
        "it has finished.",
        "By hand (when the package is too large for the system, or you prefer "
        "to): get the package files from the SharePoint folder (download them, or "
        "use your OneDrive-synced copy), open the Zenodo draft, drag the files "
        "onto it or click “Upload files”, and wait until every file has finished. "
        "Then press “Uploaded by hand” — the system checks that the draft holds "
        "the files.",
        "Nothing is published at this step: reviewing and publishing the draft "
        "comes next.",
    ),
    buttons=(
        Button("now", "Upload now", background=True),
        Button("manual", "Uploaded by hand — record it",
               apply_code="zenodo_files_uploaded", style="secondary",
               did="Have you uploaded every package file to the Zenodo draft yourself, "
                   "and has each one finished?"),
    ),
    links=("zenodo", "folder"),
)

SPECS: dict[str, ActionSpec] = {
    "qa_pass": ActionSpec(
        title="Quality check the uploaded data",
        steps=(
            "Open the SharePoint folder and review what the data contact uploaded.",
            "The package must be: one .zip with the datasets, a README.txt beside "
            "the zip, and a version of the manuscript (.doc/.docx/.pdf) beside the zip.",
            "Pass → the next step is creating the Zenodo draft record: the next "
            "automatic run does it, or you can do it straight away on this page. "
            "Fail → the archive stays where it is; say what is wrong in the note "
            "so it is on record (the data contact hears about it through the next "
            "reminder or your own email).",
            ELSEWHERE_STEP,
        ),
        buttons=(
            Button("pass", "QA pass",
                   did="Have you checked the package in the SharePoint folder — the "
                       ".zip, the README and the manuscript?"),
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
        buttons=(SEND, EMAIL_SENT),
        email_stem=DRAFT_STEMS["remind_sent"],
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
        buttons=(Button("done", "PI contacted", did="Have you contacted the PI yourself?"),),
        email_stem="reminder_{pub_id}_{n}_PASTDUE",
        note_hint="Optional note — what was said or agreed",
    ),
    "handover_sent": ActionSpec(
        title="Send the handover notice to the new data contact",
        steps=_EMAIL_STEPS,
        buttons=(SEND, EMAIL_SENT),
        email_stem=DRAFT_STEMS["handover_sent"],
    ),
    "completion_sent": ActionSpec(
        title="Send the completion email (data archived)",
        steps=_EMAIL_STEPS,
        buttons=(SEND, EMAIL_SENT),
        email_stem=DRAFT_STEMS["completion_sent"],
    ),
    "reject_done_sent": ActionSpec(
        title="Send the “not done yet” email",
        steps=_EMAIL_STEPS,
        buttons=(SEND, EMAIL_SENT),
        email_stem=DRAFT_STEMS["reject_done_sent"],
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
        title="Create the Zenodo draft record",
        steps=(
            "QA has passed. This creates the record on Zenodo with the metadata "
            "from the publication database and reserves its DOI. No data is "
            "uploaded and nothing is published — uploading the data is the next step.",
            "The next automatic run creates it by itself, or create it now with "
            "the button.",
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
        buttons=(Button("done", "Draft created on Zenodo",
                         did="Have you created the deposit on Zenodo yourself?"),
                  ELSEWHERE),
        fields=("zenodo_code",),
        links=("folder",),
    ),
    # Both upload codes share one card: the sheet emits zenodo_upload_files
    # when the system can upload the package and zenodo_files_uploaded
    # when it must go by hand (zenodo.plan_upload); the page greys out
    # "Upload now" with the reason in the second case.
    "zenodo_upload_files": UPLOAD,
    "zenodo_files_uploaded": UPLOAD,
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
        buttons=(
            Button("done", "Published on Zenodo — confirm",
                   did="Have you clicked Publish on Zenodo?"),
            # Same path as done=1 + pid/url on this row in the sheet:
            # apply's fast-track records the identifiers as entered.
            Button("correct", "Published on Zenodo — record these identifiers",
                   style="secondary", needs_pid_url=True, prefill=True,
                   alt_summary="Different DOI or URL on Zenodo? Edit them and record",
                   did="Have you clicked Publish on Zenodo?"),
        ),
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
        buttons=(Button("done", "Record DOI and URL", needs_pid_url=True, prefill=True,
                         did="Have you published the deposit on Zenodo?"),),
        links=("zenodo",),
    ),
    "db_updated": ActionSpec(
        title="Enter the dataset into the institutional publication database",
        steps=(
            "Open this publication in the institutional publication database and "
            "enter the dataset DOI and URL shown below (repository: Zenodo).",
            "Confirm here once it is saved.",
        ),
        buttons=(Button("done", "Database updated",
                         did="Have you entered the dataset DOI and URL in the institutional "
                             "publication database and saved it?"),),
        links=("doi", "pubdb"),
    ),
    "folder_removed": ActionSpec(
        title="Delete the SharePoint folder and close",
        steps=(
            "Everything is archived and recorded. Delete this publication's "
            "folder on SharePoint (the tracker never deletes anything itself).",
            "Confirming closes the archive as “data archived”.",
        ),
        buttons=(Button("done", "Folder deleted — close archive",
                         did="Have you deleted this publication’s folder on SharePoint?"),),
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
        buttons=(Button("done", "Folder deleted",
                         did="Have you deleted this publication’s folder on SharePoint?"),),
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
        buttons=(Button("done", "Acknowledge",
                         did="Have you asked the Project Office / IT about this paper’s "
                             "mandate?"),),
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
               "archived”. If those steps are still to do, use the "
               "“deposited externally” exemption (or “Deposited elsewhere” on "
               "the QA / Zenodo action) instead.",),
        buttons=(Button("done", "Close as archived elsewhere", style="danger",
                        needs_pid_url=True,
                        confirm="Close this archive as archived elsewhere?"),),
    ),
}


# Offered under "Other actions" only while QA is on hold (failed, folder
# unchanged since — the QA step itself is then hidden): passing it anyway,
# e.g. when the data contact explained that the package is fine after all.
QA_HELD_OTHER: dict[str, ActionSpec] = {
    "qa_pass": ActionSpec(
        title="QA pass anyway",
        steps=("The QA check is hidden because it failed and nothing in the "
               "folder has changed since. If the package is fine after all "
               "(for example the data contact explained), pass it here.",),
        buttons=(Button("pass", "QA pass"),),
        note_hint="Note (optional): why it passes after all",
    ),
}

# ── Exemptions ────────────────────────────────────────────────────────
# The same closed list as the Tracker List's "Propose exemption" column,
# applied the same way (sharepoint.EXEMPTION_ROUTING), so a data contact
# who tells the operator instead of using the List gets the identical
# result. Offered up to the Zenodo deposit — afterwards the data is
# archived and only the "Other actions" closures make sense.

@dataclass(frozen=True)
class Exemption:
    key: str            # posted back as "exemption"
    text: str           # the List's wording, verbatim
    apply_code: str
    effect: str         # what happens, in the operator's terms
    needs_pid_url: bool = False
    needs_note: bool = False


def _routed(text: str) -> str:
    return sp.EXEMPTION_ROUTING[text][0]


EXEMPTIONS: tuple[Exemption, ...] = (
    Exemption(
        "external", sp.EXEMPTION_EXTERNAL, _routed(sp.EXEMPTION_EXTERNAL),
        "Not a closure: the external PID/DOI and URL are recorded and the "
        "Zenodo steps are skipped. The completion email, publication-database "
        "entry and folder deletion still follow as actions.",
        needs_pid_url=True,
    ),
    Exemption(
        "no_share", sp.EXEMPTION_NO_SHARE, _routed(sp.EXEMPTION_NO_SHARE),
        "Closes at once as an exception. Only the folder deletion remains.",
    ),
    Exemption(
        "no_data", sp.EXEMPTION_NO_DATA, _routed(sp.EXEMPTION_NO_DATA),
        "Closes at once as publication-only. Only the folder deletion remains.",
    ),
    Exemption(
        "consult", sp.EXEMPTION_CONSULT, _routed(sp.EXEMPTION_CONSULT),
        "Closes at once as an exception. Only the folder deletion remains.",
    ),
    # On the List "Other" never applies by itself — the operator routes
    # it. Here the operator IS routing it: an explained exception.
    Exemption(
        "other", sp.EXEMPTION_OTHER, "close_exception",
        "Closes at once as an exception, with your explanation in the note "
        "(required). Only the folder deletion remains.",
        needs_note=True,
    ),
)

EXEMPTION_STATUSES = frozenset({
    st.OPEN_INACTIVE, st.OPEN_ACTIVE, st.OPEN_READY_FOR_ZENODO_DRAFT,
})

STATUS_LABELS = {
    "OPEN_INACTIVE": "Waiting for data",
    "OPEN_ACTIVE": "Data uploaded — QA",
    "OPEN_READY_FOR_ZENODO_DRAFT": "QA passed — create draft",
    "OPEN_ZENODO_DRAFT_CREATED": "Zenodo draft created",
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
    "zenodo_upload_files": "Upload data to Zenodo",
    "zenodo_files_uploaded": "Upload data to Zenodo (by hand)",
    "zenodo_validated": "Review & publish on Zenodo",
    "zenodo_publish": "Publish on Zenodo",
    "zenodo_published": "Record Zenodo DOI",
    "db_updated": "Update publication DB",
    "folder_removed": "Delete folder & close",
    "closed_folder_removed": "Delete folder",
    "close_publication_only": "Close (publication only)",
    "mandate_missing": "Confirm mandate",
}
