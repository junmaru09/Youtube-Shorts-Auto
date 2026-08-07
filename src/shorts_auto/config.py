"""Loading and validating settings.yaml, channels.yaml and config/series/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .models import SeriesConfig


class ConfigError(Exception):
    """Raised when a config file is missing or structurally wrong."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"expected a mapping at the top level of {path}")
    return data


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    return _read_yaml(paths.SETTINGS_FILE)


@lru_cache(maxsize=1)
def load_channels() -> dict[str, Any]:
    data = _read_yaml(paths.CHANNELS_FILE)
    channels = data.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ConfigError("channels.yaml must define a non-empty 'channels' list")
    for channel in channels:
        for required in ("id", "language", "token_path"):
            if required not in channel:
                raise ConfigError(f"channel {channel!r} is missing '{required}'")
    return data


def channel(channel_id: str) -> dict[str, Any]:
    for entry in load_channels()["channels"]:
        if entry["id"] == channel_id:
            return entry
    raise ConfigError(f"unknown channel id: {channel_id}")


_REQUIRED_SERIES_KEYS = ("id", "prompt_template")


def parse_series(data: dict[str, Any], source: str = "<memory>") -> SeriesConfig:
    """Validate one series mapping and turn it into a SeriesConfig."""
    for required in _REQUIRED_SERIES_KEYS:
        if required not in data:
            raise ConfigError(f"{source}: series is missing required key '{required}'")

    known = set(SeriesConfig.__slots__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{source}: unknown series keys: {sorted(unknown)}")

    if "{scene}" not in data["prompt_template"]:
        raise ConfigError(f"{source}: prompt_template must contain a '{{scene}}' placeholder")

    return SeriesConfig(**data)


def load_series(include_disabled: bool = False) -> list[SeriesConfig]:
    """Read every YAML in config/series/. Adding a genre = adding one file."""
    if not paths.SERIES_DIR.exists():
        raise ConfigError(f"series directory not found: {paths.SERIES_DIR}")

    series: list[SeriesConfig] = []
    seen: set[str] = set()
    for path in sorted(paths.SERIES_DIR.glob("*.yaml")):
        parsed = parse_series(_read_yaml(path), source=str(path))
        if parsed.id in seen:
            raise ConfigError(f"duplicate series id '{parsed.id}' in {path}")
        seen.add(parsed.id)
        if parsed.enabled or include_disabled:
            series.append(parsed)

    if not series:
        raise ConfigError("no enabled series found in config/series/")
    return series


def series_by_id(series_id: str) -> SeriesConfig:
    for entry in load_series(include_disabled=True):
        if entry.id == series_id:
            return entry
    raise ConfigError(f"unknown series id: {series_id}")


def video_defaults(series: SeriesConfig | None = None) -> dict[str, Any]:
    """settings.yaml video block, overridden per-series where specified."""
    defaults = dict(load_settings().get("video", {}))
    if series:
        defaults.update(series.video)
    return defaults


def reset_cache() -> None:
    """Tests mutate config files; let them drop the memoised copies."""
    load_settings.cache_clear()
    load_channels.cache_clear()
