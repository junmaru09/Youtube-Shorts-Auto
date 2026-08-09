"""The channel's identity, in one place.

YouTube's January 2026 enforcement wave judged channels on "whether the channel
has its own brand", not on which tools made the video. A channel assembled by a
program has no brand unless one is designed in, so every recurring element —
who speaks, how a video opens and closes, what question each episode always
answers, what it looks like — is defined here rather than left to whatever the
model produces that day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from . import paths


@dataclass(slots=True)
class Navigator:
    """A recurring voice. Fixed across every episode, on purpose."""

    role: str
    name: str
    persona: str
    voice: str          # Chirp 3 HD voice name, e.g. ja-JP-Chirp3-HD-Charon
    speaking_rate: float = 1.0


@dataclass(slots=True)
class Brand:
    channel_name: str
    tagline: str
    navigators: dict[str, Navigator]
    # The question every episode answers in its final chapter. This is the
    # channel's editorial angle, and the main thing a summary-rewriting channel
    # cannot copy.
    signature_question: str
    closing_line: str
    palette: dict[str, str]
    font_path: str | None
    telop: dict[str, Any]
    disclosure: str
    credit_line: str

    @property
    def explainer(self) -> Navigator:
        return self.navigators["explainer"]

    @property
    def listener(self) -> Navigator:
        return self.navigators["listener"]

    def voice_map(self) -> dict[str, str]:
        return {role: nav.voice for role, nav in self.navigators.items()}


DEFAULT_BRAND: dict[str, Any] = {
    "channel_name": "そらのしくみ",
    "tagline": "宇宙と地球を、一次情報から。",
    "signature_question": "この発見は、それまでの何を覆したか",
    "closing_line": "今日の話のもとになった論文と資料は、概要欄にすべて置いてあります。",
    "navigators": {
        "explainer": {
            "name": "カナタ",
            "persona": "落ち着いた解説役。断定を避け、分かっていないことは分かっていないと言う。",
            "voice": "ja-JP-Chirp3-HD-Charon",
            "speaking_rate": 1.0,
        },
        "listener": {
            "name": "ミオ",
            "persona": "聞き役。視聴者が引っかかるところで短く一度だけ質問する。相槌は打たない。",
            "voice": "ja-JP-Chirp3-HD-Aoede",
            "speaking_rate": 1.02,
        },
    },
    "palette": {
        "bg": "#05070F",
        "ink": "#FFFFFF",
        "accent": "#FFD34D",
        "sub": "#9FB4D8",
        "grid": "#1E2A44",
    },
    "font_path": None,
    "telop": {
        "keyword_size": 64,
        "subtitle_size": 52,
        "chapter_size": 88,
        "border": 6,
    },
    "disclosure": "※この動画のナレーションと図解は生成AIで制作しています。",
    "credit_line": "Video / Images: NASA (public domain). NASAは本チャンネルを推奨していません。",
}


def _brand_file():
    return paths.CONFIG_DIR / "brand.yaml"


def load_brand() -> Brand:
    """Read config/brand.yaml, falling back to the built-in defaults."""
    data = dict(DEFAULT_BRAND)
    path = _brand_file()
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            override = yaml.safe_load(handle) or {}
        data.update(override)

    navigators = {
        role: Navigator(role=role, **spec) for role, spec in data["navigators"].items()
    }
    return Brand(
        channel_name=data["channel_name"],
        tagline=data["tagline"],
        navigators=navigators,
        signature_question=data["signature_question"],
        closing_line=data["closing_line"],
        palette=data["palette"],
        font_path=data.get("font_path"),
        telop=data["telop"],
        disclosure=data["disclosure"],
        credit_line=data["credit_line"],
    )


def description_header(brand: Brand) -> str:
    """The first lines of every description.

    The AI disclosure and the NASA credit lead, because YouTube truncates
    descriptions in most surfaces and a credit nobody sees is not a credit.
    """
    return "\n".join([brand.disclosure, brand.credit_line])


@dataclass(slots=True)
class ChapterPlan:
    """The fixed shape of an episode.

    Same skeleton every time is the point: a viewer should recognise the channel
    from its structure. `share` is the fraction of the target duration.
    """

    key: str
    title: str
    share: float
    intent: str


EPISODE_PLAN: list[ChapterPlan] = [
    ChapterPlan("hook", "つかみ", 0.02,
                "予想を裏切る数字か問いを一つ。5秒以内に言い切る。"),
    ChapterPlan("intro", "今日の話", 0.03,
                "何を扱うか、なぜ今なのか。一次ソースの日付に触れる。"),
    ChapterPlan("basis", "前提", 0.17,
                "話を理解するのに要る背景。図解が主役。"),
    ChapterPlan("main", "本題", 0.23,
                "観測や実験そのもの。実写映像が主役。"),
    ChapterPlan("detail", "詳しく", 0.25,
                "数字とデータ。すべて出典つき。図解と静止画。"),
    ChapterPlan("meaning", "何が覆ったか", 0.20,
                "チャンネル固有の視点。それまでの理解のどこが変わったか。"),
    ChapterPlan("outro", "まとめ", 0.10,
                "要点の再掲、出典の明示、次回予告。"),
]

# Chapters whose visuals should lean on real footage rather than diagrams.
FOOTAGE_HEAVY = frozenset({"main", "hook"})


def target_chars(duration_minutes: float, chars_per_minute: int = 400) -> int:
    """Japanese narration runs roughly 400 characters per minute at normal pace."""
    return int(duration_minutes * chars_per_minute)
