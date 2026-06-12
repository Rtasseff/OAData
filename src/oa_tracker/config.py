"""Load config.toml, resolve paths relative to project root."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class ReminderSettings:
    first_reminder_days: int = 14
    reminder_interval_days: int = 7
    max_reminders: int = 5


@dataclass
class EmailSettings:
    """Outbound-email settings (``[email]``).

    Sender identity is here (not hardcoded in templates), and ``draft_format``
    selects how ``oa emails`` writes drafts. The dataclass defaults are
    conservative (``txt``, current behavior) so code that builds ``Config``
    directly is unchanged; ``config.toml`` sets the active production values.
    """
    sender_name: str = "Ryan Tasseff"
    sender_title: str = "Data Quality Officer"
    sender_email: str = ""          # used as the From header in .eml drafts; blank → omit From
    draft_format: str = "txt"       # "txt" | "eml" | "both"


@dataclass
class SharePointSettings:
    """Settings for the SharePoint List parallel track (``[sharepoint]``).

    ``enabled`` defaults to False so the rest of the tool runs untouched
    when the section is absent. No secret lives here — device-code auth
    uses the operator's identity; only the MSAL refresh-token cache
    (``token_cache``, in ``~/``) is sensitive and never enters the repo.
    """
    enabled: bool = False
    tenant: str = "biomagune.onmicrosoft.com"
    client_id: str = ""
    site: str = "biomagune.sharepoint.com:/sites/PublicationsData"
    list_name: str = "OA Archive Tracker"
    sop_url: str = ""               # protocol/SOP link — used by the List SOP column AND email drafts
    tracker_url: str = ""           # the List view users land on (e.g. "My data-contact papers")
    # Optional ``str.format`` template with a ``{pub_id}`` field; when set,
    # the Folder column links to the publication's SharePoint folder.
    folder_url_template: str = ""
    sync_closed: bool = False
    token_cache: Path = field(default_factory=lambda: Path("~/.oa_sharepoint_token.json"))


@dataclass
class Config:
    project_root: Path = field(default_factory=Path.cwd)
    sharepoint_root: Path = field(default_factory=lambda: Path("./data/publications"))
    database: Path = field(default_factory=lambda: Path("./oa_tracker.sqlite"))
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    email_drafts_dir: Path = field(default_factory=lambda: Path("./output/email_drafts"))
    template_dir: Path = field(default_factory=lambda: Path("./templates"))
    reminders: ReminderSettings = field(default_factory=ReminderSettings)
    sharepoint: SharePointSettings = field(default_factory=SharePointSettings)
    email: EmailSettings = field(default_factory=EmailSettings)


def _resolve(base: Path, p: str) -> Path:
    """Resolve a path relative to base if not absolute."""
    path = Path(p)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def load_config(config_path: Path | None = None, project_root: Path | None = None) -> Config:
    """Load configuration from a TOML file.

    Args:
        config_path: Explicit path to config.toml. If None, looks for
                     config.toml in the project root.
        project_root: Project root directory. Defaults to cwd.
    """
    root = (project_root or Path.cwd()).resolve()

    if config_path is None:
        config_path = root / _DEFAULT_CONFIG_NAME

    raw: dict = {}
    if config_path.exists():
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    paths = raw.get("paths", {})
    reminders_raw = raw.get("reminders", {})
    sp_raw = raw.get("sharepoint", {})
    email_raw = raw.get("email", {})
    email_defaults = EmailSettings()

    sp_defaults = SharePointSettings()
    token_cache_raw = sp_raw.get("token_cache")
    token_cache = (
        Path(token_cache_raw).expanduser()
        if token_cache_raw
        else sp_defaults.token_cache.expanduser()
    )

    return Config(
        project_root=root,
        sharepoint_root=_resolve(root, paths.get("sharepoint_root", "./data/publications")),
        database=_resolve(root, paths.get("database", "./oa_tracker.sqlite")),
        output_dir=_resolve(root, paths.get("output_dir", "./output")),
        email_drafts_dir=_resolve(root, paths.get("email_drafts_dir", "./output/email_drafts")),
        template_dir=_resolve(root, paths.get("template_dir", "./templates")),
        reminders=ReminderSettings(
            first_reminder_days=reminders_raw.get("first_reminder_days", 14),
            reminder_interval_days=reminders_raw.get("reminder_interval_days", 7),
            max_reminders=reminders_raw.get("max_reminders", 5),
        ),
        sharepoint=SharePointSettings(
            enabled=sp_raw.get("enabled", sp_defaults.enabled),
            tenant=sp_raw.get("tenant", sp_defaults.tenant),
            client_id=sp_raw.get("client_id", sp_defaults.client_id),
            site=sp_raw.get("site", sp_defaults.site),
            list_name=sp_raw.get("list_name", sp_defaults.list_name),
            sop_url=sp_raw.get("sop_url", sp_defaults.sop_url),
            tracker_url=sp_raw.get("tracker_url", sp_defaults.tracker_url),
            folder_url_template=sp_raw.get("folder_url_template", sp_defaults.folder_url_template),
            sync_closed=sp_raw.get("sync_closed", sp_defaults.sync_closed),
            token_cache=token_cache,
        ),
        email=EmailSettings(
            sender_name=email_raw.get("sender_name", email_defaults.sender_name),
            sender_title=email_raw.get("sender_title", email_defaults.sender_title),
            sender_email=email_raw.get("sender_email", email_defaults.sender_email),
            draft_format=email_raw.get("draft_format", email_defaults.draft_format),
        ),
    )
