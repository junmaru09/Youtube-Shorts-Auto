"""Stage 1: decide what today's video is about, and where its facts come from.

The model does not invent a topic. It reads what NASA published and what was
posted to arXiv, then picks a cluster of 3-5 related items that add up to one
explainable idea. Those items become the citation list, and nothing downstream
may state a number that does not trace back to one of them.

Bundling several sources is deliberate. A 20-minute video built on a single
press release is thin, and thin is what the inauthentic-content policy targets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import config, db, dedup, feeds, scoring
from ..llm import LLMClient
from ..models import Arm, Source, ThemeConfig

log = logging.getLogger(__name__)

TOPIC_TOOL = {
    "name": "submit_topic",
    "description": "Submit the chosen topic for one video, with its sources.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "日本語の動画タイトル。40文字以内。シリーズのタイトルパターンに沿うこと。誇張や断定は避ける。",
            },
            "angle": {
                "type": "string",
                "description": "One English sentence naming the specific question this video answers. Compared against past videos for novelty, so be specific.",
            },
            "why_now": {
                "type": "string",
                "description": "日本語で1文。なぜ今この話なのか（どの観測・論文が出たから）。",
            },
            "source_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The 1-based indices of the feed items this video is built on. Choose 3 to 5 that genuinely relate to each other.",
            },
            "chapter_plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "title": {"type": "string", "description": "日本語の章タイトル。15文字以内。"},
                        "covers": {"type": "string", "description": "日本語で1文。この章で扱う内容。"},
                        "visual_intent": {
                            "type": "string",
                            "description": "English search terms for the footage that should accompany this chapter.",
                        },
                    },
                    "required": ["key", "title", "covers", "visual_intent"],
                },
            },
        },
            "history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "who": {"type": "string", "description": "人物名または研究チーム。日本語表記。"},
                        "year": {"type": "integer", "description": "西暦。"},
                        "what": {"type": "string", "description": "日本語で1文。何に気づいたか、何を発表したか。"},
                    },
                    "required": ["who", "year", "what"],
                },
                "description": (
                    "発見史の材料。この話に至るまでに誰が・いつ・何に気づいたか、3〜6件を古い順に。"
                    "教科書や百科事典にある一般知識の範囲に限り、確信のないものは入れない。"
                ),
            },
        },
        "required": ["title", "angle", "why_now", "source_indices", "chapter_plan", "history"],
    },
}


@dataclass(slots=True)
class ResearchResult:
    created: int = 0
    duplicates: int = 0
    failed: int = 0
    llm_cost_usd: float = 0.0
    topics: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _system_prompt(theme: ThemeConfig, brand) -> str:
    return f"""\
あなたは日本語の科学解説チャンネル「{brand.channel_name}」の企画担当です。
{brand.tagline}

このチャンネルの約束:
- 事実は必ず一次ソース（NASAの発表、査読前論文）に基づく。推測は推測と明示する。
- 毎回、最終章で「{brand.signature_question}」に答える。これがこのチャンネルの視点です。
- 分かっていないことは「分かっていない」と言う。断定して埋めない。

守ること:
- 与えられたフィード項目**だけ**から題材を選ぶ。項目にない話題は選ばない。
- 3〜5件の項目を束ねて、ひとつの問いに答える構成にする。1件だけでは20分の中身にならない。
- 束ねる項目どうしが実際に関係していること。無関係なものを並べない。
- 過去の企画と、扱う問いが重ならないこと。
- タイトルで断定しない。「〜が判明」より「〜はどこまで分かったのか」。
- history には、この話の「発見の物語」を書くための人物と年を入れる。台本はここから
  「誰が・いつ・何に気づいたか」の章を作る。確信のない人名や年は入れない。

このテーマで禁止されていること:
{chr(10).join('- ' + b for b in theme.banned)}"""


def _user_prompt(
    theme: ThemeConfig, items: list[feeds.FeedItem], recent: list[str], plan
) -> str:
    parts = [
        f"テーマ: {theme.id} — {theme.description}",
        f"想定視聴者: {theme.audience}",
        "",
        "タイトルパターン（参考。そのまま使わなくてよい）:",
    ]
    parts += [f"  {p}" for p in theme.title_patterns.get("ja", [])]

    parts += ["", "章立ては次の型に従うこと（key は変えない）:"]
    parts += [f"  {c.key}: {c.title} — {c.intent}" for c in plan]

    parts += ["", f"今日選べるフィード項目（{len(items)}件）:"]
    for index, item in enumerate(items, start=1):
        age = item.age_days
        when = f"{age:.0f}日前" if age is not None else "日付不明"
        parts.append(f"[{index}] ({item.kind}, {when}) {item.title}")
        if item.summary:
            parts.append(f"     {item.summary[:280]}")

    if recent:
        parts += ["", f"過去に扱った問い（{len(recent)}件）。重ならないこと:"]
        parts += [f"  - {angle}" for angle in recent]

    parts += ["", "1本ぶんの企画を submit_topic で提出してください。"]
    return "\n".join(parts)


def run(
    count: int | None = None,
    theme_id: str | None = None,
    dry_run: bool = False,
) -> ResearchResult:
    """Plan `count` videos, each with its own bundle of primary sources."""
    from ..brand import EPISODE_PLAN, load_brand

    settings = config.load_settings()
    research_cfg = settings.get("research", {})
    llm_cfg = settings.get("llm", {})
    count = count if count is not None else int(settings["pipeline"]["ideas_per_day"])
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    brand = load_brand()
    client = LLMClient(
        model=llm_cfg.get("research_model", "claude-sonnet-5"),
        max_tokens=int(llm_cfg.get("max_tokens", 16000)),
    )

    if theme_id:
        theme = config.theme_by_id(theme_id)
        picks = [Arm(series_id=theme_id, lang=theme.languages[0]).key] * count
    else:
        picks = scoring.sample_arms(count)
        log.info("allocation: %s", {k: f"{v:.0%}" for k, v in sorted(scoring.allocation().items())})

    result = ResearchResult()
    with db.session() as conn:
        seen_urls = db.known_source_urls(conn, int(research_cfg.get("recent_source_memory", 500)))

        for arm_key in picks:
            arm = Arm.parse(arm_key)
            theme = config.theme_by_id(arm.series_id)
            recent = db.recent_script_summaries(
                conn, arm.series_id, int(llm_cfg.get("recent_context_size", 40))
            )

            items = feeds.gather(
                theme.nasa_queries,
                theme.arxiv_categories,
                exclude_urls=seen_urls,
                nasa_max_age_days=int(research_cfg.get("nasa_max_age_days", 365)),
                arxiv_max_age_days=int(research_cfg.get("arxiv_max_age_days", 30)),
            )
            wanted = research_cfg.get("sources_per_video", {})
            minimum = int(wanted.get("min", 3))
            if len(items) < minimum:
                result.failed += 1
                result.errors.append(
                    f"{arm_key}: only {len(items)} unused feed items, need at least {minimum}. "
                    "Widen the theme's nasa_queries or arxiv_categories, or raise "
                    "research.arxiv_max_age_days."
                )
                continue

            try:
                response = client.call_tool(
                    system=_system_prompt(theme, brand),
                    user=_user_prompt(theme, items, recent, EPISODE_PLAN),
                    tool=TOPIC_TOOL,
                )
            except Exception as exc:  # noqa: BLE001 - one arm must not kill the run
                log.error("research failed for %s: %s", arm_key, exc)
                result.failed += 1
                result.errors.append(f"{arm_key}: {exc}")
                continue

            if response.cost_usd:
                db.insert_llm_call(
                    conn,
                    purpose="research",
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                )
                conn.commit()
                result.llm_cost_usd += response.cost_usd

            plan = response.payload
            chosen = _resolve_sources(plan.get("source_indices", []), items, wanted, result, arm_key)
            if chosen is None:
                continue
            chosen += _history_sources(plan.get("history", []), start=len(chosen) + 1)

            record = _store(conn, theme, arm, plan, chosen, dry_run, result)
            if record is not None:
                seen_urls.update(s.url for s in chosen)
                recent.append(plan["angle"])

    return result


def _history_sources(history: list[dict[str, Any]], start: int) -> list[Source]:
    """Discovery-history entries as citable refs.

    The script's citation check demands a ref on every year, and rightly so.
    History the model supplies is textbook knowledge, not a primary source, so
    it is stored with kind "background" — listed apart from the primary
    sources in the description, but citable, so "1964年、ハーランドは" can
    carry [S6] instead of being rejected.
    """
    sources = []
    for i, entry in enumerate(history[:6], start=start):
        who = str(entry.get("who", "")).strip()
        what = str(entry.get("what", "")).strip()
        year = entry.get("year")
        if not who or not what or not year:
            continue
        sources.append(Source(
            ref=f"S{i}", kind="background", url="", title=f"{year}年 {who}",
            published_at=f"{int(year):04d}-01-01", summary=what,
        ))
    return sources


def _resolve_sources(
    indices: list[int],
    items: list[feeds.FeedItem],
    wanted: dict[str, Any],
    result: ResearchResult,
    arm_key: str,
) -> list[Source] | None:
    """Turn the model's picks into Source rows, refusing an unusable bundle."""
    minimum = int(wanted.get("min", 3))
    maximum = int(wanted.get("max", 5))

    picked: list[feeds.FeedItem] = []
    for raw in indices:
        index = int(raw) - 1
        if 0 <= index < len(items) and items[index] not in picked:
            picked.append(items[index])

    if len(picked) < minimum:
        result.failed += 1
        result.errors.append(
            f"{arm_key}: model chose {len(picked)} valid sources, need {minimum}. "
            "A video on one source is too thin to fill 20 minutes."
        )
        return None

    return [
        Source(
            ref=f"S{i}",
            kind=item.kind,
            url=item.url,
            title=item.title,
            published_at=item.published_at,
            summary=item.summary,
        )
        for i, item in enumerate(picked[:maximum], start=1)
    ]


def _store(
    conn,
    theme: ThemeConfig,
    arm: Arm,
    plan: dict[str, Any],
    sources: list[Source],
    dry_run: bool,
    result: ResearchResult,
) -> dict[str, Any] | None:
    angle = plan["angle"].strip()
    title = plan["title"].strip()

    too_similar, collided = dedup.is_too_similar(
        angle, db.recent_script_summaries(conn, theme.id, 60)
    )
    if too_similar:
        log.info("dropping near-duplicate angle in %s: %r ~ %r", theme.id, angle, collided)
        result.duplicates += 1
        return None

    record = {
        "series_id": theme.id,
        "lang": arm.lang,
        "hook": title,
        "scene_summary": angle,
        "why_now": plan.get("why_now", "").strip(),
        "plan": plan.get("chapter_plan", []),
        "tags": list(theme.hashtags.get(arm.lang, [])),
        "dedup_key": dedup.make_key(theme.id, angle),
    }

    if dry_run:
        result.topics.append({**record, "sources": [s.as_dict() for s in sources]})
        result.created += 1
        return record

    idea_id = db.insert_idea(conn, **record)
    if idea_id is None:
        log.info("dropping exact duplicate in %s: %r", theme.id, angle)
        result.duplicates += 1
        return None

    db.replace_sources(conn, idea_id, [s.as_dict() for s in sources])
    db.set_idea_status(conn, idea_id, "researched")
    conn.commit()

    result.topics.append({**record, "id": idea_id, "sources": [s.as_dict() for s in sources]})
    result.created += 1
    log.info("idea %d planned: %s (%d sources)", idea_id, title, len(sources))
    return record
