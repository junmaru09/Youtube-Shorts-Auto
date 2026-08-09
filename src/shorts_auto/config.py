"""Loading and validating settings.yaml, channels.yaml and config/series/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .models import Arm, SeriesConfig


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
    for entry in channels:
        for required in ("id", "language", "token_path"):
            if required not in entry:
                raise ConfigError(f"channel {entry!r} is missing '{required}'")
    return data


def channel(channel_id: str) -> dict[str, Any]:
    for entry in load_channels()["channels"]:
        if entry["id"] == channel_id:
            return entry
    raise ConfigError(f"unknown channel id: {channel_id}")


_REQUIRED_SERIES_KEYS = ("id", "prompt_template", "negative_prompt")

# The prompt template is assembled from the fields the ideation model returns,
# in the order Google's Veo guide recommends. Every one must have a slot.
_REQUIRED_PROMPT_SLOTS = ("{cinematography}", "{subject}", "{action}", "{context}", "{audio}")


def parse_series(data: dict[str, Any], source: str = "<memory>") -> SeriesConfig:
    """Validate one series mapping and turn it into a SeriesConfig."""
    for required in _REQUIRED_SERIES_KEYS:
        if required not in data:
            raise ConfigError(f"{source}: series is missing required key '{required}'")

    known = set(SeriesConfig.__slots__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{source}: unknown series keys: {sorted(unknown)}")

    template = data["prompt_template"]
    missing = [slot for slot in _REQUIRED_PROMPT_SLOTS if slot not in template]
    if missing:
        raise ConfigError(f"{source}: prompt_template is missing slots {missing}")

    series = SeriesConfig(**data)

    if not series.languages:
        raise ConfigError(f"{source}: series must target at least one language")
    for lang in series.languages:
        if not series.title_patterns.get(lang):
            raise ConfigError(f"{source}: no title_patterns for language '{lang}'")

    return series


@lru_cache(maxsize=1)
def _load_series_cached() -> tuple[SeriesConfig, ...]:
    if not paths.SERIES_DIR.exists():
        raise ConfigError(f"series directory not found: {paths.SERIES_DIR}")

    series: list[SeriesConfig] = []
    seen: set[str] = set()
    for path in sorted(paths.SERIES_DIR.glob("*.yaml")):
        parsed = parse_series(_read_yaml(path), source=str(path))
        if parsed.id in seen:
            raise ConfigError(f"duplicate series id '{parsed.id}' in {path}")
        seen.add(parsed.id)
        series.append(parsed)

    if not series:
        raise ConfigError("no series found in config/series/")
    return tuple(series)


def load_series(include_disabled: bool = False) -> list[SeriesConfig]:
    all_series = _load_series_cached()
    if include_disabled:
        return list(all_series)
    enabled = [s for s in all_series if s.enabled]
    if not enabled:
        raise ConfigError("every series in config/series/ is disabled")
    return enabled


def series_by_id(series_id: str) -> SeriesConfig:
    for entry in _load_series_cached():
        if entry.id == series_id:
            return entry
    raise ConfigError(f"unknown series id: {series_id}")


def arms(include_disabled: bool = False) -> list[Arm]:
    """Every (series, language) pair under test."""
    return [
        Arm(series_id=s.id, lang=lang)
        for s in load_series(include_disabled=include_disabled)
        for lang in s.languages
    ]


def video_defaults(series: SeriesConfig | None = None) -> dict[str, Any]:
    """settings.yaml video block, overridden per-series where specified."""
    defaults = dict(load_settings().get("video", {}))
    if series:
        defaults.update(series.video)
    return defaults


def negative_prompt_for(series: SeriesConfig) -> str:
    """Global negative prompt merged with the series' own, de-duplicated."""
    parts: list[str] = []
    for source in (load_settings().get("video", {}).get("negative_prompt", ""), series.negative_prompt):
        for term in str(source).replace("\n", " ").split(","):
            cleaned = term.strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
    return ", ".join(parts)


def validate_all() -> list[str]:
    """Cross-file checks. Returns human-readable problems, empty if healthy."""
    problems: list[str] = []
    try:
        channel_ids = {c["id"] for c in load_channels()["channels"]}
    except ConfigError as exc:
        return [str(exc)]

    try:
        series = load_series(include_disabled=True)
    except ConfigError as exc:
        return [str(exc)]

    for entry in series:
        for lang in entry.languages:
            if lang not in channel_ids:
                problems.append(
                    f"series '{entry.id}' targets language '{lang}' but no channel has that id"
                )
            if not entry.hashtags.get(lang):
                problems.append(f"series '{entry.id}' has no {lang} hashtags")

    settings = load_settings()
    pipeline = settings.get("pipeline", {})
    ideas_per_day = int(pipeline.get("ideas_per_day", 0))
    publish_per_day = int(pipeline.get("publish_per_day", 0))
    if ideas_per_day > publish_per_day:
        problems.append(
            f"pipeline.ideas_per_day ({ideas_per_day}) exceeds publish_per_day "
            f"({publish_per_day}); generated videos would pile up unpublished"
        )
    if publish_per_day > 6:
        problems.append(
            f"pipeline.publish_per_day ({publish_per_day}) exceeds the 6/day the default "
            "YouTube Data API quota allows (videos.insert costs 1600 of 10,000 units)"
        )

    return problems


def reset_cache() -> None:
    """Tests mutate config files; let them drop the memoised copies."""
    load_settings.cache_clear()
    load_channels.cache_clear()
    _load_series_cached.cache_clear()
