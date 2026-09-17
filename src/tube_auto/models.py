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

# Who says a line. The explainer carries the video; the listener is the
# viewer's stand-in — objecting, paraphrasing, being surprised, asking the
# next question — and speaks about a quarter of the lines, as in the
# reference channel.
SPEAKERS = ("explainer", "listener")

# Sprite expressions a line may ask for. Each needs <expression>_open.png and
# <expression>_closed.png in the character's sprite folder.
EXPRESSIONS = ("normal", "happy", "surprised", "thinking", "sad")


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
    # Whether this theme may use NASA *video*, which is opt-in and off by default.
    #
    # Three separate test videos each shipped something no metadata filter could
    # have caught: a presenter and the insignia, a "MISSION FEATURE" slate, and
    # a third-party ESO/Hubble credit burned into the frame. Text about a clip
    # cannot describe what is inside it. Mission themes have enough clean
    # material to be worth the review; theory themes have three to nine usable
    # clips and are better served by diagrams.
    allow_footage: bool = False
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
    # What the stage does while this line is spoken: the DSL lines the model
    # wrote, and the operations they parse to (see canvas/dsl.py). Kept both
    # ways so a stored script can be re-rendered without re-parsing.
    visual: list[str] = field(default_factory=list)
    ops: list[dict[str, Any]] = field(default_factory=list)
    expression: str = "normal"
    # set by validation: the visual could not be applied (not stored)
    broken: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "display": self.display,
            "spoken": self.spoken,
            "refs": self.refs,
            "visual": self.visual,
            "ops": self.ops,
            "expression": self.expression,
        }

    @classmethod
    def from_dict(cls, line: dict[str, Any]) -> Line:
        return cls(
            speaker=line["speaker"],
            display=line["display"],
            spoken=line["spoken"],
            refs=line.get("refs", []),
            visual=line.get("visual", []),
            ops=line.get("ops", []),
            expression=line.get("expression", "normal") or "normal",
        )


@dataclass(slots=True)
class Chapter:
    title: str
    visual_intent: str = ""  # English search phrase for a photo to dim behind the stage
    lines: list[Line] = field(default_factory=list)
    key: str = ""            # which slot of the episode skeleton this fills
    hooks: list[str] = field(default_factory=list)   # opener only: the three opening lines

    @property
    def display_text(self) -> str:
        return "".join(line.display for line in self.lines)

    @property
    def spoken_text(self) -> str:
        return "".join(line.spoken for line in self.lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "visual_intent": self.visual_intent,
            "lines": [line.as_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chapter:
        return cls(
            title=data["title"],
            visual_intent=data.get("visual_intent", ""),
            lines=[Line.from_dict(line) for line in data.get("lines", [])],
            key=data.get("key", ""),
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
