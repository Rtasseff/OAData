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
class Config:
    project_root: Path = field(default_factory=Path.cwd)
    sharepoint_root: Path = field(default_factory=lambda: Path("./data/publications"))
    database: Path = field(default_factory=lambda: Path("./oa_tracker.sqlite"))
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    email_drafts_dir: Path = field(default_factory=lambda: Path("./output/email_drafts"))
    template_dir: Path = field(default_factory=lambda: Path("./templates"))
    reminders: ReminderSettings = field(default_factory=ReminderSettings)


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
    )
