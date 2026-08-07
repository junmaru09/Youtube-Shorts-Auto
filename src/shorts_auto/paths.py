"""Filesystem layout. Everything is resolved relative to the repo root."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
CHANNELS_FILE = CONFIG_DIR / "channels.yaml"
SERIES_DIR = CONFIG_DIR / "series"

WORK_DIR = ROOT / "work"
ASSETS_DIR = WORK_DIR / "assets"
RENDERS_DIR = WORK_DIR / "renders"
THUMBS_DIR = WORK_DIR / "thumbs"


def db_path() -> Path:
    """SQLite location. Overridable so tests can point at a temp file."""
    override = os.environ.get("SHORTS_AUTO_DB")
    if override:
        return Path(override)
    return WORK_DIR / "shorts_auto.db"


def ensure_work_dirs() -> None:
    for directory in (WORK_DIR, ASSETS_DIR, RENDERS_DIR, THUMBS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
