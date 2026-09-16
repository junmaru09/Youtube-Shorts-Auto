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
    # VOICEVOX speaker id (the style id from /speakers), used when tts.provider
    # is voicevox. 3 = ずんだもん ノーマル, 2 = 四国めたん ノーマル.
    voicevox_speaker: int | None = None
    # Sprite folder under assets/sprites/, and the credit the licence asks for.
    sprite: str = ""
    credit: str = ""


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
    "channel_name": "ずんだもんの宇宙ノート",
    "tagline": "宇宙と地球のふしぎを、身近な話から。",
    "signature_question": "この発見は、それまでの何を覆したか",
    "closing_line": "今日の話のもとになった論文と資料は、概要欄に置いてあるのだ。",
    "navigators": {
        # The two voices are the format. ずんだもん explains, 四国めたん asks —
        # the same pairing (and the same green / purple subtitles) the reference
        # channel uses, with characters whose voices and sprites are free to
        # use commercially with a credit.
        "explainer": {
            "name": "ずんだもん",
            "persona": "解説役。語尾は「〜のだ」「〜なのだ」。一人称は「ボク」。断定を避け、"
                       "分かっていないことは分かっていないと言う。難しい言葉は必ず図と身近な例で言い直す。",
            "voice": "ja-JP-Chirp3-HD-Charon",
            "speaking_rate": 1.0,
            "voicevox_speaker": 3,
            "sprite": "zundamon",
            "credit": "VOICEVOX:ずんだもん",
        },
        "listener": {
            "name": "四国めたん",
            "persona": "聞き役。一人称は「わたくし」、語尾は「〜かしら」「〜のね」「〜じゃない」。"
                       "少しツンデレ気味で、視聴者の代わりに反論し、言い換え、驚き、次の疑問を投げる。",
            "voice": "ja-JP-Chirp3-HD-Aoede",
            "speaking_rate": 1.02,
            "voicevox_speaker": 2,
            "sprite": "metan",
            "credit": "VOICEVOX:四国めたん",
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
    "disclosure": "※この動画の台本と図解は生成AIを使って制作しています。",
    "credit_line": "VOICEVOX:ずんだもん / VOICEVOX:四国めたん / Images: NASA (public domain)。NASAは本チャンネルを推奨していません。",
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

    The shape is the reference channel's, measured: small talk in the room,
    the topic named only at the 9% mark after the background has been built
    up in figures, a discovery story with a person's name over it, the
    mechanism one arrow at a time, the main figure rebuilt as the recap, and
    an open question before a thirty-second goodbye. Nothing here is a news
    report: no "today's topic" card, no bullet-point summary.
    """

    key: str
    title: str
    share: float
    intent: str
    # What the stage should be doing while this chapter plays.
    visual: str = ""
    # Whether the listener closes the chapter with the question the next
    # chapter answers — the reference's yellow question at a section's end.
    ends_with_question: bool = False


EPISODE_PLAN: list[ChapterPlan] = [
    ChapterPlan("opener", "雑談導入", 0.08,
                "部屋で、身近な観察か素朴な違和感から始める。本題の名前も論文の結論も出さない。"
                "listener が切り出し、explainer が「実は違うのだ」と逆説を返す。",
                visual="background room。図は最小限、逆説の一言だけ label。",
                ends_with_question=True),
    ChapterPlan("context", "背景", 0.12,
                "本題を理解するのに要る前提を、図を1枚ずつ積んで腹落ちさせる。"
                "listener の言い換え（要するに〜なのね）と反論（そう言われても実感がない）で進める。",
                visual="1行1操作。timeline、compare、pie、earth_arc など背景に合う図を build する。"),
    ChapterPlan("name", "本題", 0.03,
                "ここで初めて本題の名前を出す。listener が「え、本当にそんなことが？」と大疑問を投げる。",
                visual="title で本題名を大きく。背景を差し替える。",
                ends_with_question=True),
    ChapterPlan("history", "発見史", 0.15,
                "誰が・いつ・何に気づいたか。人名を heading に置き、その人が見たものを図で build する。"
                "章末は当時の謎を listener の問いにする。",
                visual="heading に人名、年は label。地層・観測・実験の図を1手ずつ。",
                ends_with_question=True),
    ChapterPlan("mechanism1", "仕組みⅠ", 0.20,
                "第一の仕組み。因果を arrow と label で1手ずつ。数字は出典つき。",
                visual="1つの図を10行以上かけて育てる。clear は章頭だけ。",
                ends_with_question=True),
    ChapterPlan("mechanism2", "仕組みⅡ", 0.20,
                "第二の仕組み、または前章への反論への答え。対比なら columns か table。",
                visual="前章の図を clear せず書き足すか、columns / table で対比。"),
    ChapterPlan("replay", "図で復習", 0.07,
                "今日の主図を空の状態から4〜6行で再構築しながら要点を言う。文字の箇条書きは禁止。"
                "最後に listener が残る疑問を投げる。",
                visual="clear してから主図をもう一度 build。list_add は使わない。",
                ends_with_question=True),
    ChapterPlan("open", "未解決", 0.12,
                "分かっていないこと、別の仮説、そして「この発見は、それまでの何を覆したか」。"
                "新しい図を1〜2枚。",
                visual="仮説ごとに小さな図。分かっていない所は label に「？」。"),
    ChapterPlan("close", "締め", 0.03,
                "部屋に戻り、3〜4行で締める。最後の行は closing_line。",
                visual="background room、clear。"),
]

# Chapters whose visuals should lean on real footage rather than diagrams.
# None, now: the stage is drawn, and photos only ever sit dimmed behind it.
FOOTAGE_HEAVY: frozenset[str] = frozenset()


def target_chars(duration_minutes: float, chars_per_minute: int = 400) -> int:
    """Japanese narration runs roughly 400 characters per minute at normal pace."""
    return int(duration_minutes * chars_per_minute)
