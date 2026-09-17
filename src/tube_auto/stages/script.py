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
CHARS_PER_LINE = 40

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
- 1行は1〜2文、字幕は30字×2行に収まる長さ（display は60字以内）。最後の行は「{brand.closing_line}」。

冒頭（opener）の型。何も知らない人が「見てみよう」と思う入り方にする:
- 本題の名前も、論文も、数字も、専門用語も出さない。listener の身近な体験から始める。
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
LISTENER_SHARE = (0.15, 0.45)
# Lines in a row that leave the stage untouched before it counts as static.
# The room chapters are conversation; the reference holds its room for
# thirty seconds at a time there and nowhere else.
MAX_HOLD_RUN = 3
MAX_HOLD_RUN_ROOM = 5
ROOM_CHAPTERS = {"opener", "close"}
# A subtitle is two rows of SUBTITLE_WRAP characters. Longer lines overflow
# the band, and a line that long is a paragraph anyway.
MAX_DISPLAY_CHARS = 60


def check_visuals(script: Script) -> list[str]:
    """Parse every line's visual DSL and dry-run it on a canvas.

    Populates `line.ops` as a side effect, so the stored script carries the
    parsed operations. A broken reference ("arrow from=ice3" when ice3 was
    never placed) is caught here, before narration is paid for, with the
    chapter and line it sits on.
    """
    problems: list[str] = []
    canvas = Canvas()
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
        problems.append(f"{len(too_long)} 行の display が{MAX_DISPLAY_CHARS}字を超えている（字幕2行に収まらない）: "
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
            system = _system_prompt(brand, theme, target_chars)
            user = _user_prompt(idea, sources, plan, target_chars)

            # A rejected script is sent back with its problems, up to the
            # retry cap, in this same run. Most rejections are one fixable
            # thing — a broken visual reference, a chapter that forgot to end
            # on a question — and the model fixes them when told; without the
            # feedback it just writes another script with different mistakes.
            script = None
            problems: list[str] = []
            feedback = ""
            for round_no in range(max_retries - int(idea["attempts"])):
                try:
                    response = client.call_tool(system=system, user=user + feedback, tool=SCRIPT_TOOL)
                except Exception as exc:  # noqa: BLE001 - one idea must not kill the run
                    log.error("script generation failed for idea %d: %s", current_id, exc)
                    problems = [str(exc)]
                    break

                if response.cost_usd:
                    db.insert_llm_call(
                        conn, purpose="script", model=response.model,
                        input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                        cost_usd=response.cost_usd,
                    )
                    conn.commit()
                    result.llm_cost_usd += response.cost_usd

                script = _parse(response.payload)
                problems = validate(script, known_refs, target_chars, plan)
                if not problems:
                    break
                dump = _dump_rejected(current_id, round_no, response, problems)
                log.error("idea %d rejected (round %d, %d output tokens, stop=%s): %s — raw payload in %s",
                          current_id, round_no + 1, response.output_tokens, response.stop_reason,
                          "; ".join(problems), dump)
                db.bump_attempts(conn, current_id)
                conn.commit()
                feedback = _feedback(problems)

            if problems or script is None:
                result.rejected += 1
                result.errors.append(f"idea {current_id}: " + "; ".join(problems))
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


def _feedback(problems: list[str]) -> str:
    return (
        "\n\n前回の提出は次の理由で差し戻されました。全部直して、台本全体をもう一度提出してください:\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n（visual の書式は `op key=value`。例: `background name=space` / `label text=氷期 at=tl.cold side=above`。"
        "要素の部位は要素ごとに違うが、どの要素にも .top .bottom .left .right .centre はある）"
    )


def _dump_rejected(idea_id: int, round_no: int, response, problems: list[str]):
    """Keep the raw payload of a rejected script, so a rejection can be read
    rather than guessed at. Cheap insurance: the call already cost money."""
    from .. import paths

    folder = paths.WORK_DIR / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"script_rejected_idea{idea_id:05d}_round{round_no + 1}.json"
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
