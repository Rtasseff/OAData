from __future__ import annotations

import markdown
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from oa_tracker.report import build_report

from oa_web import guide, tracker


@login_required
def papers(request):
    """Main list: one line per publication, with its pending action(s)."""
    config = tracker.get_config()
    show = request.GET.get("show", "open")
    q = request.GET.get("q", "").strip().lower()
    pending = tracker.pending_by_pub(config)

    rows = []
    counts = {"open": 0, "todo": 0, "closed": 0, "all": 0}
    for a in tracker.list_archives(config):
        a["pending"] = pending.get(a["publication_id"], [])
        counts["all"] += 1
        counts["open" if a["is_open"] else "closed"] += 1
        if a["pending"]:
            counts["todo"] += 1
        if show == "open" and not a["is_open"]:
            continue
        if show == "closed" and a["is_open"]:
            continue
        if show == "todo" and not a["pending"]:
            continue
        if q:
            haystack = " ".join(str(a.get(k) or "") for k in (
                "publication_id", "pub_title", "data_contact_name", "data_contact_email",
                "corresponding_author_name", "status_label",
            )).lower()
            if q not in haystack:
                continue
        rows.append(a)

    return render(request, "oa_web/papers.html", {
        "rows": rows, "show": show, "q": request.GET.get("q", ""), "counts": counts,
        "nav": "papers",
    })


@login_required
def action_list(request):
    """The action sheet, live: every pending action across all papers."""
    config = tracker.get_config()
    titles = {a["publication_id"]: a for a in tracker.list_archives(config)}
    rows = []
    for pub_rows in tracker.pending_by_pub(config).values():
        for r in pub_rows:
            r["archive"] = titles.get(r["publication_id"], {})
            rows.append(r)
    return render(request, "oa_web/actions.html", {"rows": rows, "nav": "actions"})


def _md(text: str) -> str:
    # The text is generated from database values — neutralise any stray markup.
    return mark_safe(markdown.markdown(text.replace("<", "&lt;"), extensions=["sane_lists"]))


@login_required
def report(request):
    config = tracker.get_config()
    digest_path = config.output_dir / "auto_digest.md"
    digest = digest_path.read_text() if digest_path.is_file() else ""
    return render(request, "oa_web/report.html", {
        "report_html": _md(build_report(config)),
        "digest_html": _md(digest) if digest else "",
        "nav": "report",
    })


@login_required
def history(request):
    config = tracker.get_config()
    return render(request, "oa_web/history.html", {
        "events": tracker.recent_events(config), "nav": "history",
    })


@login_required
def paper(request, pub_id: str):
    """One paper: details, what to do next (with the buttons), audit trail."""
    config = tracker.get_config()
    archive = tracker.get_archive(config, pub_id)
    if archive is None:
        raise Http404(f"Publication {pub_id} is not tracked")

    zen = tracker.zenodo_links(config, archive)
    cards = []
    for row in tracker.pending_by_pub(config).get(pub_id, []):
        spec = guide.SPECS.get(row["task_code"])
        buttons = spec.buttons if spec else ()
        cards.append({
            "row": row, "spec": spec,
            "drafts": tracker.email_drafts(config, archive, spec) if spec else [],
            # Buttons that ask for a DOI + URL get their own form (folded
            # away under "Deposited elsewhere" when they are the alternative).
            "main": [b for b in buttons if not b.needs_pid_url],
            "pid_buttons": [b for b in buttons if b.needs_pid_url],
        })
    show_folder = any(c["spec"] and "folder" in c["spec"].links for c in cards)

    return render(request, "oa_web/paper.html", {
        "a": archive, "cards": cards, "zen": zen,
        "zenodo_enabled": config.zenodo.enabled,
        "others": guide.OTHER if archive["is_open"] else {},
        "files": tracker.folder_listing(archive) if show_folder else None,
        "show_folder": show_folder,
        "pub_db_url": tracker.pub_db_url(pub_id),
        "events": tracker.events_for(config, pub_id),
        "nav": "papers",
    })


@login_required
@require_POST
def paper_action(request, pub_id: str):
    config = tracker.get_config()
    p = request.POST
    if p.get("form") == "data_contact":
        out = tracker.set_data_contact(
            config, request.user.get_username(), pub_id,
            name=p.get("name", ""), email=p.get("email", ""),
            notify=p.get("notify") == "1",
        )
    else:
        out = tracker.perform(
            config, request.user.get_username(), pub_id,
            task_code=p.get("task_code", ""), choice=p.get("choice", ""),
            expected_status=p.get("expected_status", ""),
            note=p.get("note", ""), pid=p.get("pid", ""), url=p.get("url", ""),
            zenodo_code=p.get("zenodo_code", ""), other=p.get("other") == "1",
        )
    for m in out.messages:
        messages.success(request, m)
    for m in out.warnings:
        messages.warning(request, m)
    for m in out.errors:
        messages.error(request, m)
    # Back to the same paper: the page re-renders with whatever is next.
    return redirect(reverse("paper", args=[pub_id]))


@login_required
def draft(request, pub_id: str, filename: str):
    """Download an email draft from output/email_drafts (opens in Outlook)."""
    path = tracker.draft_path(tracker.get_config(), pub_id, filename)
    if path is None:
        raise Http404("No such draft")
    content_type = "message/rfc822" if path.suffix == ".eml" else "text/plain"
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name,
                        content_type=content_type)
