"""Making Japanese narration say what the script means.

Google's Japanese TTS mis-reads exactly the things a science channel is made of:
exponents, units, catalogue designations, and kanji that have more than one
reading. The documented fix is IPA phoneme markup, which is expensive to author
and impossible to review by eye.

So the reading is settled in text instead. The script model writes every line
twice — once for the subtitle, once for the voice — and this module supplies the
substitutions it must apply, plus a check that catches what it missed.

`config/terms.yaml` is the dictionary. It starts small and grows every time a
mispronunciation is heard in a finished video.
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from . import paths

# Characters that a Japanese voice either skips or reads in English. Any of
# these surviving into the spoken text is a mispronunciation waiting to happen.
RISKY_PATTERN = re.compile(
    r"""
      \^\d+                # 10^24
    | [A-Za-z]{2,}         # Kepler, JWST, km
    | [×÷±≈≠≦≧∼]           # maths symbols
    | \d+\s*[%°]           # 30%, 45°
    | \d+/\d+              # fractions
    | [･·]                 # middle dots inside designations
    """,
    re.VERBOSE,
)

# Substitutions applied before the check, for forms that are always read the
# same way. Longer keys first so "光年" wins over "年".
BUILTIN_READINGS: dict[str, str] = {
    "×10^": "かける10の",
    "km/s": "キロメートル毎秒",
    "km/h": "キロメートル毎時",
    "m/s": "メートル毎秒",
    "%": "パーセント",
    "°C": "度",
    "°": "度",
    "×": "かける",
    "÷": "わる",
    "≠": "イコールではない",
    "≦": "以下",
    "≧": "以上",
    "∼": "およそ",
    "光年": "こうねん",
    "天文単位": "てんもんたんい",
    "太陽質量": "たいようしつりょう",
    "重力波": "じゅうりょくは",
    "赤方偏移": "せきほうへんい",
    "白色矮星": "はくしょくわいせい",
    "中性子星": "ちゅうせいしせい",
    "超新星": "ちょうしんせい",
    "星間物質": "せいかんぶっしつ",
    "系外惑星": "けいがいわくせい",
    "木星": "もくせい",
    "水星": "すいせい",
    "金星": "きんせい",
    "火星": "かせい",
    "土星": "どせい",
    "天王星": "てんのうせい",
    "海王星": "かいおうせい",
    "冥王星": "めいおうせい",
}


class UnreadableText(ValueError):
    """Spoken text still contains something the voice cannot read correctly."""


@lru_cache(maxsize=1)
def load_terms() -> dict[str, str]:
    """Merge the built-in readings with config/terms.yaml (the file wins)."""
    terms = dict(BUILTIN_READINGS)
    path = paths.CONFIG_DIR / "terms.yaml"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        terms.update({str(k): str(v) for k, v in (data.get("readings") or {}).items()})
    return terms


def reset_cache() -> None:
    load_terms.cache_clear()


def apply_readings(text: str, terms: dict[str, str] | None = None) -> str:
    """Replace known terms with their spoken form, longest match first."""
    table = terms if terms is not None else load_terms()
    for key in sorted(table, key=len, reverse=True):
        text = text.replace(key, table[key])
    return text


def risky_fragments(text: str) -> list[str]:
    """Everything left that the voice is likely to get wrong."""
    return sorted(set(RISKY_PATTERN.findall(text)))


def check_spoken(text: str) -> list[str]:
    """Fragments still unreadable after the dictionary has been applied."""
    return risky_fragments(apply_readings(text))


def prepare_spoken(text: str, strict: bool = False) -> str:
    """Turn a written line into one safe to synthesise.

    With `strict`, refuses rather than shipping a line the voice will mangle —
    used where a human will not hear the result before it is published.
    """
    spoken = apply_readings(text)
    remaining = risky_fragments(spoken)
    if remaining and strict:
        raise UnreadableText(
            f"cannot read {remaining} aloud. Add them to config/terms.yaml, or have the "
            "script write the reading out in kana."
        )
    return spoken


# The instruction handed to the script model. Kept next to the checker so the
# two cannot drift apart.
SPOKEN_TEXT_RULES = """\
各行は「display」（字幕用）と「spoken」（読み上げ用）の2つを書くこと。

spoken では次をすべて日本語の読みに開くこと:
- 指数表記: 10^24 kg → 10の24乗キログラム
- 単位記号: km/s → キロメートル毎秒、°C → 度
- ローマ字の固有名詞: Kepler-452b → ケプラー452b、JWST → ジェイムズ・ウェッブ宇宙望遠鏡
- 数式記号: × → かける、± → プラスマイナス、≈ → およそ
- 分数・比: 2/3 → 3分の2
- 読みが割れる漢字は、初出時にカタカナで開く

display は表示用なので元の表記のままでよい。"""
