"""Domain objects shared across stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ideated -> generated -> post_processed -> approved / rejected -> published -> live
IDEA_STATUSES = (
    "ideated",
    "generated",
    "post_processed",
    "approved",
    "rejected",
    "published",  # uploaded, still private
    "live",       # visible on YouTube, accumulating views
    "failed",
)

# Statuses that will never move again without operator action.
TERMINAL_STATUSES = ("rejected", "live", "failed")


@dataclass(slots=True)
class SeriesConfig:
    """One genre. An A/B arm is a (series, language) pair.

    Backed by a single YAML file in config/series/ — adding a genre is adding a
    file.
    """

    id: str
    enabled: bool = True
    languages: list[str] = field(default_factory=lambda: ["ja"])
    description: str = ""
    style: str = ""
    title_patterns: dict[str, list[str]] = field(default_factory=dict)
    hashtags: dict[str, list[str]] = field(default_factory=dict)
    prompt_template: str = ""
    subject_pool: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    # English, passed straight to the video model. `banned` is Japanese guidance
    # for the ideation model and never reaches the video model, so anything that
    # must not appear on screen has to be repeated here.
    negative_prompt: str = ""
    made_for_kids: bool = False
    video: dict[str, Any] = field(default_factory=dict)

    def video_setting(self, key: str, default: Any = None) -> Any:
        return self.video.get(key, default)


@dataclass(slots=True)
class Arm:
    """One A/B test arm: a genre aimed at one channel."""

    series_id: str
    lang: str

    @property
    def key(self) -> str:
        return f"{self.series_id}:{self.lang}"

    @classmethod
    def parse(cls, key: str) -> Arm:
        series_id, _, lang = key.rpartition(":")
        return cls(series_id=series_id, lang=lang)


@dataclass(slots=True)
class IdeaDraft:
    """A planned video, before any pixels exist."""

    series_id: str
    lang: str
    hook: str
    video_prompt: str
    scene_summary: str
    tags: list[str] = field(default_factory=list)
    dedup_key: str = ""


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
    person_generation: str | None = None
    output_path: str | None = None
