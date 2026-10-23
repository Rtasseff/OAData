"""Who may do what on the web UI. Three levels, set per login under
/admin/ (Users → the login → Permissions):

* neither flag   → read only: every page and email draft, no buttons
* Staff status   → read and write: every button (recorded as web:<user>)
* Superuser      → read, write and manage logins (/admin/)

Writes reach the tracker only through POST views marked
``@write_required``; the templates hide the buttons when ``can_act`` is
false, so a read-only login never sees a form it cannot use.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

READ_ONLY_TEXT = ("Your login is read-only, so nothing was recorded. Ask an "
                  "administrator for staff access if you need to make changes.")


def can_act(user) -> bool:
    return user.is_active and (user.is_staff or user.is_superuser)


def context(request) -> dict:
    """Template context processor: ``can_act`` on every page."""
    user = getattr(request, "user", None)
    return {"can_act": bool(user and user.is_authenticated and can_act(user))}


def write_required(view):
    """Refuse a write from a read-only login: message, back to the page."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if can_act(request.user):
            return view(request, *args, **kwargs)
        messages.error(request, READ_ONLY_TEXT)
        pub_id = kwargs.get("pub_id")
        return redirect(reverse("paper", args=[pub_id]) if pub_id else reverse("papers"))
    return wrapped
