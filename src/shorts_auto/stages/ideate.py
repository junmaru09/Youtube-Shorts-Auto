"""Stage 1: plan videos.

Series are sampled by the current A/B allocation, then the model is asked for
ideas with the recent history and the reviewer's rejections in context. Every
candidate passes the novelty gate before it reaches the database.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .. import config, db, dedup, scoring
from ..llm import IdeaGenerator
from ..models import SeriesConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IdeateResult:
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    ideas: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _hashtags(series: SeriesConfig) -> dict[str, list[str]]:
    return {lang: list(tags) for lang, tags in series.hashtags.items()}


def _describe_rejection(row) -> str:
    hook = json.loads(row["hook_json"]).get("ja", "")
    reason = row["reason_tag"] or "理由なし"
    note = f" ({row['note']})" if row["note"] else ""
    return f"{row['scene_summary']} [{hook}] -> rejected: {reason}{note}"


def run(
    count: int | None = None,
    dry_run: bool = False,
    series_id: str | None = None,
) -> IdeateResult:
    settings = config.load_settings()
    llm_cfg = settings.get("llm", {})
    count = count or int(settings.get("pipeline", {}).get("ideas_per_day", 3))

    if series_id:
        picks = [series_id] * count
    else:
        picks = scoring.sample_series(count)
        log.info("allocation: %s", {k: f"{v:.0%}" for k, v in scoring.allocation().items()})

    per_series: dict[str, int] = defaultdict(int)
    for pick in picks:
        per_series[pick] += 1

    generator = IdeaGenerator(
        model=llm_cfg.get("model", "claude-sonnet-5"),
        max_tokens=int(llm_cfg.get("max_tokens", 4096)),
    )
    recent_size = int(llm_cfg.get("recent_context_size", 40))

    result = IdeateResult()
    with db.session() as conn:
        for sid, wanted in per_series.items():
            series = config.series_by_id(sid)
            recent_rows = db.recent_ideas(conn, sid, recent_size)
            recent_scenes = [row["scene_summary"] for row in recent_rows if row["scene_summary"]]
            rejections = [_describe_rejection(row) for row in db.recent_rejections(conn, sid, 10)]

            try:
                candidates = generator.generate(series, wanted, recent_scenes, rejections)
            except Exception as exc:  # noqa: BLE001 - one series must not kill the run
                log.error("ideation failed for %s: %s", sid, exc)
                result.failed += wanted
                result.errors.append(f"{sid}: {exc}")
                continue

            for candidate in candidates:
                accepted = _accept(
                    conn,
                    series,
                    candidate,
                    recent_scenes,
                    dry_run=dry_run,
                    result=result,
                )
                if accepted:
                    recent_scenes.append(candidate["scene_summary"])
    return result


def _accept(
    conn,
    series: SeriesConfig,
    candidate: dict[str, Any],
    recent_scenes: list[str],
    *,
    dry_run: bool,
    result: IdeateResult,
) -> bool:
    scene_summary = candidate.get("scene_summary", "").strip()
    scene = candidate.get("video_prompt_scene", "").strip()
    if not scene_summary or not scene:
        result.failed += 1
        result.errors.append(f"{series.id}: model returned an incomplete idea")
        return False

    too_similar, collided = dedup.is_too_similar(scene_summary, recent_scenes)
    if too_similar:
        log.info("dropping near-duplicate in %s: %r ~ %r", series.id, scene_summary, collided)
        result.duplicates += 1
        return False

    hook = {
        "ja": candidate.get("hook_ja", "").strip(),
        "en": candidate.get("hook_en", "").strip(),
    }
    record = {
        "series_id": series.id,
        "hook": hook,
        "video_prompt": series.prompt_template.replace("{scene}", scene),
        "scene_summary": scene_summary,
        "tags": _hashtags(series),
        "dedup_key": dedup.make_key(series.id, scene_summary),
    }

    if dry_run:
        result.ideas.append(record)
        result.inserted += 1
        return True

    idea_id = db.insert_idea(conn, **record)
    if idea_id is None:
        log.info("dropping exact duplicate in %s: %r", series.id, scene_summary)
        result.duplicates += 1
        return False

    result.ideas.append({**record, "id": idea_id})
    result.inserted += 1
    return True
