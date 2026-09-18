"""Loading and validating settings.yaml, channels.yaml and config/themes/*.yaml."""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .models import Arm, ThemeConfig


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


_REQUIRED_THEME_KEYS = ("id", "description", "nasa_queries", "topic_seeds", "banned")


def parse_theme(data: dict[str, Any], source: str = "<memory>") -> ThemeConfig:
    """Validate one theme mapping and turn it into a ThemeConfig."""
    for required in _REQUIRED_THEME_KEYS:
        if not data.get(required):
            raise ConfigError(f"{source}: theme is missing or has an empty '{required}'")

    known = set(ThemeConfig.__slots__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{source}: unknown theme keys: {sorted(unknown)}")

    theme = ThemeConfig(**data)
    if not theme.languages:
        raise ConfigError(f"{source}: theme must target at least one language")
    for lang in theme.languages:
        if not theme.title_patterns.get(lang):
            raise ConfigError(f"{source}: no title_patterns for language '{lang}'")
        if not theme.hashtags.get(lang):
            raise ConfigError(f"{source}: no hashtags for language '{lang}'")
    return theme


@lru_cache(maxsize=1)
def _load_themes_cached() -> tuple[ThemeConfig, ...]:
    if not paths.THEMES_DIR.exists():
        raise ConfigError(f"themes directory not found: {paths.THEMES_DIR}")

    themes: list[ThemeConfig] = []
    seen: set[str] = set()
    for path in sorted(paths.THEMES_DIR.glob("*.yaml")):
        parsed = parse_theme(_read_yaml(path), source=str(path))
        if parsed.id in seen:
            raise ConfigError(f"duplicate theme id '{parsed.id}' in {path}")
        seen.add(parsed.id)
        themes.append(parsed)

    if not themes:
        raise ConfigError("no themes found in config/themes/")
    return tuple(themes)


def load_themes(include_disabled: bool = False) -> list[ThemeConfig]:
    all_themes = _load_themes_cached()
    if include_disabled:
        return list(all_themes)
    enabled = [t for t in all_themes if t.enabled]
    if not enabled:
        raise ConfigError("every theme in config/themes/ is disabled")
    return enabled


def theme_by_id(theme_id: str) -> ThemeConfig:
    for entry in _load_themes_cached():
        if entry.id == theme_id:
            return entry
    raise ConfigError(f"unknown theme id: {theme_id}")


def arms(include_disabled: bool = False) -> list[Arm]:
    """Every (theme, language) pair under test."""
    return [
        Arm(series_id=t.id, lang=lang)
        for t in load_themes(include_disabled=include_disabled)
        for lang in t.languages
    ]


# --- publishing pace ---------------------------------------------------------


def channel_start_date() -> date | None:
    raw = load_settings().get("publish", {}).get("channel_started_on")
    if not raw:
        return None
    return date.fromisoformat(str(raw))


def allowed_uploads_per_week(day_index: int) -> int:
    """How many uploads the ramp-up permits `day_index` days after launch.

    A brand-new channel that starts posting daily looks like a spam account, and
    "mass uploading without improvement" is explicitly counterproductive. The
    ramp trades about three weeks of gate progress for that risk.
    """
    ramp = load_settings().get("publish", {}).get("ramp_up") or []
    for step in ramp:
        until = step.get("until_day")
        if until is None or day_index <= int(until):
            return int(step["per_week"])
    return 7


def uploads_allowed_today(reference: date | None = None) -> int:
    """Daily cap derived from the ramp, never above the API's hard limit."""
    settings = load_settings().get("publish", {})
    hard_cap = int(settings.get("hard_daily_cap", 6))

    start = channel_start_date()
    if start is None:
        return min(1, hard_cap)  # unknown launch date: stay conservative

    today = reference or datetime.now(UTC).date()
    day_index = (today - start).days
    if day_index < 0:
        return 0

    per_week = allowed_uploads_per_week(day_index)
    # Spread the week's quota rather than posting them back to back.
    daily = per_week / 7
    # Post on the days the running total says we are behind.
    scheduled = int((day_index + 1) * daily) - int(day_index * daily)
    return min(scheduled, hard_cap)


def validate_all() -> list[str]:
    """Cross-file checks. Returns human-readable problems, empty if healthy."""
    problems: list[str] = []
    try:
        channel_ids = {c["id"] for c in load_channels()["channels"]}
    except ConfigError as exc:
        return [str(exc)]

    try:
        themes = load_themes(include_disabled=True)
    except ConfigError as exc:
        return [str(exc)]

    for entry in themes:
        for lang in entry.languages:
            if lang not in channel_ids:
                problems.append(
                    f"theme '{entry.id}' targets language '{lang}' but no channel has that id"
                )

    settings = load_settings()
    pipeline = settings.get("pipeline", {})
    publish = settings.get("publish", {})

    ideas_per_day = int(pipeline.get("ideas_per_day", 0))
    hard_cap = int(publish.get("hard_daily_cap", 6))
    if ideas_per_day > hard_cap:
        problems.append(
            f"pipeline.ideas_per_day ({ideas_per_day}) exceeds publish.hard_daily_cap "
            f"({hard_cap}); videos would pile up unpublished"
        )
    if hard_cap > 6:
        problems.append(
            f"publish.hard_daily_cap ({hard_cap}) exceeds the 6/day the default YouTube "
            "Data API quota allows (videos.insert costs 1600 of 10,000 units)"
        )
    if not publish.get("ramp_up"):
        problems.append("publish.ramp_up is empty; a new channel posting daily risks a spam flag")

    composition = settings.get("video", {}).get("composition", {})
    if composition:
        total = sum(float(v) for v in composition.values())
        if abs(total - 1.0) > 0.01:
            problems.append(f"video.composition sums to {total:.2f}, expected 1.00")

    return problems


def reset_cache() -> None:
    """Tests mutate config files; let them drop the memoised copies."""
    load_settings.cache_clear()
    load_channels.cache_clear()
    _load_themes_cached.cache_clear()
