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
class ZenodoSettings:
    """Settings for the Zenodo API integration (``[zenodo]``).

    ``enabled`` defaults to False so nothing touches the API until the
    section is configured. ``environment`` defaults to sandbox — we never
    default to production. The token itself lives in ``token_file``
    (mode 600, outside the repo — same pattern as ``~/.my.cnf``), under
    section ``[zenodo]`` (production) or ``[zenodo-sandbox]``.
    """
    enabled: bool = False
    environment: str = "sandbox"            # "sandbox" | "production"
    token_file: Path = field(default_factory=lambda: Path("~/.zenodorc"))
    default_license: str = "cc0-1.0"        # Creative Commons Zero v1.0 Universal
    default_affiliation: str = "CIC biomaGUNE"
    default_keywords: list[str] = field(default_factory=lambda: ["CIC biomaGUNE"])
    # What gets uploaded to the draft: "package" = only the protocol
    # package (*.zip + README*.txt); "all" = every non-clutter file in
    # the folder. Extra folder files are reported, never silently skipped.
    upload_files: str = "package"
    manifest_dir: Path = field(default_factory=lambda: Path("./output/zenodo_uploads"))
    # Files larger than the threshold upload via InvenioRDM's multipart
    # transfer (type "M"): the file is split into parts, each part is an
    # independent, retryable PUT — a mid-transfer drop costs one part,
    # not the whole file. Verified live against sandbox.zenodo.org
    # 2026-07-04 (init returns per-part URLs valid for 14 days). Files
    # at or below the threshold keep the plain single-PUT upload.
    multipart_threshold_mb: int = 1024
    multipart_part_size_mb: int = 200
    # Ceiling for UNATTENDED single-PUT uploads. Multipart ignores it
    # (per-part retry makes size safe) — but as of 2026-07-04 Zenodo
    # denies multipart part uploads (403; scaffolding deployed, feature
    # not enabled for API users), so oversized files fall back to
    # single PUT, and above this ceiling that attempt is deferred to
    # the operator instead: the digest carries the manual instructions
    # and `oa action <pub> zenodo_upload_files` closes the loop after a
    # hand upload (checksum match — no bytes re-sent).
    single_put_max_mb: int = 5120

    @property
    def base_url(self) -> str:
        return (
            "https://zenodo.org"
            if self.environment == "production"
            else "https://sandbox.zenodo.org"
        )


@dataclass
class AutomationSettings:
    """Per-signal-class automation gates (``[automation]``).

    Each gate promotes one validated signal class from action-sheet
    routing to auto-apply (see feedback_no_auto_state_changes.md — the
    action sheet remains the fallback for anything not gated on, and
    ``oa auto`` records every automatic action in the audit log and the
    run digest). ``auto_publish`` is deliberately absent: publishing
    mints a permanent DOI and always stays operator-confirmed.
    """
    enabled: bool = False
    # Tracker "I think this is done" + .zip + README detected → qa_pass.
    auto_qa_pass: bool = True
    # READY archives: create the draft, reserve the DOI, record the code.
    auto_zenodo_draft: bool = True
    # Upload the package files to the (auto-created) draft.
    auto_zenodo_upload: bool = True
    # SharePoint proposals: apply "suggest a new data contact" directly.
    auto_apply_data_contact: bool = True
    # SharePoint proposals: apply categorized exemptions with evidence
    # directly ("Other — needs explanation" always stays manual).
    auto_apply_exemptions: bool = True
    # SharePoint notes: record free-text List notes to the archive
    # directly (awareness only — no status change either way).
    auto_apply_user_notes: bool = True
    # OPEN_DB_UPDATED + folder gone from the tree + PID on record →
    # close as CLOSED_DATA_ARCHIVED (the folder_removed transition).
    auto_close_on_folder_removed: bool = True


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
    zenodo: ZenodoSettings = field(default_factory=ZenodoSettings)
    automation: AutomationSettings = field(default_factory=AutomationSettings)


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
    zen_raw = raw.get("zenodo", {})
    auto_raw = raw.get("automation", {})
    email_defaults = EmailSettings()
    zen_defaults = ZenodoSettings()
    auto_defaults = AutomationSettings()

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
        zenodo=ZenodoSettings(
            enabled=zen_raw.get("enabled", zen_defaults.enabled),
            environment=zen_raw.get("environment", zen_defaults.environment),
            token_file=Path(zen_raw.get("token_file", str(zen_defaults.token_file))).expanduser(),
            default_license=zen_raw.get("default_license", zen_defaults.default_license),
            default_affiliation=zen_raw.get("default_affiliation", zen_defaults.default_affiliation),
            default_keywords=list(zen_raw.get("default_keywords", zen_defaults.default_keywords)),
            upload_files=zen_raw.get("upload_files", zen_defaults.upload_files),
            manifest_dir=_resolve(root, zen_raw.get("manifest_dir", "./output/zenodo_uploads")),
            multipart_threshold_mb=zen_raw.get(
                "multipart_threshold_mb", zen_defaults.multipart_threshold_mb),
            multipart_part_size_mb=zen_raw.get(
                "multipart_part_size_mb", zen_defaults.multipart_part_size_mb),
            single_put_max_mb=zen_raw.get(
                "single_put_max_mb", zen_defaults.single_put_max_mb),
        ),
        automation=AutomationSettings(
            enabled=auto_raw.get("enabled", auto_defaults.enabled),
            auto_qa_pass=auto_raw.get("auto_qa_pass", auto_defaults.auto_qa_pass),
            auto_zenodo_draft=auto_raw.get("auto_zenodo_draft", auto_defaults.auto_zenodo_draft),
            auto_zenodo_upload=auto_raw.get("auto_zenodo_upload", auto_defaults.auto_zenodo_upload),
            auto_apply_data_contact=auto_raw.get(
                "auto_apply_data_contact", auto_defaults.auto_apply_data_contact),
            auto_apply_exemptions=auto_raw.get(
                "auto_apply_exemptions", auto_defaults.auto_apply_exemptions),
            auto_apply_user_notes=auto_raw.get(
                "auto_apply_user_notes", auto_defaults.auto_apply_user_notes),
            auto_close_on_folder_removed=auto_raw.get(
                "auto_close_on_folder_removed", auto_defaults.auto_close_on_folder_removed),
        ),
    )
