"""Stage 2: turn `ideated` rows into mp4 files on disk.

Every call passes through BudgetGuard first — the whole point of policy C is
that the downside is capped in code, not in intent.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import config, db, paths
from ..backends import get_backend
from ..budget import BudgetExceeded, BudgetGuard
from ..models import VideoRequest

log = logging.getLogger(__name__)


@dataclass(slots=True)
class GenerateResult:
    generated: int = 0
    failed: int = 0
    skipped_budget: int = 0
    spent_usd: float = 0.0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def build_request(idea: sqlite3.Row, output_path: Path) -> VideoRequest:
    """Merge settings.yaml defaults with the series' own video block."""
    series = config.series_by_id(idea["series_id"])
    video = config.video_defaults(series)
    return VideoRequest(
        prompt=idea["video_prompt"],
        model=video["model"],
        duration_seconds=int(video.get("duration_seconds", 8)),
        aspect_ratio=video.get("aspect_ratio", "9:16"),
        resolution=video.get("resolution", "720p"),
        generate_audio=bool(video.get("generate_audio", True)),
        negative_prompt=video.get("negative_prompt"),
        output_path=str(output_path),
    )


def run(limit: int = 3, backend_name: str | None = None, dry_run: bool = False) -> GenerateResult:
    """Generate videos for up to `limit` ideas awaiting generation."""
    paths.ensure_work_dirs()
    settings = config.load_settings()
    name = backend_name or ("fake" if dry_run else settings["video"]["backend"])
    backend = get_backend(name)
    max_retries = int(settings.get("pipeline", {}).get("max_retries_per_idea", 2))

    result = GenerateResult()
    with db.session() as conn:
        guard = BudgetGuard(conn, settings)
        ideas = db.ideas_by_status(conn, "ideated", limit=limit)
        if not ideas:
            log.info("no ideas with status 'ideated'")
            return result

        for idea in ideas:
            idea_id = int(idea["id"])
            output = paths.ASSETS_DIR / f"idea_{idea_id:05d}.mp4"
            request = build_request(idea, output)
            cost = backend.estimate_cost(request)

            try:
                guard.check(cost)
            except BudgetExceeded as exc:
                log.warning("stopping: %s", exc)
                result.skipped_budget = len(ideas) - result.generated - result.failed
                result.errors.append(str(exc))
                break

            attempts = db.bump_attempts(conn, idea_id)
            try:
                video = backend.generate(request)
            except Exception as exc:  # noqa: BLE001 - one bad idea must not kill the run
                log.error("idea %d failed (attempt %d): %s", idea_id, attempts, exc)
                result.failed += 1
                result.errors.append(f"idea {idea_id}: {exc}")
                if attempts >= max_retries:
                    db.set_idea_status(conn, idea_id, "failed")
                continue

            db.insert_asset(
                conn,
                idea_id=idea_id,
                path=video.path,
                backend=video.backend,
                model=video.model,
                duration_s=video.duration_s,
                cost_usd=video.cost_usd,
                meta=video.meta,
            )
            db.set_idea_status(conn, idea_id, "generated")
            guard.commit(video.cost_usd)
            result.generated += 1
            result.spent_usd += video.cost_usd
            log.info("idea %d generated -> %s ($%.2f)", idea_id, video.path, video.cost_usd)

        log.info("budget: %s", guard.summary())
    return result


def generate_one(prompt: str, output_path: Path, backend_name: str | None = None) -> None:
    """Ad-hoc single generation. Used to smoke-test the backend end to end.

    Bypasses the ideas table, but the spend is still recorded so repeated
    smoke tests cannot quietly walk past the monthly ceiling.
    """
    settings = config.load_settings()
    video = settings["video"]
    backend = get_backend(backend_name or video["backend"])
    request = VideoRequest(
        prompt=prompt,
        model=video["model"],
        duration_seconds=int(video.get("duration_seconds", 8)),
        aspect_ratio=video.get("aspect_ratio", "9:16"),
        resolution=video.get("resolution", "720p"),
        generate_audio=bool(video.get("generate_audio", True)),
        negative_prompt=video.get("negative_prompt"),
        output_path=str(output_path),
    )
    with db.session() as conn:
        guard = BudgetGuard(conn, settings)
        guard.check(backend.estimate_cost(request))
        result = backend.generate(request)
        db.insert_asset(
            conn,
            idea_id=None,
            path=result.path,
            backend=result.backend,
            model=result.model,
            duration_s=result.duration_s,
            cost_usd=result.cost_usd,
            meta={**result.meta, "adhoc": True},
        )
        guard.commit(result.cost_usd)
        log.info("generated %s ($%.2f). %s", result.path, result.cost_usd, guard.summary())
