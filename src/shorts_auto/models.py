"""Domain objects shared across stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ideated -> generated -> post_processed -> approved / rejected -> published
IDEA_STATUSES = (
    "ideated",
    "generated",
    "post_processed",
    "approved",
    "rejected",
    "published",
    "failed",
)


@dataclass(slots=True)
class SeriesConfig:
    """One A/B arm. Backed by a single YAML file in config/series/."""

    id: str
    enabled: bool = True
    weight: float = 1.0
    language_independent: bool = True
    languages: list[str] = field(default_factory=lambda: ["ja", "en"])
    description: str = ""
    title_patterns: dict[str, list[str]] = field(default_factory=dict)
    hashtags: dict[str, list[str]] = field(default_factory=dict)
    prompt_template: str = "{scene}"
    subject_pool: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    video: dict[str, Any] = field(default_factory=dict)

    def video_setting(self, key: str, default: Any = None) -> Any:
        return self.video.get(key, default)


@dataclass(slots=True)
class Idea:
    """A planned video, before any pixels exist."""

    series_id: str
    hook: dict[str, str]  # {"ja": "...", "en": "..."}
    video_prompt: str
    scene_summary: str
    tags: dict[str, list[str]] = field(default_factory=dict)
    dedup_key: str = ""
    id: int | None = None
    status: str = "ideated"
    created_at: str = ""


@dataclass(slots=True)
class GeneratedVideo:
    """What a VideoBackend hands back."""

    path: str
    backend: str
    model: str
    duration_s: float
    cost_usd: float
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VideoRequest:
    """Backend-agnostic generation request."""

    prompt: str
    model: str
    duration_seconds: int = 8
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    generate_audio: bool = True
    negative_prompt: str | None = None
    output_path: str | None = None
