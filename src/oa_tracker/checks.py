"""Archive-state checks shared by the sheet, the report, the automation
digest and the email drafts.

**Done-tick rejection rules.** A data contact can tick "I think this is
done" on the Tracker when the folder plainly isn't done. Each rule looks
at one archive row and returns a reason — phrased to finish the sentence
"you marked this as done, but ..." — or ``None``. ``reject_reasons``
runs them all; a non-empty result means the tick gets turned down
(``reject_done``: untick it on the List + email the data contact).
Add a rule by writing one more function and listing it in
``REJECT_RULES`` — the sheet row, the digest, the apply path and the
email all pick it up.

Only clear-cut cases belong here. Judgement calls (e.g. a package that
is present but incomplete) stay as operator-reviewed mismatches.
"""

from __future__ import annotations

from typing import Any, Callable

from oa_tracker import status as st


def folder_empty(archive: dict[str, Any]) -> str | None:
    """The folder has no files. OPEN_INACTIVE is the scanner's record of
    exactly that (an archive leaves it on the first scan that sees files)."""
    if archive.get("status") == st.OPEN_INACTIVE:
        return "the publication folder is still empty (we cannot see any uploaded files)"
    return None


REJECT_RULES: tuple[Callable[[dict[str, Any]], str | None], ...] = (
    folder_empty,
)


def reject_reasons(archive: dict[str, Any]) -> list[str]:
    """Why this archive's Tracker 'done' tick should be turned down.

    Empty unless the tick is set, the work is still author-owned
    (OPEN_INACTIVE / OPEN_ACTIVE) and the mandate requires data — a done
    tick on a paper-only or no-OA publication can legitimately mean
    "nothing to upload".
    """
    if not archive.get("user_done_flag"):
        return []
    if archive.get("status") not in (st.OPEN_INACTIVE, st.OPEN_ACTIVE):
        return []
    if archive.get("oa_data_required") != 1 or archive.get("oa_mandate_missing") == 1:
        return []
    return [reason for rule in REJECT_RULES if (reason := rule(archive))]


def missing_package_items(archive: dict[str, Any]) -> list[str]:
    """The protocol package items the scanner did not find in the folder."""
    return [
        label for label, key in (
            (".zip", "package_has_zip"),
            ("README.txt", "package_has_readme"),
            ("manuscript", "package_has_manuscript"),
        ) if not archive.get(key)
    ]


def has_data_contact(archive: dict[str, Any]) -> bool:
    """True when the archive names a real data contact. The scanner stores
    the literal placeholder 'TBD' when the central DB can't resolve one."""
    return "@" in (archive.get("data_contact_email") or "")


def is_external_deposit(archive: dict[str, Any]) -> bool:
    """The recorded dataset lives in a repository other than our Zenodo
    pipeline (the "deposited externally" exemption, or a hand-recorded
    non-Zenodo PID): a final PID with no Zenodo record on file and no
    'zenodo' in the identifier."""
    pid = (archive.get("final_pid") or "").strip().lower()
    if not pid or archive.get("zenodo_code"):
        return False
    return "zenodo" not in pid
