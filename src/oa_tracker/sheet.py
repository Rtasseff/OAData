"""Generate action_sheet.tsv from current DB state."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from oa_tracker import db, status as st
from oa_tracker.config import Config

SHEET_COLUMNS = [
    "publication_id",
    "current_status",
    "task_code",
    "task_text",
    "first_seen_at",
    "next_reminder_at",
    "reminder_count",
    "done",
    "pid",
    "url",
    "note",
]


def _mandate_classification(archive: dict[str, Any]) -> tuple[str, str]:
    """Derive the action-sheet treatment for an archive from its cached
    pub-DB flags.

    Returns ``(category, auto_note)``. Categories:

    - ``"mandate_missing"`` — no rule applied; emit a single
      ``mandate_missing`` row asking the operator to confirm with
      PO/IT before doing anything else.
    - ``"close_no_oa"`` — explicit No-OA (paper and data both not
      required); emit a single ``close_publication_only`` row.
    - ``"paper_only"`` — paper required but no data; the standard
      pipeline rows still emit (operator decides QA/close), with a
      note flagging the paper-only nature.
    - ``"data_required"`` — standard flow, no auto-note.
    - ``"unclassified"`` — pub-DB has never enriched this archive
      (e.g., never reached, or this is a legacy v1 row pre-migration).
      Treat as standard flow so the tracker still works.
    """
    refreshed = archive.get("pub_db_last_refreshed_at")
    if not refreshed:
        return ("unclassified", "")

    if archive.get("oa_mandate_missing") == 1:
        return (
            "mandate_missing",
            "No mandate found in cff_oaMandate or AEI rule — "
            "investigate before closing.",
        )

    data_req = archive.get("oa_data_required")
    paper_req = archive.get("oa_paper_required")

    if data_req == 0 and paper_req == 0:
        return (
            "close_no_oa",
            "No OA mandate on linked project(s); no data archiving required.",
        )

    if data_req != 1 and paper_req == 1:
        # data_req is 0 (paper-only on every project) or NULL with a
        # paper-only signal — treated identically: workflow continues
        # but the operator should know data isn't actually mandated.
        return (
            "paper_only",
            "PAPER ONLY: data not required by mandate; "
            "processing as if data were required.",
        )

    if data_req == 1:
        return ("data_required", "")

    # Anything else (mix of `no_oa` + `unknown` contributions, or any
    # state where we can't conclude data_req=1 or data_req=0+paper_req=0
    # or paper-only) is ambiguous. Surface it like a missing mandate so
    # the operator can confirm with PO/IT before doing anything.
    return (
        "mandate_missing",
        "Mandate signal ambiguous (mixed no-OA and unknown projects) — "
        "confirm with PO/IT before closing or pursuing.",
    )


def proposal_row(
    pub_id: str,
    archive: dict[str, Any] | None,
    task_code: str,
    task_text: str,
    note: str = "",
    pid: str = "",
    url: str = "",
) -> dict[str, str]:
    """A sheet-format row for a pulled SharePoint proposal — a drop-in for
    ``oa apply``. Mirrors ``_row`` but tolerates a missing archive (a List
    row we don't recognise still needs to surface for the operator)."""
    row = {c: "" for c in SHEET_COLUMNS}
    row.update({
        "publication_id": pub_id, "task_code": task_code,
        "task_text": task_text, "done": "0", "pid": pid, "url": url, "note": note,
    })
    if archive is not None:
        row["current_status"] = archive["status"]
        row["first_seen_at"] = archive.get("first_seen_at") or ""
        row["next_reminder_at"] = archive.get("next_reminder_at") or ""
        row["reminder_count"] = str(archive.get("reminder_count") or 0)
    else:
        row["reminder_count"] = "0"
    return row


def _package_note(archive: dict[str, Any]) -> str:
    """Cross-check the Tracker 'done' tick against the detected package
    (.zip + README.txt + manuscript) for OPEN_ACTIVE archives. Returns the
    operator note for the QA row — empty when there's nothing noteworthy."""
    user_done = bool(archive.get("user_done_flag"))
    has_zip = bool(archive.get("package_has_zip"))
    has_readme = bool(archive.get("package_has_readme"))
    has_manuscript = bool(archive.get("package_has_manuscript"))
    complete = has_zip and has_readme and has_manuscript
    if user_done and complete:
        return (
            "Tracker 'done' + package (.zip + README + manuscript) detected — "
            "auto-QC eligible; if this row is still here, automation is off "
            "or the mandate isn't data-required. Review and pass QA."
        )
    if user_done and not complete:
        missing = " and ".join(
            m for m, ok in (
                (".zip", has_zip),
                ("README.txt", has_readme),
                ("a manuscript (.doc/.docx/.pdf)", has_manuscript),
            ) if not ok
        )
        return (
            f"MISMATCH: user marked done on the Tracker but the folder is "
            f"missing {missing} — the reminder asks them to package per "
            "protocol; QA manually if the contents are actually fine."
        )
    if complete:
        return (
            "Package (.zip + README + manuscript) detected but the contact "
            "hasn't ticked 'done' on the Tracker — QA manually, or wait for "
            "their confirmation."
        )
    return ""


def _join_notes(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _row(archive: dict[str, Any], task_code: str, task_text: str, note: str = "") -> dict[str, str]:
    """Build a sheet row dict with the standard column population."""
    return {
        "publication_id": archive["publication_id"],
        "current_status": archive["status"],
        "task_code": task_code,
        "task_text": task_text,
        "first_seen_at": archive.get("first_seen_at") or "",
        "next_reminder_at": archive.get("next_reminder_at") or "",
        "reminder_count": str(archive.get("reminder_count") or 0),
        "done": "0",
        "pid": "",
        "url": "",
        "note": note,
    }


def generate_sheet(config: Config) -> Path:
    """Generate action_sheet.tsv for all OPEN archives and return the file path."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = config.output_dir / "action_sheet.tsv"
    now_str = datetime.now().isoformat(timespec="seconds")

    rows: list[dict[str, str]] = []

    with db.get_connection(config.database) as conn:
        open_archives = db.get_open_archives(conn)
        reminders_due = {
            a["publication_id"] for a in db.get_reminders_due(conn, now_str)
        }

        for archive in open_archives:
            pub_id = archive["publication_id"]
            cur_status = archive["status"]
            category, auto_note = _mandate_classification(archive)

            # A pending data-contact handover (auto-applied reassignment)
            # gets its row FIRST, whatever the mandate category — the new
            # contact should be welcomed before being asked to act. The
            # row recurs until done=1 records handover_sent.
            if db.get_pending_handover(conn, pub_id) is not None:
                rows.append(_row(
                    archive, "handover_sent",
                    st.TASK_CODES["handover_sent"]["description"],
                    note=(
                        f"New data contact auto-assigned — send "
                        f"email_drafts/handover_{pub_id}.eml (regenerates "
                        "until sent); done=1 records it as sent."
                    ),
                ))

            # Mandate-missing and explicit no-OA archives produce a single
            # actionable row each — nothing else (no pipeline progression,
            # no reminders) until the operator addresses the situation.
            if category == "mandate_missing":
                rows.append(_row(
                    archive,
                    "mandate_missing",
                    st.TASK_CODES["mandate_missing"]["description"],
                    note=auto_note,
                ))
                continue

            if category == "close_no_oa":
                rows.append(_row(
                    archive,
                    "close_publication_only",
                    st.TASK_CODES["close_publication_only"]["description"],
                    note=auto_note,
                ))
                continue

            # Paper-only archives that sit empty (OPEN_INACTIVE) have no
            # natural next action — reminders are suppressed because the
            # mandate doesn't require data, and there's no pipeline row
            # to emit. Surface a close_publication_only row so the
            # operator can choose to close (or leave done=0 to wait).
            # Once the folder activates (OPEN_ACTIVE), we fall through
            # to the normal pipeline path with the paper-only side note.
            if category == "paper_only" and cur_status == st.OPEN_INACTIVE:
                rows.append(_row(
                    archive,
                    "close_publication_only",
                    st.TASK_CODES["close_publication_only"]["description"],
                    note=(
                        "PAPER ONLY mandate: data not required and folder still empty — "
                        "consider closing as publication-only."
                    ),
                ))
                continue

            # Pipeline task goes FIRST so the operator (or eventual
            # automation) considers it before a reminder. For OPEN_ACTIVE
            # this means QA is decided first: if QA passes, status
            # advances and any reminder row that follows is moot
            # (apply_actions will skip it with a warning). If QA fails
            # (operator switches task_code to qa_hold + done=1), status
            # stays OPEN_ACTIVE and the reminder row is the right next
            # step — exactly the "QA must precede reminder" rule.
            next_task = st.next_task_for_status(cur_status)
            if next_task:
                task = next_task
                note = auto_note

                if cur_status == st.OPEN_ACTIVE:
                    note = _join_notes(note, _package_note(archive))

                # Zenodo-aware rows: when the API integration is on, the
                # sheet offers the API-backed codes so done=1 performs the
                # step (create draft / publish) instead of just recording
                # hand-done work. Env-mismatched or hand-managed drafts
                # keep the manual codes.
                zen = config.zenodo
                env_ok = archive.get("zenodo_env") in (None, zen.environment)
                if zen.enabled and cur_status == st.OPEN_READY_FOR_ZENODO_DRAFT \
                        and not archive.get("zenodo_code") and pub_id.isdigit():
                    task = "zenodo_create_draft"
                    note = _join_notes(
                        note,
                        f"done=1 creates the draft via the API on {zen.environment} "
                        "(metadata + reserved DOI + package upload happen automatically "
                        "when `oa auto` runs).",
                    )
                elif cur_status == st.OPEN_ZENODO_DRAFT_CREATED and archive.get("zenodo_code"):
                    from oa_tracker import zenodo as z
                    note = _join_notes(
                        note,
                        f"Review the draft: {z.record_ui_url(zen, archive['zenodo_code'])}",
                    )
                elif zen.enabled and cur_status == st.OPEN_ZENODO_DRAFT_VALIDATED \
                        and archive.get("zenodo_code") and env_ok:
                    task = "zenodo_publish"
                    note = _join_notes(
                        note,
                        f"done=1 publishes record {archive['zenodo_code']} on "
                        f"{zen.environment} via the API and mints the DOI — this is "
                        "the permanent step.",
                    )

                meta = st.TASK_CODES[task]
                rows.append(_row(
                    archive, task, meta["description"], note=note,
                ))

            # Reminders fire only when data is actually required by mandate
            # (or when we don't have classification info — legacy rows
            # behave as before so existing flows aren't broken) AND only
            # while the work is still author-owned (OPEN_INACTIVE / OPEN_ACTIVE).
            # Once QA passes the remaining steps are the operator's, so a
            # reminder row would be noise — mirrors emails._REMINDER_STATUSES.
            allow_reminders = (
                category in ("data_required", "unclassified")
                and cur_status in (st.OPEN_INACTIVE, st.OPEN_ACTIVE)
            )
            if pub_id in reminders_due and allow_reminders:
                reached_max = (
                    archive.get("reminder_count") or 0
                ) >= config.reminders.max_reminders - 1
                task = "contact_pi_manual" if reached_max else "remind_sent"
                reminder_note = ""
                if reached_max:
                    n = (archive.get("reminder_count") or 0) + 1
                    reminder_note = (
                        f"Past-due draft: email_drafts/reminder_{pub_id}_{n}"
                        "_PASTDUE.eml (skeleton for the personal follow-up). "
                        "done=1 logs the contact and re-queues this item at the "
                        "next interval; to abandon instead, change the task to "
                        "close_exception with a note."
                    )
                rows.append(_row(
                    archive, task, st.TASK_CODES[task]["description"],
                    note=reminder_note,
                ))

    with open(sheet_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    return sheet_path
