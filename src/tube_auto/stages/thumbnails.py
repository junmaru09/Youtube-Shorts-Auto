"""Stage 7: the three thumbnails.

A thumbnail is not a small picture of the video. It is decided at roughly
210x118 pixels in a mobile feed, and at that size a twenty-character Japanese
headline is a grey smear. So the governing constraint is not "make an image" but
**at most about nine characters, enormous** — everything else here follows from
that.

The background comes from the episode's own NASA stills rather than a generated
illustration. Three reasons: the stills are already downloaded and already
rights-cleared, so no new spend and no new licence question; a real telescope
image is the thing this channel actually has and a summary channel does not; and
generated cover art is precisely what YouTube's inauthentic-content rules are
looking at.

The three variants are different *arguments*, not three crops of the same idea:
a number, a question, and the bare subject. YouTube's Test & Compare picks a
winner but tells you nothing about why, so the variants have to differ in a way
that is legible afterwards. Varying only the font would produce a winner that
teaches nothing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import brand as brand_mod
from .. import config, db, ffmpeg, paths

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1280, 720
# YouTube rejects thumbnails over 2 MB. JPEG quality is stepped down until the
# file fits rather than guessed at, because a rejected thumbnail fails at upload
# time — after the video is already on the platform.
MAX_BYTES = 2 * 1024 * 1024
JPEG_QUALITY_STEPS = (2, 4, 7, 12)

# Readability at feed size. Measured against the mobile rendering, not the
# 1280x720 file: nine characters is about the point where a Japanese line stops
# being a shape and starts being words.
MAX_CHARS_PER_LINE = 9
MAX_LINES = 2

# The duration badge sits in the bottom-right corner, so nothing important goes
# there. The text block is centred in the upper two thirds.
TEXT_CENTRE_Y = 0.42
# The stroke around each glyph adds to its width, so the nominal safe area has to
# leave room for it — at 0.88 the longest line ran into both edges.
SAFE_WIDTH = 0.82

VARIANTS = ("number", "question", "subject")


@dataclass(slots=True)
class ThumbnailResult:
    made: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


def variant_paths(idea_id: int) -> list[Path]:
    """Where an idea's thumbnails live. Deterministic, so nothing needs a lookup."""
    return [paths.THUMBS_DIR / f"idea_{idea_id:05d}_{name}.jpg" for name in VARIANTS]


# --- choosing the words ------------------------------------------------------

# A number with a unit attached is the only kind worth enlarging. A bare integer
# ("3つの") means nothing without its sentence; "1400万度" carries on its own.
#
# The trailing (?!の) rejects "3000分の1" — a fraction whose numerator alone is
# not a fact, and which the naive pattern happily read as "3000分".
_NUMBER = re.compile(
    r"(?:約|およそ)?"
    r"(\d[\d,]*(?:\.\d+)?)"
    r"(兆|億|万|千)?"
    r"\s?"
    r"(度|光年|億年|万年|年|倍|km|キロ|秒|分|時間|日|%|パーセント|"
    r"個|回|km/s|キログラム|トン|ケルビン|太陽質量|天文単位|種類)"
    r"(?!の\d)"
)

_SCALE = {"兆": 1e12, "億": 1e8, "万": 1e4, "千": 1e3}

# Below this, a figure is not what anyone clicked for. "1.1倍" is true and dull.
MIN_STRIKING_VALUE = 10

# Openers that eat the character budget without saying anything.
_FILLER = re.compile(r"^(?:じつは|実は|そして|しかし|つまり|ところが|さらに|なんと|ここで)")

_ENDING = re.compile(r"(?:のです|んです|ます|でした|である|です|だ)[。、]?$")


def _tighten(text: str) -> str:
    """Strip a sentence back to the part that carries the meaning."""
    text = text.strip().strip("。、！？!?　 ")
    text = _FILLER.sub("", text)
    text = _ENDING.sub("", text)
    return text.strip("、。 　")


def _biggest_number(script_text: str) -> str | None:
    """The most striking figure in the script, by actual magnitude.

    Ranking by digit count made "1.1倍" tie with "400万倍". Parsing the value and
    applying the scale word settles it, and the floor drops the small true facts
    that nobody clicks on. A human still sees the result before it is published.
    """
    best: tuple[float, str] | None = None
    for match in _NUMBER.finditer(script_text):
        digits, scale_word, unit = match.groups()
        try:
            value = float(digits.replace(",", "")) * _SCALE.get(scale_word or "", 1.0)
        except ValueError:
            continue
        if value < MIN_STRIKING_VALUE:
            continue
        phrase = f"{digits}{scale_word or ''}{unit}"
        if len(phrase) > MAX_CHARS_PER_LINE:
            continue
        if best is None or value > best[0]:
            best = (value, phrase)
    return best[1] if best else None


_QUESTION_TAIL = re.compile(
    r"[なだ]?(?:のでしょう|でしょう|のだろう|だろう|ますか|ですか|のか|のです|なのか)[かね]?[。？?]?$"
)

# Copy is budgeted two characters under the hard wrap limit. Filling both lines
# exactly leaves the wrapper one legal break point, and it is usually mid-word.
COPY_BUDGET = MAX_CHARS_PER_LINE * MAX_LINES - 2


def _question(hooks: list[str], title: str) -> str | None:
    """The episode's central question, cut down to what reads at feed size.

    A spoken hook is a whole sentence — "ブラックホールは、本当に「穴」なのでしょうか？" —
    and the question lives in its last clause. Taking the first clause would keep
    the subject and throw away the question.
    """
    budget = COPY_BUDGET - 1  # the ？ needs a character too
    for candidate in [*hooks, title]:
        text = candidate.strip()
        if not text:
            continue
        if not ("?" in text or "？" in text or text.rstrip("。 ").endswith("か")):
            continue

        body = text.split("？")[0].split("?")[0]
        # Work backwards from the question mark. The nearest clause carries the
        # question; earlier ones carry the subject, which the other two variants
        # already say.
        for clause in reversed(body.split("、")):
            core = _tighten(_QUESTION_TAIL.sub("", clause))
            if core and len(core) <= budget:
                return core + "？"
    return None


def _subject(title: str) -> str:
    """The title, cut to what survives at feed size."""
    core = _tighten(title)
    for separator in ("、", "――", "—", "：", ":"):
        if separator in core:
            core = core.split(separator)[0]
    return core[:COPY_BUDGET] if core else "宇宙"


def _trim_head(text: str, budget: int) -> str:
    """Shorten by dropping the leading modifier, not the trailing noun.

    Japanese puts the head noun last, so cutting the tail off
    「観測でわかった系外惑星の正体」 leaves 「…の正」 — a truncated word. Cutting
    the front leaves 「系外惑星の正体」, which is the thing the episode is about.
    """
    if len(text) <= budget:
        return text

    earliest = len(text) - budget
    # A noun phrase in Japanese starts where kana gives way to kanji or katakana.
    # Scoring by particle instead — the rule the line-wrapper uses — picks the で
    # in 「観測で|わかった…」 and leaves a dangling verb.
    starts = [
        index
        for index in range(earliest, len(text) - 1)
        if _char_class(text[index - 1]) == "hiragana"
        and _char_class(text[index]) in ("kanji", "katakana")
    ]
    return text[starts[0] :] if starts else text[earliest:]


def _topic(title: str, budget: int) -> str:
    """The entity the episode is about, for the caption above a number.

    Not `_trim_head`. Japanese puts the grammatical head last, so trimming
    「ブラックホールの常識が変わった話」 to its head gives 「常識が変わった話」 — correct
    grammar, wrong word. What a caption needs is the thing being talked about,
    and that is the leading run of kanji or katakana.
    """
    # Longest such run, not the first: 「いま分かっている範囲での暗黒物質の正体」 leads
    # with 範囲, and the episode is not about 範囲.
    runs = re.findall(r"[一-鿿゠-ヿー0-9]{2,}", title)
    if runs:
        return max(runs, key=len)[:budget]
    # No noun run at all — take everything before the first particle. The search
    # starts at the second character: 「はやぶさ2…」 begins with one.
    core = _tighten(title)
    lead = core[:1] + re.split(r"[はがをにでとのも、]", core[1:])[0]
    return (lead or core)[:budget]


def choose_text(title: str, hooks: list[str], script_text: str) -> dict[str, str]:
    """One line of copy per variant.

    Every variant falls back to the subject rather than being dropped: three
    thumbnails is what Test & Compare wants, and a missing one silently reduces
    the experiment to a two-way test.
    """
    subject = _subject(title)
    number = _biggest_number(script_text)
    question = _question(hooks, title)
    return {
        "number": number or subject,
        "question": question or (_trim_head(subject, COPY_BUDGET - 3) + "とは？"),
        "subject": subject,
    }


def wrap_for_thumbnail(text: str) -> list[str]:
    """Break the copy into at most two lines, each short enough to read.

    Overflow is cut, not shrunk. Text that has been scaled down to fit is text
    nobody reads in a feed, which defeats the point of the thumbnail.
    """
    text = text.strip()
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]

    text = text[: MAX_CHARS_PER_LINE * MAX_LINES]

    # Both halves must fit on their own line, so the break point is confined to
    # this window before any preference is applied.
    low = max(1, len(text) - MAX_CHARS_PER_LINE)
    high = min(MAX_CHARS_PER_LINE, len(text) - 1)
    middle = (low + high) / 2

    best = max(range(low, high + 1), key=lambda i: _break_score(text, i, middle))
    return [line for line in (text[:best], text[best:]) if line]


def _char_class(char: str) -> str:
    if "぀" <= char <= "ゟ":
        return "hiragana"
    if "゠" <= char <= "ヿ" or char == "ー":
        return "katakana"
    if "一" <= char <= "鿿":
        return "kanji"
    return "other"


def _break_score(text: str, index: int, middle: float) -> float:
    """How good a line break before `index` is.

    Japanese has no spaces, so there is nothing to split on. A particle boundary
    is a real word boundary; failing that, a change of character class is a decent
    proxy for one — without it "系外惑星" gets broken after 系, which reads as a
    typo. Distance from centre only breaks ties.
    """
    score = -abs(index - middle)
    if text[index - 1] in "はがをにでとのも、":
        score += 100
    elif _char_class(text[index - 1]) != _char_class(text[index]):
        score += 50
    return score


# --- drawing -----------------------------------------------------------------


def _font_size(line_count: int, longest: int) -> int:
    """Fill the safe width. The text is the design; there is nothing else."""
    usable = WIDTH * SAFE_WIDTH
    by_width = int(usable / max(longest, 1))
    by_height = int(HEIGHT * (0.30 if line_count == 1 else 0.22))
    return max(56, min(by_width, by_height, 190))


def _pick_background(assets: list[Any], variant_index: int) -> Path | None:
    """A different cleared still per variant, so the three do not look identical."""
    usable = [
        Path(asset["path"])
        for asset in assets
        if asset["kind"] in ("still", "footage", "diagram")
        and asset["license_ok"]
        and asset["path"]
        and Path(asset["path"]).exists()
    ]
    stills = [p for p in usable if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    pool = stills or usable
    if not pool:
        return None
    return pool[(variant_index * max(1, len(pool) // len(VARIANTS))) % len(pool)]


def _draw(
    lines: list[str],
    background: Path | None,
    output: Path,
    brand: brand_mod.Brand,
    accent: bool,
    caption: str = "",
) -> Path:
    """Composite one thumbnail."""
    font = ffmpeg.find_font(brand.font_path)
    size = _font_size(len(lines), max(len(line) for line in lines))
    border = max(6, size // 14)
    gap = int(size * 0.16)
    caption_size = int(size * 0.34) if caption else 0
    block = len(lines) * size + (len(lines) - 1) * gap
    if caption:
        block += caption_size + gap

    filters = [
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={WIDTH}:{HEIGHT}",
        # An even darkening, not a panel behind the text. A box drew hard grey
        # edges across the picture, which is the single clearest tell of an
        # auto-generated thumbnail.
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color=black@0.45:t=fill",
    ]

    text_files: list[Path] = []
    top = int(HEIGHT * TEXT_CENTRE_Y - block / 2)

    def draw(text: str, y: int, font_size: int, colour: str) -> None:
        text_file = output.parent / f"{output.stem}_{len(text_files)}.txt"
        text_file.write_text(text, encoding="utf-8")
        text_files.append(text_file)
        filters.append(
            f"drawtext=fontfile='{_escape(font)}':textfile='{_escape(text_file)}'"
            f":expansion=none:fontsize={font_size}:fontcolor={colour}"
            f":borderw={max(4, font_size // 14)}:bordercolor=black@0.92"
            f":x=(w-text_w)/2:y={y}"
        )

    cursor = top
    if caption:
        # A number with no subject is a number. "1200万キロ" could be anything.
        draw(caption, cursor, caption_size, brand.palette["sub"])
        cursor += caption_size + gap
    for index, line in enumerate(lines):
        colour = brand.palette["accent"] if (accent and index == 0) else brand.palette["ink"]
        draw(line, cursor + index * (size + gap), size, colour)

    # Only under a single line, where it anchors the composition. Under a
    # two-line block that already fills the frame it reads as a stray mark, or
    # worse, as a strikethrough on the last line.
    if len(lines) == 1:
        # drawtext's y is the top of a box taller than the glyphs, so clearance
        # is measured generously — at exactly one font size the rule sat close
        # enough to the characters to read as an underline.
        rule_y = cursor + int(size * 1.30) + border
        rule_width = int(WIDTH * 0.22)
        filters.append(
            f"drawbox=x=(iw-{rule_width})/2:y={min(rule_y, HEIGHT - 40)}"
            f":w={rule_width}:h=8:color={brand.palette['accent']}:t=fill"
        )

    source = (
        ["-i", str(background)]
        if background
        else ["-f", "lavfi", "-i", f"color={brand.palette['bg']}:s={WIDTH}x{HEIGHT}"]
    )

    try:
        for quality in JPEG_QUALITY_STEPS:
            ffmpeg._run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                *source, "-frames:v", "1",
                "-vf", ",".join(filters),
                "-q:v", str(quality), str(output),
            ], timeout=120)
            if output.stat().st_size <= MAX_BYTES:
                break
        else:
            raise ffmpeg.FFmpegError(
                f"{output.name} is {output.stat().st_size} bytes even at the lowest "
                f"quality; YouTube's limit is {MAX_BYTES}"
            )
    finally:
        for text_file in text_files:
            text_file.unlink(missing_ok=True)
    return output


def _escape(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    for char in (":", "'", "[", "]", ","):
        text = text.replace(char, f"\\{char}")
    return text


def build(idea_id: int, title: str, hooks: list[str], script_text: str, assets: list[Any]) -> list[Path]:
    """Draw all three variants. Returns the paths in VARIANTS order."""
    brand = brand_mod.load_brand()
    copy = choose_text(title, hooks, script_text)
    paths.THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    subject = _subject(title)
    made: list[Path] = []
    for index, (name, output) in enumerate(zip(VARIANTS, variant_paths(idea_id), strict=True)):
        lines = wrap_for_thumbnail(copy[name])
        # Only the number needs a subject line, and only when it is not already
        # showing the subject because no number was found.
        caption = ""
        if name == "number" and copy["number"] != subject:
            caption = _topic(title, MAX_CHARS_PER_LINE + 4)
        _draw(lines, _pick_background(assets, index), output, brand,
              accent=(name == "number"), caption=caption)
        made.append(output)
        log.info("thumbnail %s for idea %d: %s", name, idea_id, " / ".join(lines))
    return made


def run(limit: int = 1, idea_id: int | None = None) -> ThumbnailResult:
    """Make thumbnails for assembled ideas that have none."""
    result = ThumbnailResult()
    with db.session() as conn:
        if idea_id is not None:
            # Named explicitly, so redraw even if thumbnails already exist —
            # rewording the copy after seeing the first attempt is the main use.
            idea = db.get_idea(conn, idea_id)
            if idea is None or db.render_for(conn, idea_id) is None:
                result.errors.append(
                    f"idea {idea_id}: "
                    + ("まだ組み上がっていません" if idea else "そんな企画はありません")
                )
                result.failed += 1
                return result
            candidates = [idea]
        else:
            candidates = db.ideas_needing_thumbnails(conn)[:limit]

        if not candidates:
            return result

        for idea in candidates:
            current_id = int(idea["id"])
            script_row = db.get_script(conn, current_id)
            assets = db.get_assets(conn, current_id)

            hooks = json.loads(script_row["hooks_json"]) if script_row else []
            script_text = ""
            if script_row:
                script_text = "".join(
                    line["display"]
                    for chapter in json.loads(script_row["chapters_json"])
                    for line in chapter.get("lines", [])
                )

            try:
                made = build(current_id, idea["hook"] or "", hooks, script_text, assets)
            except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, ffmpeg.FontMissing, OSError) as exc:
                log.error("thumbnails failed for idea %d: %s", current_id, exc)
                result.failed += 1
                result.errors.append(f"idea {current_id}: {exc}")
                continue

            # Recorded as assets so the rights audit and `gc` both see them.
            db.replace_assets(conn, current_id, "thumb", [
                {
                    "path": str(path),
                    "order_idx": index,
                    "credit": brand_mod.load_brand().credit_line,
                    "license_ok": True,
                    "meta": {"variant": VARIANTS[index]},
                }
                for index, path in enumerate(made)
            ])
            # Variant 1 ships with the upload; the operator runs Test & Compare
            # in Studio with the other two, which has no API.
            db.set_render_thumb(conn, current_id, str(made[0]))
            conn.commit()

            result.made += 1
            result.details.append({"idea_id": current_id, "paths": [str(p) for p in made]})

    return result


def summary_line(settings: dict | None = None) -> str:
    settings = settings or config.load_settings()
    return f"{len(VARIANTS)} variants per video at {WIDTH}x{HEIGHT}"
