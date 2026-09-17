"""Django settings for the OA Archive Tracker web UI.

Which tracker the UI serves is decided by ``OA_CONFIG`` (path to a
config.toml; default ``./config.toml``) — the same file the CLI reads.
An optional ``[web]`` section in it tunes the server:

    [web]
    allowed_hosts = ["localhost", "127.0.0.1", "my-pc.cicbiomagune.es"]
    database = "./oa_web.sqlite"        # logins + sessions only
    pub_db_url_template = "https://intranet/.../{pub_id}"   # optional link
"""

from __future__ import annotations

import os
import secrets
import tomllib
from pathlib import Path

OA_CONFIG_PATH = Path(os.environ.get("OA_CONFIG", "config.toml")).resolve()
OA_PROJECT_ROOT = OA_CONFIG_PATH.parent

_web: dict = {}
if OA_CONFIG_PATH.exists():
    with open(OA_CONFIG_PATH, "rb") as _f:
        _web = tomllib.load(_f).get("web", {})

OA_PUB_DB_URL_TEMPLATE = _web.get("pub_db_url_template", "")


def _secret_key() -> str:
    """A per-install key kept beside the config (mode 600, gitignored)."""
    env = os.environ.get("OA_WEB_SECRET_KEY")
    if env:
        return env
    path = OA_PROJECT_ROOT / ".oa_web_secret"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
DEBUG = os.environ.get("OA_WEB_DEBUG", "") == "1"
ALLOWED_HOSTS = list(_web.get("allowed_hosts", ["localhost", "127.0.0.1", "[::1]"]))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "oa_web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "oa_web.urls"
WSGI_APPLICATION = "oa_web.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Django's own tables only (users, sessions, admin log). The tracker
# database is read and written exclusively through oa_tracker.
_web_db = Path(os.environ.get("OA_WEB_DB") or _web.get("database", "oa_web.sqlite"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _web_db if _web_db.is_absolute() else OA_PROJECT_ROOT / _web_db,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "papers"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Madrid"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
# Served straight from the app's static/ folder by WhiteNoise — no
# collectstatic step for an internal tool.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
