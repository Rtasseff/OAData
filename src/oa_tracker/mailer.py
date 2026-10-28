"""Send email drafts through Microsoft Graph, from the office address.

The drafts are the ones ``oa emails`` writes (``output/email_drafts``).
Sending uses the operator's own sign-in — the SharePoint List's MSAL token
cache and Entra app, asking for the delegated ``Mail.Send.Shared`` scope —
and ``POST /me/sendMail`` with ``from`` set to ``[email] sender_email``.
Exchange "Send As" on that address (granted 2026-10-28) makes recipients
see only the office address; the sent copy lands in the operator's Sent
Items. See docs/email_from_office_address.md.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from oa_tracker.config import Config

SCOPES = ("Mail.Send.Shared",)
SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"

# The sendable email steps → the stem of the draft ``oa emails`` writes for
# them (``{n}`` = the reminder's number). The PI-contact step is not here:
# that contact stays personal.
DRAFT_STEMS = {
    "remind_sent": "reminder_{pub_id}_{n}",
    "handover_sent": "handover_{pub_id}",
    "completion_sent": "completion_{pub_id}",
    "reject_done_sent": "reject_done_{pub_id}",
}


class SendError(RuntimeError):
    """The email was not sent (or, when ``maybe_sent``, it is unknown).
    ``sign_in``: the Microsoft sign-in failed, so no other send can work."""

    def __init__(self, message: str, maybe_sent: bool = False, sign_in: bool = False):
        super().__init__(message)
        self.maybe_sent = maybe_sent
        self.sign_in = sign_in


def draft_file(config: Config, archive: dict[str, Any], code: str) -> Path | None:
    """The draft on disk for one email step (.eml before .txt), or None."""
    stem = DRAFT_STEMS[code].format(
        pub_id=archive["publication_id"], n=(archive.get("reminder_count") or 0) + 1)
    for path in (config.email_drafts_dir / f"{stem}.eml",
                 config.email_drafts_dir / f"{stem}.txt"):
        if path.is_file():
            return path
    return None


@dataclass(frozen=True)
class Draft:
    to: tuple[tuple[str, str], ...]     # (name, address)
    cc: tuple[tuple[str, str], ...]
    subject: str
    body: str

    @staticmethod
    def _text(pairs: tuple[tuple[str, str], ...]) -> str:
        def one(name: str, addr: str) -> str:
            if not name:
                return addr
            if any(c in name for c in ',;:"<>@()[]'):
                name = '"' + name.replace('"', "") + '"'   # so it reads back as one address
            return f"{name} <{addr}>"
        return ", ".join(one(n, a) for n, a in pairs)

    @property
    def to_text(self) -> str:
        return self._text(self.to)

    @property
    def cc_text(self) -> str:
        return self._text(self.cc)

    def problem(self) -> str:
        """Why this draft must not be sent as is ("" when it can be)."""
        if not self.to:
            return "the draft has no To address"
        for _, addr in self.to + self.cc:
            if "@" not in addr or addr.upper().startswith("TBD"):
                return f"the draft has an incomplete address ({addr or 'empty'})"
        if not self.subject.strip():
            return "the draft has no subject"
        return ""


def from_fields(to: str, cc: str, subject: str, body: str) -> Draft:
    """A draft as edited on the web page ("Name <address>, …" lists)."""
    def addresses(text: str) -> tuple[tuple[str, str], ...]:
        return tuple((n, a) for n, a in getaddresses([text]) if n or a)
    return Draft(to=addresses(to), cc=addresses(cc), subject=subject.strip(),
                 body=body.replace("\r\n", "\n"))


def read_draft(path: Path) -> Draft:
    """Parse a draft written by ``oa emails``. Both formats are headers, a
    blank line and the body; the .eml one may be MIME-encoded."""
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    if msg.get("Content-Type") is None:
        # A .txt draft: no MIME headers, so no charset — it is UTF-8.
        body = (msg.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    else:
        body = (msg.get_body(preferencelist=("plain",)) or msg).get_content()
    return Draft(
        to=tuple(getaddresses([str(h) for h in msg.get_all("To", [])])),
        cc=tuple(getaddresses([str(h) for h in msg.get_all("Cc", [])])),
        # A long Subject comes back folded, with the fold's leading space.
        subject=str(msg.get("Subject", "")).strip(),
        body=body,
    )


def graph_message(draft: Draft, sender: str) -> dict[str, Any]:
    """The ``/me/sendMail`` payload: the draft, sent as ``sender``."""
    def recipients(pairs):
        return [{"emailAddress": {"address": a, **({"name": n} if n else {})}}
                for n, a in pairs]

    message: dict[str, Any] = {
        "subject": draft.subject,
        "body": {"contentType": "Text", "content": draft.body},
        "toRecipients": recipients(draft.to),
        "from": {"emailAddress": {"address": sender}},
    }
    if draft.cc:
        message["ccRecipients"] = recipients(draft.cc)
    return {"message": message, "saveToSentItems": True}


def unavailable_reason(config: Config, web: bool = True) -> str:
    """Why sending (from the web page, or by the automatic run when not
    ``web``) is not possible ("" when it is)."""
    if web and not config.email.send_from_web:
        return "Sending from this site is switched off ([email] send_from_web in config.toml)."
    if not config.email.sender_email:
        return "No office address is configured ([email] sender_email in config.toml)."
    if not config.sharepoint.client_id:
        return "No Microsoft sign-in is configured ([sharepoint] client_id in config.toml)."
    return ""


def send_draft(config: Config, draft: Draft, interactive: bool = False) -> None:
    """Send ``draft`` from ``[email] sender_email``. Raises ``SendError``."""
    problem = draft.problem()
    if problem:
        raise SendError(problem)
    from oa_tracker.sharepoint import GraphClient
    try:
        token = GraphClient(config.sharepoint, scopes=SCOPES,
                            interactive=interactive).authenticate()
    except (RuntimeError, ValueError) as e:
        raise SendError(f"Microsoft sign-in failed ({e})", sign_in=True) from None
    _post(token, graph_message(draft, config.email.sender_email))


def _post(token: str, payload: dict[str, Any]) -> None:
    """One attempt, never retried: a retry of a send that had in fact gone
    through would reach the recipient twice."""
    req = urllib.request.Request(
        SEND_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status != 202:
                raise SendError(f"unexpected answer from Microsoft (HTTP {resp.status})",
                                maybe_sent=True)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            err = json.loads(detail)["error"]
            detail = f"{err['code']}: {err['message']}"
        except (ValueError, KeyError, TypeError):
            pass
        raise SendError(f"Microsoft refused it (HTTP {e.code}, {detail[:300]})") from None
    except (socket.timeout, TimeoutError):
        raise SendError("no answer from Microsoft in time", maybe_sent=True) from None
    except urllib.error.URLError as e:
        raise SendError(f"could not reach Microsoft ({e.reason})") from None
