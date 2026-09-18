"""Filesystem layout. Everything is resolved relative to the repo root."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
CHANNELS_FILE = CONFIG_DIR / "channels.yaml"
THEMES_DIR = CONFIG_DIR / "themes"

# Hand-collected art: character sprites, いらすとや-style illustrations,
# background photos. Committed alongside a library.yaml that records the
# licence of every file.
ASSETS_DIR = ROOT / "assets"
SPRITES_DIR = ASSETS_DIR / "sprites"

WORK_DIR = ROOT / "work"
FOOTAGE_DIR = WORK_DIR / "footage"
STILLS_DIR = WORK_DIR / "stills"
DIAGRAMS_DIR = WORK_DIR / "diagrams"
AUDIO_DIR = WORK_DIR / "audio"
RENDERS_DIR = WORK_DIR / "renders"
THUMBS_DIR = WORK_DIR / "thumbs"


def db_path() -> Path:
    """SQLite location. Overridable so tests can point at a temp file."""
    override = os.environ.get("TUBE_AUTO_DB")
    if override:
        return Path(override)
    return WORK_DIR / "tube_auto.db"


def ensure_work_dirs() -> None:
    for directory in (WORK_DIR, FOOTAGE_DIR, STILLS_DIR, DIAGRAMS_DIR,
                      AUDIO_DIR, RENDERS_DIR, THUMBS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
