"""Stage 2: write the script.

Three things this stage refuses to let through, because each of them is
invisible until it has already cost something:

- **An uncited number.** Checked against the refs research collected, and the
  script is rejected rather than narrated.
- **Text the voice cannot read.** Japanese TTS mis-reads exponents, units and
  catalogue names, so every line carries a separate spoken form and that form is
  verified before any audio is synthesised.
- **A script of the wrong length.** Narration runs about 400 characters a
  minute; a script half the target length produces a nine-minute video that was
  planned as eighteen.

The opening is generated separately, three ways. Retention data puts the
decision in the first five seconds, so that part gets its own prompt and a
choice rather than whatever the model wrote first.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .. import brand as brand_mod
from .. import citations, config, db, reading
from pathlib import Path

from ..canvas import Canvas, CanvasError, dsl
from ..canvas.manual import VISUAL_MANUAL
from ..llm import LLMClient
from ..models import EXPRESSIONS, Chapter, Line, Script

log = logging.getLogger(__name__)

# How far off the character target a script may land before it is rejected.
LENGTH_TOLERANCE = 0.30

# One or two sentences of narration. The length target is expressed to the
# model as lines-per-chapter at this size, because a character total is not a
# thing it can hit while writing.
#
# Calibrated, not chosen: asked for 120 lines "of about 60 characters" the
# model delivered 5,058 characters, so its natural line runs about 42. The
# figure here is what it actually writes, and the line count is derived from it.
CHARS_PER_LINE = 33

SCRIPT_TOOL = {
    "name": "submit_script",
    "description": "Submit the full script for one video.",
    # Strict, so the schema is enforced rather than advisory. Without it the
    # model returned zero hooks against `minItems: 3` and the API accepted it —
    # the rejection was caught by validate(), but only after $0.11 was spent.
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            # Three named fields rather than an array of three: strict mode
            # only accepts minItems of 0 or 1, so "exactly three" cannot be
            # expressed on an array. Three required strings can.
            "hook_1": {
                "type": "string",
                "description": "冒頭5秒で言い切る一文、1案目。予想を裏切る数字か問い。",
            },
            "hook_2": {
                "type": "string",
                "description": "2案目。1案目と違う切り口。",
            },
            "hook_3": {
                "type": "string",
                "description": "3案目。1・2案目と違う切り口。",
            },
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "章のkey（指定されたものをそのまま）"},
                        "title": {"type": "string", "description": "日本語の章タイトル。15文字以内。"},
                        "visual_intent": {
                            "type": "string",
                            "description": (
                                "この章の図の後ろに暗く敷く写真の英語検索語（例: 'antarctica glacier', 'milky way'）。"
                                "羊皮紙や部屋の背景でよければ空文字。"
                            ),
                        },
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "speaker": {
                                        "type": "string",
                                        "enum": ["explainer", "listener"],
                                    },
                                    "display": {
                                        "type": "string",
                                        "description": "字幕に出す文。通常の日本語表記。",
                                    },
                                    "spoken": {
                                        "type": "string",
                                        "description": "読み上げ用。数値・単位・記号・ローマ字を日本語の読みに開いたもの。",
                                    },
                                    "refs": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "この行の数値の出典ID（S1など）。数値がなければ空配列。",
                                    },
                                    "visual": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "この行を話す間に板へ行う操作、1操作1要素、1〜3個。"
                                            "書式は「図の書き方」の通り。板を変えないなら ['hold']。"
                                        ),
                                    },
                                    "expression": {
                                        "type": "string",
                                        "enum": list(EXPRESSIONS),
                                        "description": "話者の表情。",
                                    },
                                },
                                "required": ["speaker", "display", "spoken", "refs", "visual", "expression"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["key", "title", "visual_intent", "lines"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["hook_1", "hook_2", "hook_3", "chapters"],
        "additionalProperties": False,
    },
}


# One chapter at a time. The whole-script tool above is kept for tests and
# for the dry-run path; generation itself goes chapter by chapter because a
# 20,000-token script rejected for one bad line costs a full regeneration,
# and a chapter rejected for one bad line costs a ninth of that.
CHAPTER_TOOL = {
    "name": "submit_chapter",
    "description": "Submit one chapter of the script.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "章のkey（指定されたものをそのまま）"},
            "title": {"type": "string", "description": "日本語の章タイトル。15文字以内。"},
            "visual_intent": {
                "type": "string",
                "description": "この章の図の後ろに暗く敷く写真の英語検索語（例: 'milky way'）。要らなければ空文字。",
            },
            "hooks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "opener の章だけ: 冒頭の最初の一言の3案（本題名を出さない）。他の章は空配列。",
            },
            "lines": SCRIPT_TOOL["input_schema"]["properties"]["chapters"]["items"]["properties"]["lines"],
        },
        "required": ["key", "title", "visual_intent", "hooks", "lines"],
        "additionalProperties": False,
    },
}


@dataclass(slots=True)
class ScriptResult:
    written: int = 0
    rejected: int = 0
    llm_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


def _system_prompt(brand: brand_mod.Brand, theme, target_chars: int) -> str:
    explainer = brand.explainer
    listener = brand.listener
    return f"""\
あなたは日本語の科学解説チャンネル「{brand.channel_name}」の構成作家です。
{brand.tagline}

話者は2人。毎回同じです。
- {explainer.name}（explainer）: {explainer.persona}
- {listener.name}（listener）: {listener.persona}

会話の設計（これが番組の骨格です）:
- listener は視聴者の代わり。役割は4つ: 反論「そう言われても実感がないわ」、
  言い換え「要するに〜ってことなのね」、驚き「え、本当にそんなことが？」、次の疑問。
  全体の**4分の1前後**（20〜40%）を listener が話す。相槌だけの行は禁止。
- 「章末は疑問で終える」と指定された章は、最後の行を疑問文にする。次の章がそれに答える。
- 1行は1〜2文、字幕は30字×2行に収まる長さ（display は60字以内が目安、72字まで）。最後の行は「{brand.closing_line}」。

冒頭（opener）の型。何も知らない人が「見てみよう」と思う入り方にする:
- 本題の名前も、論文も、数字も、専門用語も出さない。**最初の行は listener** で、身近な体験から始める。
- 入り方は3種類のどれか: (a) 体験「昨日〜したんだけど」 (b) 日常の違和感「〜っておかしくない？」
  (c) 思い込みの反転「〜って当たり前よね？」→ explainer「実は違うのだ」。
- 手本（テーマが「空はなぜ青いか」なら）:
    listener: 「今日海に行ってきたんだけど、めっちゃ青くてきれいだったわ」
    listener: 「あれ、でも水って透明よね？ なんで海は青く見えるのかしら」
    explainer: 「いい質問なのだ。海のほかにも青く見えるものはないのだ？」
    listener: 「……空？」
    explainer: 「そう。空も海も同じ理由で青いのだ。今日はそれを説明するのだ」
  この「身近な体験 → 素朴な疑問 → もう一つの例 → 今日の話」の順番を、テーマに合わせて作る。
- 冒頭で難しい話が出た瞬間に視聴者は帰る。opener と context の章は中学生が分かる言葉だけで書く。

言葉の選び方（全章）:
- 専門用語は、図で描けて身近な例で言い直せるものだけ使う。使うときは、その行か次の行で必ず言い換える。
- 数式名・手法名・装置の正式名称・論文の実験条件（「〜モデル」「〜分光装置」「ガンマが…」）は出さない。
  どうしても要るなら「〜という考え方」「〜という望遠鏡」のように噛み砕く。
- 数字は1章に2つまで。数字より「どれくらい大きいか」の比喩を優先する。

図の設計（最重要。視聴者はここで動画の質を判断します）:
- **話に出てくる物は、その物の絵を置く**。物差しの話なら ruler、鐘なら bell、寺なら temple、海なら wave_icon。
  台詞に出た物が絵になっていない章は差し戻される。言葉を箱に入れる concept は、絵にできない抽象語だけ。
- **文字より絵**。ラベルは絵に添える一言（8字以内）。ラベルの数が絵の数の2倍を超える章は差し戻される。
- **型を混ぜる**。波・グラフ・矢印ばかり使わない。手順は steps、循環は cycle、並べるなら stack、比べるなら compare、
  割合は pie、時間は timeline、天体は sun/earth_globe/galaxy、人は person や scientist。
- **1行につき板を1手動かす**。同じ絵のまま3行以上話さない（hold の連続は2行まで）。
- 図は積み上げる。置く→矢印→ラベル→強調、と行ごとに1手ずつ。1つの図に10行かけてよい。
- 文字の箇条書き（list_add）に逃げない。関係は arrow、対比は columns/table、割合は pie、時間は timeline。
- ラベルは12文字以内。長い説明は台詞に任せる。
- 章の最初の行は clear から。背景は章ごとに background で決める（部屋=room、宇宙=space、それ以外=parchment）。

{VISUAL_MANUAL}

このチャンネルの視点:
- 「未解決」の章では必ず「{brand.signature_question}」に答える。
- 分かっていないことは「分かっていない」と言う。断定で埋めない。

{citations.CITATION_RULES}

{reading.SPOKEN_TEXT_RULES}

進め方:
- 台本は**章ごとに**依頼される。全体の章立てと、ここまでの台詞が毎回渡されるので、続きとして書く。
- 章に指定された**行数**（±3行）を守る。1行は1〜2文、{CHARS_PER_LINE}文字前後。
  全章あわせて約{target_chars}文字（{target_chars // 400}分。日本語のナレーションは1分あたり約400文字）。
- 板（図）は章をまたいで残る。前の章の図を使い回すなら clear せずに書き足してよい。
- 差し戻された章は、指摘された行だけでなく章全体を提出し直す。

このテーマで禁止されていること:
{chr(10).join('- ' + b for b in theme.banned)}"""


def _sources_block(idea, sources) -> str:
    """The idea and its sources: the same on every call for this idea, so it
    sits in the cached prefix rather than being paid for nine times."""
    parts = [
        f"タイトル: {idea['hook']}",
        f"この動画が答える問い: {idea['scene_summary']}",
        "",
        "使える一次ソース（これ以外の事実を書かないこと）:",
    ]
    for source in sources:
        when = (source["published_at"] or "")[:10]
        parts.append(f"[{source['ref']}] ({source['kind']}, {when}) {source['title']}")
        if source["url"]:
            parts.append(f"      {source['url']}")
        if source["summary"]:
            parts.append(f"      {source['summary'][:600]}")
    return "\n".join(parts)


def _chapter_prompt(plan: list[dict[str, Any]], index: int, previous: list[Chapter],
                    stage_items: list[str], feedback: str = "") -> str:
    """What to write now: the whole skeleton for orientation, the chapters
    already written for continuity, this chapter's brief, and the items
    currently on the stage so references resolve."""
    chapter = plan[index]
    parts = ["台本は章ごとに書きます。全体の章立て:"]
    for i, c in enumerate(plan):
        mark = "→" if i == index else " "
        parts.append(f"  {mark} {c['key']}: {c['title']} — {c['covers'][:40]}（{c['lines']}行）")
    if previous:
        parts += ["", "ここまでに書いた台詞（続きとして自然につながるように）:"]
        if len(previous) > 2:
            parts.append("  （それ以前の章: " + " / ".join(c.title for c in previous[:-2]) + "）")
        for c in previous[-2:]:
            parts.append(f"[{c.key}]")
            for line in c.lines:
                parts.append(f"  {line.speaker}: {line.display}")
    parts += ["", f"いま書く章: {chapter['key']}「{chapter['title']}」",
              f"内容: {chapter['covers']}",
              f"図: {chapter['visual']}" if chapter.get("visual") else "",
              f"行数: {chapter['lines']}行（±3行）。1行は1〜2文、{CHARS_PER_LINE}字前後。"]
    if chapter.get("ends_with_question"):
        parts.append("この章は疑問で終える（最後の2行のどちらかを listener の疑問文に）。")
    if index == 0:
        parts.append("hooks に、この章の最初の一言の3案を入れる（本題名・数字・専門用語なし）。")
    else:
        parts.append("hooks は空配列。")
    if index == len(plan) - 1:
        parts.append("最後の行は closing_line で締める。")
    parts.append("最初の行の visual は clear から始める（背景を変えるなら background も）。")
    if stage_items:
        parts.append(f"clear する前の板にあるもの: {', '.join(stage_items[-10:])}")
    if feedback:
        parts += ["", feedback]
    parts.append("submit_chapter で提出してください。")
    return "\n".join(p for p in parts if p is not None)


def _user_prompt(idea, sources, plan: list[dict[str, Any]], target_chars: int) -> str:
    parts = [_sources_block(idea, sources)]

    total_lines = sum(c["lines"] for c in plan)
    parts += ["", f"章構成（key はこのまま使うこと。各章の行数は必ず満たすこと。合計 {total_lines} 行）:"]
    for chapter in plan:
        parts.append(
            f"  {chapter['key']}: {chapter['title']} — {chapter['covers']}"
            f"（{chapter['lines']}行・約{chapter['chars']}文字）"
            + (f"\n      図: {chapter['visual']}" if chapter.get("visual") else "")
            + ("\n      章末は疑問で終える" if chapter.get("ends_with_question") else "")
        )

    parts += [
        "",
        f"1行は1〜2文、{CHARS_PER_LINE}文字前後。各章の行数を満たすと spoken の合計が約{target_chars}文字になります。",
        "行数が足りない台本、図の参照が壊れている台本、listener が少なすぎる台本は自動で差し戻され、"
        "書き直しの費用がかかります。",
        "hook_1〜hook_3 は雑談導入の最初の一言の3案（本題名を出さない）。submit_script で提出してください。",
    ]
    return "\n".join(parts)


def _chapter_plan(idea_plan: list[dict[str, Any]] | None, target_chars: int) -> list[dict[str, Any]]:
    """Merge the research stage's chapter plan with the brand's fixed skeleton.

    The skeleton decides the shape and the share of the runtime; research only
    fills in what each chapter is about. That is what makes every episode
    recognisably the same channel.
    """
    by_key = {c.get("key"): c for c in (idea_plan or [])}
    plan = []
    for chapter in brand_mod.EPISODE_PLAN:
        supplied = by_key.get(chapter.key, {})
        plan.append(
            {
                "key": chapter.key,
                "title": supplied.get("title") or chapter.title,
                "covers": supplied.get("covers") or chapter.intent,
                "visual_intent": supplied.get("visual_intent", ""),
                "visual": chapter.visual,
                "ends_with_question": chapter.ends_with_question,
                "chars": int(target_chars * chapter.share),
                # A line count is something the model can actually hit; a
                # character total is not. Asked only for 7,200 characters it
                # wrote 2,566 — a third — because it cannot count as it writes.
                "lines": max(2, round(target_chars * chapter.share / CHARS_PER_LINE)),
            }
        )
    return plan


def _parse(payload: dict[str, Any]) -> Script:
    chapters = [
        Chapter(
            key=chapter.get("key", ""),
            title=chapter["title"],
            visual_intent=chapter.get("visual_intent", ""),
            lines=[
                Line(
                    speaker=line["speaker"],
                    display=line["display"].strip(),
                    spoken=line["spoken"].strip(),
                    refs=[r.strip() for r in line.get("refs", []) if r.strip()],
                    visual=[v.strip() for v in line.get("visual", []) if v.strip()],
                    expression=line.get("expression", "normal") or "normal",
                )
                for line in chapter.get("lines", [])
            ],
        )
        for chapter in payload.get("chapters", [])
    ]
    hooks = [
        str(payload.get(key, "")).strip()
        for key in ("hook_1", "hook_2", "hook_3")
        if str(payload.get(key, "")).strip()
    ]
    return Script(chapters=chapters, hooks=hooks)


# The listener's share of lines. The reference channel runs 25-35%; below the
# floor it is a monologue, above the ceiling it is a sitcom.
LISTENER_SHARE = (0.15, 0.50)
# Lines in a row that leave the stage untouched before it counts as static.
# The room chapters are conversation; the reference holds its room for
# thirty seconds at a time there and nowhere else.
MAX_HOLD_RUN = 3
MAX_HOLD_RUN_ROOM = 5
ROOM_CHAPTERS = {"opener", "close"}
# The figure each chapter must have at least one of, by op or element name.
# The first scripts leaned on label+arrow everywhere; this is what the
# reference does chapter by chapter.
REQUIRED_FIGURES: dict[str, set[str]] = {
    "context": {"timeline", "pie", "compare", "scatter", "earth_arc", "earth_globe", "columns", "ladder", "box_row"},
    "history": {"heading", "person", "telescope", "timeline"},
    "mechanism1": {"chain", "columns", "table", "scatter", "wave", "balance", "panel", "pie", "compare"},
    "mechanism2": {"chain", "columns", "table", "scatter", "wave", "balance", "panel", "pie", "compare"},
}

# Katakana words of six or more that are everyday, not jargon, for the
# opener check.
OPENER_OK_KATAKANA = {"インターネット", "スマートフォン", "コンビニ", "テレビ", "ニュース", "アイスクリーム",
                      "エアコン", "コーヒー", "チョコレート", "プラネタリウム", "ペットボトル"}

# A subtitle is two rows of SUBTITLE_WRAP characters. Longer lines overflow
# the band, and a line that long is a paragraph anyway.
MAX_DISPLAY_CHARS = 72


def check_visuals(script: Script) -> list[str]:
    """Parse every line's visual DSL and dry-run it on a canvas.

    Populates `line.ops` as a side effect, so the stored script carries the
    parsed operations. A broken reference ("arrow from=ice3" when ice3 was
    never placed) is caught here, before narration is paid for, with the
    chapter and line it sits on.
    """
    problems: list[str] = []
    canvas = Canvas(assets=_library())
    hold_run = 0
    for ci, chapter in enumerate(script.chapters):
        max_hold = MAX_HOLD_RUN_ROOM if chapter.key in ROOM_CHAPTERS else MAX_HOLD_RUN
        hold_run = 0
        for li, line in enumerate(chapter.lines):
            where = f"{chapter.key or ci}:{li + 1}「{line.display[:14]}」"
            try:
                ops = dsl.parse_many(line.visual) if line.visual else [{"op": "hold"}]
            except dsl.DSLError as exc:
                problems.append(f"{where} の visual が読めない: {exc}")
                continue
            line.ops = ops
            for op in ops:
                try:
                    canvas.apply(op)
                except CanvasError as exc:
                    problems.append(f"{where} の visual が適用できない: {exc}")
                    break
            if all(op["op"] in ("hold", "expression") for op in ops):
                hold_run += 1
                if hold_run == max_hold + 1:
                    problems.append(f"{where} で板が{max_hold + 1}行以上動いていない（1行1手で図を育てること）")
            else:
                hold_run = 0
    return problems[:8]


def check_structure(script: Script, plan: list[dict[str, Any]]) -> list[str]:
    """The shape rules: listener share, question endings, clear at chapter start."""
    problems: list[str] = []
    lines = [line for c in script.chapters for line in c.lines]
    if not lines:
        return problems
    share = sum(1 for line in lines if line.speaker == "listener") / len(lines)
    lo, hi = LISTENER_SHARE
    if share < lo:
        problems.append(f"listener の発話が{share:.0%}しかない（{lo:.0%}以上）。反論・言い換え・驚き・疑問を入れること")
    elif share > hi:
        problems.append(f"listener の発話が{share:.0%}と多すぎる（{hi:.0%}以下）")

    wants_question = {c["key"] for c in plan if c.get("ends_with_question")}
    for chapter in script.chapters:
        if not chapter.lines:
            continue
        # the question may be the last line, or the one before it when the
        # explainer answers "let's see" — the reference does both
        tail = [line.display.rstrip() for line in chapter.lines[-2:]]
        if chapter.key in wants_question and not any(("？" in d or "?" in d) for d in tail):
            problems.append(f"章 {chapter.key} は疑問で終えること（最後の行「{chapter.lines[-1].display[:20]}」）")
        first = chapter.lines[0].ops or []
        if chapter.key and first and first[0].get("op") not in ("clear", "background"):
            problems.append(f"章 {chapter.key} の最初の行は clear から始めること")
    return problems


def check_chapter(chapter: Chapter, brief: dict[str, Any], canvas: Canvas, known_refs: set[str]) -> list[str]:
    """Everything wrong with one chapter, against the stage as it stands.

    The canvas passed in is a scratch copy; the caller commits the real one
    only when the chapter is accepted.
    """
    problems: list[str] = []
    if not chapter.lines:
        return ["lines が空。台詞を書くこと"]
    wanted = int(brief.get("lines", len(chapter.lines)))
    if abs(len(chapter.lines) - wanted) > max(3, wanted * 0.35):
        problems.append(f"行数が{len(chapter.lines)}行（指定は{wanted}行±3）")

    max_hold = MAX_HOLD_RUN_ROOM if chapter.key in ROOM_CHAPTERS else MAX_HOLD_RUN
    hold_run = 0
    for li, line in enumerate(chapter.lines):
        where = f"{chapter.key}:{li + 1}「{line.display[:14]}」"
        try:
            ops = dsl.parse_many(line.visual) if line.visual else [{"op": "hold"}]
        except dsl.DSLError as exc:
            problems.append(f"{where} の visual が読めない: {exc}")
            line.ops = [{"op": "hold"}]
            line.broken = True
            continue
        line.ops = ops
        line.broken = False
        for op in ops:
            try:
                canvas.apply(op)
            except CanvasError as exc:
                problems.append(f"{where} の visual が適用できない: {exc}")
                line.broken = True
                break
        if all(op["op"] in ("hold", "expression") for op in ops):
            hold_run += 1
            if hold_run == max_hold + 1:
                problems.append(f"{where} で板が{max_hold + 1}行以上動いていない（1行1手で図を育てること）")
        else:
            hold_run = 0
        if len(line.display) > MAX_DISPLAY_CHARS:
            problems.append(f"{where} の display が{len(line.display)}字（{MAX_DISPLAY_CHARS}字まで。2文に分ける）")
        remaining = reading.check_spoken(line.spoken)
        if remaining:
            problems.append(f"{where} の spoken に読めない表記: {remaining}")
    first = chapter.lines[0].ops
    if not first or first[0].get("op") not in ("clear", "background"):
        problems.append(f"{chapter.key} の最初の行は clear から始めること")

    report = citations.check([chapter.as_dict()], known_refs)
    if report.uncited:
        problems.append(f"{len(report.uncited)} 行が数値を出典なしで述べている: " + "; ".join(u[:30] for u in report.uncited[:3]))
    if report.unknown_refs:
        problems.append(f"存在しない出典ID: {sorted(set(report.unknown_refs))}")

    if chapter.key not in ROOM_CHAPTERS and len(chapter.lines) >= 8:
        placed = sum(1 for line in chapter.lines for op in (line.ops or [])
                     if (op.get("op") in ("add", "compare", "table", "chain", "columns", "panel", "zoom")
                         or (op.get("op") == "place" and op.get("element") != "concept")))
        wanted_placed = max(2, len(chapter.lines) // 6)
        if placed < wanted_placed:
            problems.append(f"{chapter.key} で図（要素）を置く操作が{placed}回しかない（{len(chapter.lines)}行なら{wanted_placed}回以上。"
                            "ラベルと矢印だけで済ませず、要素を置いて育てること）")

    # pictures over words: things the lines mention must appear as pictures,
    # labels must not outnumber pictures, and waves/graphs/arrows must not be
    # the whole vocabulary of a chapter
    if chapter.key not in ROOM_CHAPTERS or len(chapter.lines) >= 6:
        ops = [op for line in chapter.lines for op in (line.ops or [])]
        pictures = set()
        for op in ops:
            if op.get("op") in ("place", "add") and op.get("element") not in ("concept",):
                pictures.add(op["element"])
            if op.get("op") in ("steps", "cycle", "stack"):
                for entry in op.get("items", []):
                    pictures.add(str(entry).partition(":")[0])
        on_stage = {i.props.get("element") for i in canvas.state.items if i.kind == "element"}
        mentioned = mentioned_pictures("".join(line.display for line in chapter.lines))
        have = pictures | on_stage
        missing = [(jp, name) for jp, name in mentioned
                   if name not in have and not (PICTURE_ALIASES.get(name, set()) & have)]
        if missing:
            problems.append(f"{chapter.key} の台詞に出た物の絵を置くこと: "
                            + ", ".join(f"{jp}→{name}" for jp, name in missing[:5]))
        labels = sum(1 for op in ops if op.get("op") == "label")
        if labels > max(3, 2 * len(pictures)):
            problems.append(f"{chapter.key} はラベル（文字）が{labels}個に対して絵が{len(pictures)}個。"
                            "文字で説明せず、物の絵や steps/cycle/stack で示すこと")
        liney = sum(1 for op in ops if op.get("op") in ("arrow",) or op.get("element") in ("wave", "scatter", "grid_panel"))
        if len(ops) >= 8 and liney > 0.4 * len(ops):
            problems.append(f"{chapter.key} は波・グラフ・矢印が{liney}/{len(ops)}操作と偏っている。"
                            "絵（イラスト）、steps、cycle、stack、compare を混ぜること")

    wanted_figures = REQUIRED_FIGURES.get(chapter.key)
    if wanted_figures:
        used = {op.get("op") for line in chapter.lines for op in (line.ops or [])}
        used |= {op.get("element") for line in chapter.lines for op in (line.ops or []) if op.get("op") in ("place", "add")}
        if not used & wanted_figures:
            problems.append(f"{chapter.key} には {' / '.join(sorted(wanted_figures))} のどれかを1つは使うこと（この章の図の型）")

    explainer_lines = [line for line in chapter.lines if line.speaker == "explainer"]
    if len(explainer_lines) >= 4:
        noda = sum(1 for line in explainer_lines if re.search(r"のだ[。！？!?」]*$", line.display.rstrip()))
        if noda / len(explainer_lines) < 0.5:
            problems.append(f"ずんだもんの語尾「〜のだ」が {noda}/{len(explainer_lines)} 行しかない（半分以上に）")

    if chapter.key == "opener":
        first = chapter.lines[0]
        if first.speaker != "listener":
            problems.append("opener の最初の行は listener の身近な一言から始めること")
        for li, line in enumerate(chapter.lines[:3]):
            jargon = [w for w in re.findall(r"[ァ-ヴー]{6,}", line.display) if w not in OPENER_OK_KATAKANA]
            if re.search(r"\d", line.display) or jargon:
                problems.append(f"opener:{li + 1}「{line.display[:14]}」 に数字や専門語（{', '.join(jargon) or '数字'}）がある。"
                                "冒頭3行は中学生の言葉だけで")
                break

    if brief.get("ends_with_question"):
        tail = [line.display for line in chapter.lines[-2:]]
        if not any("？" in d or "?" in d for d in tail):
            problems.append(f"{chapter.key} は疑問で終えること（最後の行「{chapter.lines[-1].display[:20]}」）")
    return problems


def validate(script: Script, known_refs: set[str], target_chars: int,
             plan: list[dict[str, Any]] | None = None) -> list[str]:
    """Everything wrong with this script, in the order it would hurt."""
    problems: list[str] = []

    if not script.chapters:
        return ["台本に章がひとつもない"]

    problems += check_visuals(script)
    problems += check_structure(script, plan or [])

    report = citations.check([c.as_dict() for c in script.chapters], known_refs)
    if not report.ok:
        problems.append(report.describe())

    too_long = [f"{c.key}:{i + 1}({len(line.display)}字)" for c in script.chapters
                for i, line in enumerate(c.lines) if len(line.display) > MAX_DISPLAY_CHARS]
    if too_long:
        problems.append(f"{len(too_long)} 行の display が{MAX_DISPLAY_CHARS}字を超えている（字幕に収まらない。2文に分けること）: "
                        + ", ".join(too_long[:5]))

    unreadable: list[str] = []
    for chapter in script.chapters:
        for line in chapter.lines:
            remaining = reading.check_spoken(line.spoken)
            if remaining:
                unreadable.append(f"{remaining} in {line.spoken[:40]}")
    if unreadable:
        problems.append(
            f"{len(unreadable)} 行が読み上げできない表記を含む: {'; '.join(unreadable[:3])}"
        )

    actual = script.char_count
    lines_total = sum(len(c.lines) for c in script.chapters)
    if lines_total == 0:
        problems.append(
            f"{len(script.chapters)} 章あるが lines が全部空。各章の lines に台詞を書くこと"
            "（章の骨組みだけの提出は受け付けない）"
        )
    elif abs(actual - target_chars) > target_chars * LENGTH_TOLERANCE:
        minutes = actual / 400
        lines = sum(len(c.lines) for c in script.chapters)
        per_line = actual / lines if lines else 0
        # The line count and the per-line average say *which* way it missed —
        # too few lines, or lines too short — and that decides the fix.
        problems.append(
            f"分量が目標から外れている: {actual}文字（目標{target_chars}文字、約{minutes:.1f}分）"
            f" — {lines}行、1行平均{per_line:.0f}字"
        )

    if len(script.hooks) < 3:
        problems.append(f"冒頭案が{len(script.hooks)}件しかない（3件必要）")

    return problems


def run(
    limit: int = 1,
    idea_id: int | None = None,
    dry_run: bool = False,
    reset_attempts: bool = False,
) -> ScriptResult:
    """Write scripts for ideas that have sources but no script yet."""
    settings = config.load_settings()
    llm_cfg = settings.get("llm", {})
    target_minutes = float(settings.get("video", {}).get("target_minutes", 18))
    target_chars = brand_mod.target_chars(target_minutes)
    brand = brand_mod.load_brand()
    max_retries = int(settings.get("pipeline", {}).get("max_retries_per_idea", 2))

    client = LLMClient(
        model=llm_cfg.get("script_model", "claude-sonnet-5"),
        max_tokens=int(llm_cfg.get("max_tokens", 16000)),
    )

    result = ScriptResult()
    with db.session() as conn:
        if idea_id is not None:
            if reset_attempts:
                # The retry cap stops the pipeline paying repeatedly for a
                # topic that will never validate. It should not also count
                # attempts that failed for reasons that have since been fixed.
                db.reset_attempts(conn, idea_id)
                conn.commit()
            row = db.get_idea(conn, idea_id)
            ideas = [row] if row else []
        else:
            ideas = db.ideas_by_status(conn, "researched", limit=limit)

        if not ideas:
            log.info("no ideas with status 'researched'")
            return result

        for idea in ideas:
            current_id = int(idea["id"])

            # Each attempt is a full paid call. Without this an idea whose
            # citations never validate is retried forever, and every retry buys
            # another script — the one place in this pipeline where a bad topic
            # can quietly spend the month's budget.
            if int(idea["attempts"]) >= max_retries:
                db.set_idea_status(conn, current_id, "failed")
                conn.commit()
                result.rejected += 1
                result.errors.append(
                    f"idea {current_id}: 台本生成が {idea['attempts']} 回失敗したので諦めます"
                    f"（上限 pipeline.max_retries_per_idea = {max_retries}）"
                )
                continue

            sources = [dict(s) for s in db.get_sources(conn, current_id)]
            if not sources:
                result.rejected += 1
                result.errors.append(f"idea {current_id}: no sources on record")
                continue

            known_refs = {s["ref"] for s in sources}
            theme = config.theme_by_id(idea["series_id"])
            plan = _chapter_plan(_research_plan(idea), target_chars)
            system = [_system_prompt(brand, theme, target_chars), _sources_block(idea, sources)]

            script, problems, spent = write_chapters(client, system, plan, known_refs, current_id,
                                                     rounds=max_retries, record=lambda r: _record_call(conn, r))
            result.llm_cost_usd += spent
            if script is None:
                result.rejected += 1
                result.errors.append(f"idea {current_id}: " + "; ".join(problems))
                db.bump_attempts(conn, current_id)
                conn.commit()
                continue
            for problem in problems:
                # accepted with degradations; say so, do not stop
                log.warning("idea %d: %s", current_id, problem)
                result.errors.append(f"idea {current_id} (accepted): {problem}")

            if dry_run:
                result.written += 1
                result.details.append(
                    {"idea_id": current_id, "chars": script.char_count, "hooks": script.hooks}
                )
                continue

            db.upsert_script(
                conn,
                idea_id=current_id,
                chapters=script.as_dicts(),
                hooks=script.hooks,
                char_count=script.char_count,
                model=client.model,
            )
            db.set_idea_status(conn, current_id, "scripted")
            preview = write_preview(script, current_id)
            if preview:
                log.info("idea %d: figure preview at %s", current_id, preview)
            db.reset_attempts(conn, current_id)
            conn.commit()

            result.written += 1
            result.details.append(
                {"idea_id": current_id, "chars": script.char_count, "hooks": script.hooks}
            )
            log.info(
                "idea %d scripted: %d chars (~%.1f min), %d chapters",
                current_id, script.char_count, script.char_count / 400, len(script.chapters),
            )

    return result


def write_preview(script: Script, idea_id: int) -> Path | None:
    """A contact sheet of the stage at four points in every chapter, so the
    figures can be judged before a minute of narration is synthesised."""
    from PIL import Image

    from .. import paths
    from ..canvas import AssetLibrary, SpriteSet

    try:
        brand = brand_mod.load_brand()
        sprites = SpriteSet(paths.SPRITES_DIR, {r: n.sprite for r, n in brand.navigators.items()})
        canvas = Canvas(assets=AssetLibrary(paths.ASSETS_DIR))
        frames = []
        for chapter in script.chapters:
            n = len(chapter.lines)
            marks = sorted({max(0, n - 1), n // 4, n // 2, (3 * n) // 4}) if n else []
            for i, line in enumerate(chapter.lines):
                for op in line.ops or [{"op": "hold"}]:
                    try:
                        canvas.apply(op)
                    except CanvasError:
                        break
                if i in marks:
                    img = canvas.render(subtitle=line.display, speaker=line.speaker, sprites=sprites)
                    frames.append(img.resize((480, 270), Image.LANCZOS))
        if not frames:
            return None
        cols = 4
        rows = (len(frames) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * 484 + 4, rows * 274 + 4), (40, 40, 40))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (4 + (i % cols) * 484, 4 + (i // cols) * 274))
        folder = paths.WORK_DIR / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"script_idea{idea_id:05d}_preview.png"
        sheet.save(path)
        return path
    except Exception as exc:  # noqa: BLE001 - a preview must never fail the script
        log.warning("preview failed: %s", exc)
        return None


# A drawn element that shows the same thing as a catalogue picture.
PICTURE_ALIASES = {
    "sun_icon": {"sun"}, "earth_icon": {"earth_globe", "earth_arc"}, "globe": {"earth_globe"},
    "moon_icon": {"moon"}, "full_moon": {"moon"}, "cloud_icon": {"cloud"}, "star_icon": {"star_dots"},
    "milky_way": {"galaxy", "star_dots"}, "telescope_icon": {"telescope"}, "ice_cube": {"ice_block"},
    "scale": {"balance"}, "wave_icon": {"wave"}, "ripple": {"wave"}, "scientist": {"person"},
    "hole": {"black_blob"},
}

# One-character catalogue words that are mostly suffixes of other words.
_GENERIC_SINGLE = {"目", "手", "本", "的", "家", "道", "穴", "塩", "岩", "街", "旗", "剣", "的"}


def mentioned_pictures(text: str) -> list[tuple[str, str]]:
    """Catalogue things named in the text, longest match first, as (日本語, name).

    Two-character words match as substrings; a one-character word only when
    it stands alone (「鐘」, not 重力波's 波, not 望遠鏡's 鏡).
    """
    from ..canvas.illustrations import ILLUSTRATIONS

    found: list[tuple[str, str]] = []
    covered: list[tuple[int, int]] = []
    by_len = sorted(((jp, name) for name, (_, jp) in ILLUSTRATIONS.items() if jp), key=lambda x: -len(x[0]))
    for jp, name in by_len:
        start = text.find(jp)
        while start != -1:
            end = start + len(jp)
            overlapped = any(s < end and start < e for s, e in covered)
            if not overlapped:
                if len(jp) >= 2:
                    ok = True
                else:
                    before = text[start - 1] if start > 0 else ""
                    after = text[end] if end < len(text) else ""
                    kanji = lambda ch: "一" <= ch <= "鿿"   # noqa: E731
                    ok = jp not in _GENERIC_SINGLE and not kanji(before) and not kanji(after)
                if ok:
                    covered.append((start, end))
                    if name not in {n for _, n in found}:
                        found.append((jp, name))
            start = text.find(jp, end)
    return found


def _library():
    from .. import paths
    from ..canvas import AssetLibrary

    return AssetLibrary(paths.ASSETS_DIR)


def _record_call(conn, response) -> None:
    if response.cost_usd:
        db.insert_llm_call(
            conn, purpose="script", model=response.model,
            input_tokens=response.input_tokens + response.cache_read_tokens + response.cache_write_tokens,
            output_tokens=response.output_tokens, cost_usd=response.cost_usd,
        )
        conn.commit()


def write_chapters(client, system: list[str], plan: list[dict[str, Any]], known_refs: set[str],
                   idea_id: int, rounds: int = 2, record=None) -> tuple[Script | None, list[str], float]:
    """Write the script one chapter at a time, on a persistent stage.

    Each chapter gets `rounds` attempts with its problems fed back. A chapter
    still wrong after that is accepted with its broken visual lines dropped
    to `hold` — a figure fewer, not a video fewer — unless it has no lines at
    all, which fails the script. Returns the script (or None), the notes
    (degradations, or the fatal problem), and what it cost.
    """
    canvas = Canvas(assets=_library())
    chapters: list[Chapter] = []
    hooks: list[str] = []
    notes: list[str] = []
    spent = 0.0

    for index, brief in enumerate(plan):
        chapter, problems, cost = write_chapter(client, system, plan, index, chapters, canvas, known_refs,
                                                idea_id, rounds)
        spent += cost
        if record:
            for response in _last_responses:
                record(response)
        if chapter is None:
            return None, problems, spent
        if problems:
            # out of rounds: keep the best attempt, drop what does not render
            dropped = 0
            for line in chapter.lines:
                if getattr(line, "broken", False):
                    line.ops = [{"op": "hold"}]
                    line.visual = ["hold"]
                    line.dropped = True
                    dropped += 1
            for line in chapter.lines:
                for op in line.ops:
                    try:
                        canvas.apply(op)
                    except CanvasError:
                        line.ops = [{"op": "hold"}]
                        break
            notes.append(f"{brief['key']}: {len(problems)} 件を残して採用（板書を落とした行 {dropped}）: " + "; ".join(problems[:3]))
        else:
            for line in chapter.lines:
                for op in line.ops:
                    canvas.apply(op)
        chapters.append(chapter)
        if index == 0:
            hooks = [h for h in chapter.hooks if h.strip()][:3]

    script = Script(chapters=chapters, hooks=hooks)
    lines = [line for c in chapters for line in c.lines]
    share = sum(1 for line in lines if line.speaker == "listener") / max(len(lines), 1)
    if not (LISTENER_SHARE[0] <= share <= LISTENER_SHARE[1]):
        notes.append(f"listener の発話が{share:.0%}（目安 {LISTENER_SHARE[0]:.0%}〜{LISTENER_SHARE[1]:.0%}）")
    if len(hooks) < 3:
        hooks += [chapters[0].lines[0].display] * (3 - len(hooks))
        script.hooks = hooks
    return script, notes, spent


_last_responses: list = []


def write_chapter(client, system: list[str], plan: list[dict[str, Any]], index: int, previous: list[Chapter],
                  canvas: Canvas, known_refs: set[str], idea_id: int, rounds: int = 2
                  ) -> tuple[Chapter | None, list[str], float]:
    """One chapter, up to `rounds` attempts with feedback, best attempt kept.

    The best attempt is the one with the fewest problems among those that
    are not stubs (a model that has been corrected twice sometimes answers
    with one placeholder line; that is never the one to keep). Returns the
    chapter, its remaining problems (empty when clean), and the cost. The
    canvas is not modified; the caller applies the chosen chapter.
    """
    brief = plan[index]
    wanted = int(brief.get("lines", 10))
    stage_items = [i.name for i in canvas.state.items if i.kind == "element"]
    feedback = ""
    candidates: list[tuple[int, int, Chapter, list[str]]] = []
    spent = 0.0
    _last_responses.clear()
    extra = 0
    round_no = 0
    while round_no < rounds + extra:
        user = _chapter_prompt(plan, index, previous, stage_items, feedback)
        try:
            response = client.call_tool(system=system, user=user, tool=CHAPTER_TOOL, max_tokens=12000)
        except Exception as exc:  # noqa: BLE001 - reported, the run moves on
            return None, [f"{brief['key']}: {exc}"], spent
        spent += response.cost_usd
        _last_responses.append(response)
        chapter = _parse_chapter(response.payload, brief)
        problems = check_chapter(chapter, brief, copy.deepcopy(canvas), known_refs)
        if not problems:
            return chapter, [], spent
        _dump_rejected(idea_id, round_no, response, problems, suffix=brief["key"])
        log.warning("idea %d %s round %d: %s", idea_id, brief["key"], round_no + 1, "; ".join(problems[:4]))
        stub = len(chapter.lines) < max(2, wanted * 0.4)
        if stub and extra == 0:
            extra = 1                      # one more go; a stub is not an answer
            feedback = _feedback(problems) + "\n（前回は台詞がほとんど無かった。章全体を書くこと）"
        else:
            feedback = _feedback(problems)
        if not stub:
            candidates.append((len(problems), -len(chapter.lines), chapter, problems))
        round_no += 1
    if not candidates:
        return None, [f"{brief['key']}: 台詞が書けなかった"], spent
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, best, problems = candidates[0]
    # re-check the best against the real stage so its lines carry `broken`
    check_chapter(best, brief, copy.deepcopy(canvas), known_refs)
    return best, problems, spent


def rewrite_chapter(idea_id: int, key: str, rounds: int | None = None) -> tuple[Script, list[str], float]:
    """Regenerate one chapter of a stored script and store the result.

    For the case the first run showed: eight chapters fine, one a stub.
    Rewriting the whole script for that costs nine calls; this costs one
    to three. Earlier chapters are replayed on the stage so references
    resolve the same way they did the first time.
    """
    settings = config.load_settings()
    llm_cfg = settings.get("llm", {})
    target_chars = brand_mod.target_chars(float(settings.get("video", {}).get("target_minutes", 15)))
    brand = brand_mod.load_brand()
    rounds = rounds or int(settings.get("pipeline", {}).get("max_retries_per_idea", 2))
    client = LLMClient(model=llm_cfg.get("script_model", "claude-sonnet-5"),
                       max_tokens=int(llm_cfg.get("max_tokens", 16000)))

    with db.session() as conn:
        idea = db.get_idea(conn, idea_id)
        row = db.get_script(conn, idea_id)
        if idea is None or row is None:
            raise ValueError(f"idea {idea_id} has no script to rewrite")
        chapters = [Chapter.from_dict(c) for c in json.loads(row["chapters_json"])]
        hooks = json.loads(row["hooks_json"] or "[]")
        sources = [dict(s) for s in db.get_sources(conn, idea_id)]
        known_refs = {s["ref"] for s in sources}
        theme = config.theme_by_id(idea["series_id"])
        plan = _chapter_plan(_research_plan(idea), target_chars)
        keys = [c["key"] for c in plan]
        if key not in keys:
            raise ValueError(f"no chapter {key!r}; one of {keys}")
        index = keys.index(key)
        system = [_system_prompt(brand, theme, target_chars), _sources_block(idea, sources)]

        canvas = Canvas(assets=_library())
        for chapter in chapters[:index]:
            for line in chapter.lines:
                for op in line.ops or []:
                    try:
                        canvas.apply(op)
                    except CanvasError:
                        break
        chapter, problems, spent = write_chapter(client, system, plan, index, chapters[:index], canvas,
                                                 known_refs, idea_id, rounds)
        for response in _last_responses:
            _record_call(conn, response)
        if chapter is None:
            raise RuntimeError("; ".join(problems))
        for line in chapter.lines:
            if getattr(line, "broken", False):
                line.ops, line.visual, line.dropped = [{"op": "hold"}], ["hold"], True
        if index < len(chapters):
            chapters[index] = chapter
        else:
            chapters.append(chapter)
        if index == 0 and chapter.hooks:
            hooks = [h for h in chapter.hooks if h.strip()][:3] or hooks
        script = Script(chapters=chapters, hooks=hooks)
        db.upsert_script(conn, idea_id=idea_id, chapters=script.as_dicts(), hooks=script.hooks,
                         char_count=script.char_count, model=client.model)
        # the old narration no longer matches the script
        conn.execute("DELETE FROM narrations WHERE idea_id = ?", (idea_id,))
        db.set_idea_status(conn, idea_id, "scripted")
        conn.commit()
        write_preview(script, idea_id)
    return script, problems, spent


def _parse_chapter(payload: dict[str, Any], brief: dict[str, Any]) -> Chapter:
    chapter = Chapter(
        key=brief["key"],
        title=str(payload.get("title") or brief["title"]).strip(),
        visual_intent=str(payload.get("visual_intent", "")).strip(),
        lines=[
            Line(
                speaker=line.get("speaker", "explainer"),
                display=str(line.get("display", "")).strip(),
                spoken=str(line.get("spoken", "")).strip(),
                refs=[r.strip() for r in line.get("refs", []) if r.strip()],
                visual=[v.strip() for v in line.get("visual", []) if v.strip()],
                expression=line.get("expression", "normal") or "normal",
            )
            for line in payload.get("lines", []) if line.get("display")
        ],
    )
    chapter.hooks = [str(h) for h in payload.get("hooks", [])]
    return chapter


def _feedback(problems: list[str]) -> str:
    return (
        "\n\n前回の提出は次の理由で差し戻されました。全部直して、台本全体をもう一度提出してください:\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n（visual の書式は `op key=value`。例: `background name=space` / `label text=氷期 at=tl.cold side=above`。"
        "要素の部位は要素ごとに違うが、どの要素にも .top .bottom .left .right .centre はある）"
    )


def _dump_rejected(idea_id: int, round_no: int, response, problems: list[str], suffix: str = ""):
    """Keep the raw payload of a rejected script, so a rejection can be read
    rather than guessed at. Cheap insurance: the call already cost money."""
    from .. import paths

    folder = paths.WORK_DIR / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    tag = f"_{suffix}" if suffix else ""
    path = folder / f"script_rejected_idea{idea_id:05d}{tag}_round{round_no + 1}.json"
    path.write_text(json.dumps({
        "problems": problems,
        "stop_reason": response.stop_reason,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "raw_text": response.raw_text,
        "payload": response.payload,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _research_plan(idea) -> list[dict[str, Any]] | None:
    """What research decided each chapter should cover."""
    try:
        return json.loads(idea["plan_json"] or "[]") or None
    except (TypeError, ValueError, IndexError):
        return None
