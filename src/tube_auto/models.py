"""Domain objects shared across stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ideated -> researched -> scripted -> narrated -> sourced -> assembled
#         -> approved / rejected -> published -> live
IDEA_STATUSES = (
    "ideated",     # a topic and angle exist
    "researched",  # primary sources gathered and cited
    "scripted",    # script written and every number cited
    "narrated",    # audio synthesised, chapter timeline known
    "sourced",     # visuals gathered and rights-checked
    "assembled",   # the mp4 exists
    "approved",
    "rejected",
    "published",   # uploaded, still private
    "live",        # visible on YouTube, accumulating views
    "failed",
)

TERMINAL_STATUSES = ("rejected", "live", "failed")

# Who says a line. The explainer carries the video; the listener interjects only
# at chapter turns and hard spots, which keeps the script from drifting into the
# stilted back-and-forth that fully-scripted dialogue produces.
SPEAKERS = ("explainer", "listener")


@dataclass(slots=True)
class ThemeConfig:
    """One theme. An A/B arm is a (theme, language) pair.

    Backed by a single YAML file in config/themes/ — adding a theme is adding a
    file.
    """

    id: str
    enabled: bool = True
    languages: list[str] = field(default_factory=lambda: ["ja"])
    description: str = ""
    audience: str = ""
    # Search terms used against NASA's library and arXiv.
    nasa_queries: list[str] = field(default_factory=list)
    arxiv_categories: list[str] = field(default_factory=list)
    title_patterns: dict[str, list[str]] = field(default_factory=dict)
    hashtags: dict[str, list[str]] = field(default_factory=dict)
    # Seeds for the research model, not a fixed list.
    topic_seeds: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    made_for_kids: bool = False

    def video_setting(self, key: str, default: Any = None) -> Any:
        return default


@dataclass(slots=True)
class Arm:
    """One A/B test arm: a theme aimed at one channel."""

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
class Source:
    """A primary reference. `ref` is the citation key the script must use."""

    ref: str
    kind: str  # nasa | jpl | arxiv
    url: str
    title: str
    published_at: str | None = None
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "url": self.url,
            "title": self.title,
            "published_at": self.published_at,
            "summary": self.summary,
        }


@dataclass(slots=True)
class Line:
    """One spoken line.

    `display` is what the subtitle shows; `spoken` is the same sentence with
    numbers, units and proper nouns opened into kana, because Japanese TTS
    mis-reads `10^24 kg` and `Kepler-452b` otherwise.
    """

    speaker: str
    display: str
    spoken: str
    refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "display": self.display,
            "spoken": self.spoken,
            "refs": self.refs,
        }


@dataclass(slots=True)
class Chapter:
    title: str
    visual_intent: str = ""  # what the footage search should look for
    lines: list[Line] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        return "".join(line.display for line in self.lines)

    @property
    def spoken_text(self) -> str:
        return "".join(line.spoken for line in self.lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "visual_intent": self.visual_intent,
            "lines": [line.as_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chapter:
        return cls(
            title=data["title"],
            visual_intent=data.get("visual_intent", ""),
            lines=[
                Line(
                    speaker=line["speaker"],
                    display=line["display"],
                    spoken=line["spoken"],
                    refs=line.get("refs", []),
                )
                for line in data.get("lines", [])
            ],
        )


@dataclass(slots=True)
class Script:
    chapters: list[Chapter]
    hooks: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(chapter.spoken_text) for chapter in self.chapters)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [chapter.as_dict() for chapter in self.chapters]


@dataclass(slots=True)
class MediaAsset:
    """A visual, with the provenance needed to prove it may be used."""

    kind: str  # footage | still | diagram | concept | thumb
    path: str
    chapter: int | None = None
    order_idx: int = 0
    duration_s: float = 0.0
    source_url: str = ""
    nasa_id: str | None = None
    credit: str = ""
    license_ok: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "chapter": self.chapter,
            "order_idx": self.order_idx,
            "duration_s": self.duration_s,
            "source_url": self.source_url,
            "nasa_id": self.nasa_id,
            "credit": self.credit,
            "license_ok": self.license_ok,
            "meta": self.meta,
        }
