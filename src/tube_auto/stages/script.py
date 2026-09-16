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

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import brand as brand_mod
from .. import citations, config, db, reading
from ..llm import LLMClient
from ..models import Chapter, Line, Script

log = logging.getLogger(__name__)

# How far off the character target a script may land before it is rejected.
LENGTH_TOLERANCE = 0.25

# One or two sentences of narration. The length target is expressed to the
# model as lines-per-chapter at this size, because a character total is not a
# thing it can hit while writing.
CHARS_PER_LINE = 60

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
                            "description": "English search terms for footage matching this chapter.",
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
                                },
                                "required": ["speaker", "display", "spoken", "refs"],
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

話し方の設計:
- 基本は explainer が語る。listener は章の変わり目と、視聴者がつまずくところで
  **1〜2文だけ**質問する。相槌や合いの手は入れない。
- 掛け合いを続けない。listener が2回続けて話すことはない。
- 1行は1〜2文。長い段落にしない。

このチャンネルの視点:
- 最終章では必ず「{brand.signature_question}」に答える。これが他と違う点です。
- 分かっていないことは「分かっていない」と言う。断定で埋めない。
- 最後は「{brand.closing_line}」で締める。

{citations.CITATION_RULES}

{reading.SPOKEN_TEXT_RULES}

分量:
- 各章に指定された**行数**を満たす。1行は1〜2文、{CHARS_PER_LINE}文字前後。
- 行数を満たせば spoken の合計は約{target_chars}文字（{target_chars // 400}分）になる。
  日本語のナレーションは1分あたり約400文字です。
- 短く終わらせない。指定行数に届くまで、出典の中身を具体的に展開する。

このテーマで禁止されていること:
{chr(10).join('- ' + b for b in theme.banned)}"""


def _user_prompt(idea, sources, plan: list[dict[str, Any]], target_chars: int) -> str:
    parts = [
        f"タイトル: {idea['hook']}",
        f"この動画が答える問い: {idea['scene_summary']}",
        "",
        "使える一次ソース（これ以外の事実を書かないこと）:",
    ]
    for source in sources:
        when = (source["published_at"] or "")[:10]
        parts.append(f"[{source['ref']}] ({source['kind']}, {when}) {source['title']}")
        parts.append(f"      {source['url']}")
        if source["summary"]:
            parts.append(f"      {source['summary'][:600]}")

    total_lines = sum(c["lines"] for c in plan)
    parts += ["", f"章構成（key はこのまま使うこと。各章の行数は必ず満たすこと。合計 {total_lines} 行）:"]
    for chapter in plan:
        parts.append(
            f"  {chapter['key']}: {chapter['title']} — {chapter['covers']}"
            f"（{chapter['lines']}行・約{chapter['chars']}文字）"
        )

    parts += [
        "",
        f"1行は1〜2文、{CHARS_PER_LINE}文字前後。各章の行数を満たすと spoken の合計が約{target_chars}文字になります。",
        "行数が足りない台本は自動で差し戻され、書き直しの費用がかかります。",
        "hook_1〜hook_3 の3案はそれぞれ違う切り口で。submit_script で提出してください。",
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
            title=chapter["title"],
            visual_intent=chapter.get("visual_intent", ""),
            lines=[
                Line(
                    speaker=line["speaker"],
                    display=line["display"].strip(),
                    spoken=line["spoken"].strip(),
                    refs=[r.strip() for r in line.get("refs", []) if r.strip()],
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


def validate(script: Script, known_refs: set[str], target_chars: int) -> list[str]:
    """Everything wrong with this script, in the order it would hurt."""
    problems: list[str] = []

    if not script.chapters:
        return ["台本に章がひとつもない"]

    report = citations.check([c.as_dict() for c in script.chapters], known_refs)
    if not report.ok:
        problems.append(report.describe())

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
    if abs(actual - target_chars) > target_chars * LENGTH_TOLERANCE:
        minutes = actual / 400
        problems.append(
            f"分量が目標から外れている: {actual}文字（目標{target_chars}文字、約{minutes:.1f}分）"
        )

    if len(script.hooks) < 3:
        problems.append(f"冒頭案が{len(script.hooks)}件しかない（3件必要）")

    # The listener exists to break up the monologue; a script where they never
    # speak is the monotone that drives 20-minute retention down.
    listener_lines = sum(
        1 for c in script.chapters for line in c.lines if line.speaker == "listener"
    )
    if listener_lines == 0:
        problems.append("listener の発話がゼロ。単調になるので章の変わり目に入れること")

    return problems


def run(limit: int = 1, idea_id: int | None = None, dry_run: bool = False) -> ScriptResult:
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

            try:
                response = client.call_tool(
                    system=_system_prompt(brand, theme, target_chars),
                    user=_user_prompt(idea, sources, plan, target_chars),
                    tool=SCRIPT_TOOL,
                )
            except Exception as exc:  # noqa: BLE001 - one idea must not kill the run
                log.error("script generation failed for idea %d: %s", current_id, exc)
                result.rejected += 1
                result.errors.append(f"idea {current_id}: {exc}")
                continue

            if response.cost_usd:
                db.insert_llm_call(
                    conn,
                    purpose="script",
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                )
                conn.commit()
                result.llm_cost_usd += response.cost_usd

            script = _parse(response.payload)
            problems = validate(script, known_refs, target_chars)
            if problems:
                log.error("idea %d rejected: %s", current_id, "; ".join(problems))
                result.rejected += 1
                result.errors.append(f"idea {current_id}: " + "; ".join(problems))
                db.bump_attempts(conn, current_id)
                conn.commit()
                continue

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
                model=response.model,
            )
            db.set_idea_status(conn, current_id, "scripted")
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


def _research_plan(idea) -> list[dict[str, Any]] | None:
    """What research decided each chapter should cover."""
    try:
        return json.loads(idea["plan_json"] or "[]") or None
    except (TypeError, ValueError, IndexError):
        return None
