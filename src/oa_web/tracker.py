"""Service layer between the Django views and ``oa_tracker``.

Reads go through the same builders the CLI uses (``sheet.build_rows``,
``report.build_report``); writes go through ``actions.apply_single`` —
the path behind ``oa action`` — so validation, Zenodo calls and the audit
log behave exactly as they do from the command line.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings

import logging
import threading

from oa_tracker import actions, db, status as st
from oa_tracker.config import Config, load_config
from oa_tracker.emails import _folder_url, generate_emails
from oa_tracker.sheet import SHEET_COLUMNS, build_rows

from oa_web import guide


def get_config() -> Config:
    """Loaded per request — config.toml edits apply without a restart."""
    return load_config(settings.OA_CONFIG_PATH, project_root=settings.OA_PROJECT_ROOT)


# ── Reads ─────────────────────────────────────────────────────────────

def pending_by_pub(config: Config) -> dict[str, list[dict[str, str]]]:
    """Pending action rows keyed by publication, in sheet order."""
    out: dict[str, list[dict[str, str]]] = {}
    for row in build_rows(config):
        row["label"] = guide.SHORT_LABELS.get(row["task_code"], row["task_code"])
        # The sheet's notes speak TSV ("done=1 …"); on the web it's a button.
        spec = guide.SPECS.get(row["task_code"])
        row["note_display"] = "" if spec and spec.email_stem else \
            row["note"].replace("done=1", "confirming here")
        out.setdefault(row["publication_id"], []).append(row)
    return out


def decorate(archive: dict[str, Any], config: Config) -> dict[str, Any]:
    """Add the display-only fields the templates use."""
    archive["status_label"] = guide.STATUS_LABELS.get(archive["status"], archive["status"])
    archive["is_open"] = archive["status"] in st.OPEN_STATUSES
    archive["can_exempt"] = archive["status"] in guide.EXEMPTION_STATUSES
    archive["folder_url"] = _folder_url(archive, config)
    return archive


def list_archives(config: Config) -> list[dict[str, Any]]:
    with db.get_connection(config.database) as conn:
        archives = db.get_all_archives(conn)
    return [decorate(a, config) for a in archives]


def get_archive(config: Config, pub_id: str) -> dict[str, Any] | None:
    with db.get_connection(config.database) as conn:
        archive = db.get_archive(conn, pub_id)
    return decorate(archive, config) if archive else None


def _events(rows) -> list[dict[str, Any]]:
    events = [dict(r) for r in rows]
    for e in events:
        e["when"] = (e["ts"] or "")[:16].replace("T", " ")
    return events


def events_for(config: Config, pub_id: str) -> list[dict[str, Any]]:
    with db.get_connection(config.database) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE publication_id = ? ORDER BY event_id DESC",
            (pub_id,),
        ).fetchall()
    return _events(rows)


def recent_events(config: Config, limit: int = 200) -> list[dict[str, Any]]:
    with db.get_connection(config.database) as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY event_id DESC LIMIT ?", (limit,)
        ).fetchall()
    return _events(rows)


def folder_listing(archive: dict[str, Any], limit: int = 40) -> list[dict[str, Any]] | None:
    """Top-level contents of the synced publication folder, for the QA
    view. Strictly read-only (the folder tree is never written to);
    ``None`` when the folder is not there."""
    folder = Path(archive["folder_path"])
    try:
        if not folder.is_dir():
            return None
        entries = []
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower())[:limit]:
            is_dir = p.is_dir()
            entries.append({
                "name": p.name + ("/" if is_dir else ""),
                "size": None if is_dir else p.stat().st_size,
            })
        return entries
    except OSError:
        return None


def email_drafts(config: Config, archive: dict[str, Any], spec: guide.ActionSpec) -> list[str]:
    """File names of the email draft(s) that belong to an action."""
    if not spec.email_stem:
        return []
    stem = spec.email_stem.format(
        pub_id=archive["publication_id"], n=(archive.get("reminder_count") or 0) + 1,
    )
    d = config.email_drafts_dir
    return [p.name for p in (d / f"{stem}.eml", d / f"{stem}.txt") if p.is_file()]


def draft_path(config: Config, pub_id: str, filename: str) -> Path | None:
    """Resolve a draft download — only plain file names inside the drafts
    folder that belong to this publication."""
    if Path(filename).name != filename or f"_{pub_id}" not in filename:
        return None
    if not filename.endswith((".eml", ".txt")):
        return None
    path = config.email_drafts_dir / filename
    return path if path.is_file() else None


def zenodo_links(config: Config, archive: dict[str, Any]) -> dict[str, str]:
    from oa_tracker import zenodo

    code = archive.get("zenodo_code")
    if not code:
        return {}
    return {
        "draft": zenodo.record_ui_url(config.zenodo, str(code)),
        "record": zenodo.record_public_url(config.zenodo, str(code)),
        # The DOI reserved at draft creation IS the minted DOI — what the
        # confirm step records (see actions._confirm_zenodo_published).
        "doi": archive.get("zenodo_doi") or zenodo.code_to_doi(str(code)),
    }


def pub_db_url(pub_id: str) -> str:
    tpl = settings.OA_PUB_DB_URL_TEMPLATE
    return tpl.format(pub_id=pub_id) if tpl else ""


# ── SharePoint push (background, best effort) ────────────────────────

log = logging.getLogger("oa_web")

# pub_id → {"when", "ok", "text"}: the last push attempt, shown on the paper
# page. In-memory only; the morning `oa auto` reconciles regardless.
LAST_PUSH: dict[str, dict[str, Any]] = {}


def push_to_sharepoint(config: Config, pub_id: str) -> None:
    """Push this paper's List row now, without making the page wait."""
    if not (settings.OA_SHAREPOINT_PUSH and config.sharepoint.enabled):
        return
    LAST_PUSH[pub_id] = {"when": _now(), "ok": None, "text": "updating the SharePoint List…"}

    def run() -> None:
        from oa_tracker.auto import push_one
        try:
            text = push_one(config, pub_id)
            LAST_PUSH[pub_id] = {"when": _now(), "ok": True, "text": f"SharePoint List: {text}"}
        except Exception as e:  # network/token/Graph — never breaks the recorded action
            log.warning("SharePoint push for %s failed: %s", pub_id, e)
            LAST_PUSH[pub_id] = {
                "when": _now(), "ok": False,
                "text": f"SharePoint List not updated ({e}) — the next automatic run will do it.",
            }

    threading.Thread(target=run, name=f"sp-push-{pub_id}", daemon=True).start()


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes").replace("T", " ")


# ── Writes ────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    ok: bool = False
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _clean(msg: str) -> str:
    """apply_single labels its messages "Action (<pub>): …" for the CLI."""
    return msg.split("): ", 1)[1] if msg.startswith("Action (") and "): " in msg else msg


def perform(
    config: Config,
    username: str,
    pub_id: str,
    task_code: str,
    choice: str,
    expected_status: str,
    note: str = "",
    pid: str = "",
    url: str = "",
    zenodo_code: str = "",
    other: bool = False,
) -> Outcome:
    """Record one button press. ``task_code`` is the pending row the button
    sits on (or an always-available decision when ``other``); the button's
    ``choice`` picks what is applied."""
    out = Outcome()
    archive = get_archive(config, pub_id)
    if archive is None:
        out.errors.append(f"Publication {pub_id} is not tracked.")
        return out
    if archive["status"] != expected_status:
        out.errors.append(
            "This paper changed while the page was open (someone else, or the "
            "automatic run, got there first). Nothing was recorded — the page "
            "now shows the current state."
        )
        return out

    if other:
        spec = guide.OTHER.get(task_code)
        offered = spec is not None and archive["is_open"]
    else:
        spec = guide.SPECS.get(task_code)
        offered = spec is not None and any(
            r["task_code"] == task_code for r in pending_by_pub(config).get(pub_id, [])
        )
    if not offered:
        out.errors.append("That action is no longer pending for this paper.")
        return out
    button = next((b for b in spec.buttons if b.choice == choice), None)
    if button is None:
        out.errors.append("Unknown button.")
        return out

    note, pid, url, zenodo_code = (s.strip() for s in (note, pid, url, zenodo_code))
    if button.needs_note and not note:
        out.errors.append("A note is required for this — say why.")
        return out
    # pid/url are only ever passed for the buttons that ask for them: on any
    # other button they would trip apply's fast-track-to-published shortcut
    # (which is exactly what the "deposited elsewhere" buttons rely on).
    if button.needs_pid_url:
        if not pid or not url:
            out.errors.append("Both the DOI/PID and the URL are required.")
            return out
    else:
        pid = url = ""

    apply_code = button.apply_code or task_code

    if "zenodo_code" in spec.fields and not button.needs_pid_url:
        if not zenodo_code.isdigit():
            out.errors.append("Enter the numeric Zenodo record id (not the DOI).")
            return out
        res = actions.set_zenodo_code(config, pub_id, code=zenodo_code)
        if res.errors:
            out.errors.extend(res.errors)
            return out

    return _record(config, username, archive, task_code, apply_code, button.label,
                   note, pid, url, out)


def apply_exemption(
    config: Config,
    username: str,
    pub_id: str,
    key: str,
    expected_status: str,
    note: str = "",
    pid: str = "",
    url: str = "",
) -> Outcome:
    """Apply one of the Tracker List's exemption categories on the
    operator's say-so — the same task code the List's "Propose exemption"
    routes to, with the category carried in the note as the List does."""
    out = Outcome()
    archive = get_archive(config, pub_id)
    if archive is None:
        out.errors.append(f"Publication {pub_id} is not tracked.")
        return out
    if archive["status"] != expected_status:
        out.errors.append(
            "This paper changed while the page was open (someone else, or the "
            "automatic run, got there first). Nothing was recorded — the page "
            "now shows the current state."
        )
        return out
    if not archive["can_exempt"]:
        out.errors.append(
            "Exemptions apply only before the Zenodo deposit — use “Other "
            "actions” for this paper instead."
        )
        return out
    ex = next((e for e in guide.EXEMPTIONS if e.key == key), None)
    if ex is None:
        out.errors.append("Choose one of the exemptions.")
        return out

    note, pid, url = (s.strip() for s in (note, pid, url))
    if ex.needs_note and not note:
        out.errors.append("Explain the exemption in the note — it is required for “Other”.")
        return out
    if ex.needs_pid_url:
        if not pid or not url:
            out.errors.append("Both the external PID/DOI and the URL are required.")
            return out
    else:
        pid = url = ""  # a stray PID must not fast-track a closure
    note = f"Exemption: {ex.text}." + (f" {note}" if note else "")
    return _record(config, username, archive, ex.apply_code, ex.apply_code,
                   f"Exemption — {ex.text}", note, pid, url, out)


def _record(
    config: Config, username: str, archive: dict[str, Any], row_code: str,
    apply_code: str, label: str, note: str, pid: str, url: str, out: Outcome,
) -> Outcome:
    """Apply through the CLI's path, then keep the files and the List in
    step: retire the sheet row, push the row to SharePoint, regenerate
    the email drafts."""
    pub_id = archive["publication_id"]
    result, old_status, new_status = actions.apply_single(
        config, pub_id, apply_code, done=1, pid=pid, url=url, note=note,
        source=f"web:{username}",
    )
    out.warnings = [_clean(w) for w in result.warnings]
    out.errors = [_clean(e) for e in result.errors]
    if not result.applied:
        if not out.errors and not out.warnings:
            out.errors.append("Nothing was recorded.")
        return out

    out.ok = True
    status_changed = bool(new_status) and new_status != old_status
    if status_changed:
        out.messages.append(
            f"Recorded: {label}. Status is now "
            f"“{guide.STATUS_LABELS.get(new_status, new_status)}”."
        )
    else:
        out.messages.append(f"Recorded: {label}.")

    _retire_sheet_row(config, archive, row_code, apply_code, note, pid, url,
                      drop_all=status_changed)
    push_to_sharepoint(config, pub_id)
    # Keep output/email_drafts current (e.g. the completion draft right
    # after a publish is confirmed) — same generator as `oa emails`.
    try:
        generate_emails(config)
    except Exception as e:  # drafts are a convenience; the action is recorded
        out.warnings.append(f"Email drafts could not be regenerated: {e}")
    return out


def _retire_sheet_row(
    config: Config, archive: dict[str, Any], row_code: str, applied_code: str,
    note: str, pid: str, url: str, drop_all: bool = False,
) -> None:
    """Mirror what ``oa apply`` does to the files: the handled row leaves
    ``action_sheet.tsv`` and is appended to ``action_history.tsv``, so a
    later ``oa apply`` of the sheet cannot record the same action twice.
    ``drop_all`` (the status moved) also drops the paper's other rows —
    they were written for the old status; the next ``oa sheet`` / ``oa
    auto`` regenerates whatever still applies."""
    now = datetime.now().isoformat(timespec="seconds")
    pub_id = archive["publication_id"]
    sheet_path = config.output_dir / "action_sheet.tsv"
    applied: dict[str, str] | None = None

    if sheet_path.exists():
        with open(sheet_path, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fieldnames = reader.fieldnames or SHEET_COLUMNS
            rows = list(reader)
        for i, row in enumerate(rows):
            if (row.get("publication_id") or "").strip() == pub_id \
                    and (row.get("task_code") or "").strip() == row_code:
                applied = rows.pop(i)
                break
        before = len(rows)
        if drop_all:
            rows = [r for r in rows if (r.get("publication_id") or "").strip() != pub_id]
        if applied is not None or len(rows) != before:
            with open(sheet_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
                w.writeheader()
                w.writerows(rows)

    hist = {c: "" for c in SHEET_COLUMNS}
    if applied is not None:
        hist.update({c: applied.get(c, "") for c in SHEET_COLUMNS})
    hist.update({
        "publication_id": pub_id, "current_status": archive["status"],
        "task_code": applied_code, "done": "1", "pid": pid, "url": url, "note": note,
        "applied_at": now,
    })
    if not hist["task_text"]:
        hist["task_text"] = st.TASK_CODES[applied_code]["description"]
    history_path = config.output_dir / "action_history.tsv"
    config.output_dir.mkdir(parents=True, exist_ok=True)
    write_header = not history_path.exists()
    with open(history_path, "a", newline="") as hf:
        w = csv.DictWriter(hf, fieldnames=SHEET_COLUMNS + ["applied_at"], delimiter="\t")
        if write_header:
            w.writeheader()
        w.writerow(hist)


def set_data_contact(config: Config, username: str, pub_id: str,
                     name: str, email: str, notify: bool) -> Outcome:
    out = Outcome()
    email, name = email.strip(), name.strip()
    if "@" not in email:
        out.errors.append("Enter the new data contact's email address.")
        return out
    res = actions.set_data_contact(config, pub_id, email=email, name=(name or None),
                                   source=f"web:{username}", queue_handover=notify)
    out.errors, out.warnings = list(res.errors), list(res.warnings)
    if res.applied and not res.errors:
        out.ok = True
        out.messages.append(f"Data contact set to {name or email}.")
        push_to_sharepoint(config, pub_id)
        try:
            generate_emails(config)
        except Exception as e:
            out.warnings.append(f"Email drafts could not be regenerated: {e}")
    return out
