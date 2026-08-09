"""Stage 1: plan videos.

Arms are sampled by how far each is below its target share, then the model is
asked for ideas with the recent history and the reviewer's rejections in context.
Every candidate passes the novelty gate before it reaches the database.

One idea targets one channel. Sending the same footage to two channels is reused
content, and that penalty applies channel-wide.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .. import config, db, dedup, scoring
from ..llm import IdeaGenerator, build_video_prompt
from ..models import Arm, SeriesConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IdeateResult:
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    llm_cost_usd: float = 0.0
    ideas: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _describe_rejection(row) -> str:
    reason = row["reason_tag"] or "理由なし"
    note = f" ({row['note']})" if row["note"] else ""
    return f"{row['scene_summary']} [{row['hook']}] -> rejected: {reason}{note}"


def run(
    count: int | None = None,
    dry_run: bool = False,
    series_id: str | None = None,
    lang: str | None = None,
) -> IdeateResult:
    settings = config.load_settings()
    llm_cfg = settings.get("llm", {})
    count = count if count is not None else int(
        settings.get("pipeline", {}).get("ideas_per_day", 3)
    )
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    if series_id:
        series = config.series_by_id(series_id)
        target_lang = lang or series.languages[0]
        if target_lang not in series.languages:
            raise config.ConfigError(
                f"series '{series_id}' does not target language '{target_lang}' "
                f"(it targets {series.languages})"
            )
        picks = [Arm(series_id=series_id, lang=target_lang).key] * count
    else:
        picks = scoring.sample_arms(count)
        shares = scoring.allocation()
        log.info("allocation: %s", {k: f"{v:.0%}" for k, v in sorted(shares.items())})

    per_arm: dict[str, int] = defaultdict(int)
    for pick in picks:
        per_arm[pick] += 1

    generator = IdeaGenerator(
        model=llm_cfg.get("model", "claude-sonnet-5"),
        max_tokens=int(llm_cfg.get("max_tokens", 4096)),
    )
    recent_size = int(llm_cfg.get("recent_context_size", 40))

    result = IdeateResult()
    with db.session() as conn:
        for arm_key, wanted in sorted(per_arm.items()):
            arm = Arm.parse(arm_key)
            series = config.series_by_id(arm.series_id)
            recent_rows = db.recent_ideas(conn, arm.series_id, recent_size)
            recent_scenes = [row["scene_summary"] for row in recent_rows if row["scene_summary"]]
            rejections = [
                _describe_rejection(row) for row in db.recent_rejections(conn, arm.series_id, 10)
            ]

            try:
                batch = generator.generate(series, arm.lang, wanted, recent_scenes, rejections)
            except Exception as exc:  # noqa: BLE001 - one arm must not kill the run
                log.error("ideation failed for %s: %s", arm_key, exc)
                result.failed += wanted
                result.errors.append(f"{arm_key}: {exc}")
                continue

            # Record the spend even when every idea is later dropped: the tokens
            # were billed either way.
            if batch.cost_usd:
                db.insert_llm_call(
                    conn,
                    purpose="ideate",
                    model=batch.model,
                    input_tokens=batch.input_tokens,
                    output_tokens=batch.output_tokens,
                    cost_usd=batch.cost_usd,
                )
                conn.commit()
                result.llm_cost_usd += batch.cost_usd

            for candidate in batch.ideas:
                if _accept(conn, series, arm, candidate, recent_scenes, dry_run, result):
                    recent_scenes.append(candidate["scene_summary"])
    return result


def _accept(
    conn,
    series: SeriesConfig,
    arm: Arm,
    candidate: dict[str, Any],
    recent_scenes: list[str],
    dry_run: bool,
    result: IdeateResult,
) -> bool:
    required = ("scene_summary", "cinematography", "subject", "action", "context", "audio", "hook")
    missing = [key for key in required if not str(candidate.get(key, "")).strip()]
    if missing:
        result.failed += 1
        result.errors.append(f"{arm.key}: model returned an idea missing {missing}")
        return False

    scene_summary = candidate["scene_summary"].strip()
    too_similar, collided = dedup.is_too_similar(scene_summary, recent_scenes)
    if too_similar:
        log.info("dropping near-duplicate in %s: %r ~ %r", arm.key, scene_summary, collided)
        result.duplicates += 1
        return False

    record = {
        "series_id": series.id,
        "lang": arm.lang,
        "hook": candidate["hook"].strip(),
        "video_prompt": build_video_prompt(series, candidate),
        "scene_summary": scene_summary,
        "tags": list(series.hashtags.get(arm.lang, [])),
        "dedup_key": dedup.make_key(series.id, scene_summary),
    }

    if dry_run:
        result.ideas.append(record)
        result.inserted += 1
        return True

    idea_id = db.insert_idea(conn, **record)
    if idea_id is None:
        log.info("dropping exact duplicate in %s: %r", arm.key, scene_summary)
        result.duplicates += 1
        return False

    conn.commit()
    result.ideas.append({**record, "id": idea_id})
    result.inserted += 1
    return True
