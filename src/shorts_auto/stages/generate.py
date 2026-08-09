"""Stage 2: turn `ideated` rows into mp4 files on disk.

Every paid call is bracketed the same way: check the budget, make the call,
record the spend, **commit**, then move on. Committing per item is the difference
between a crash costing one video and a crash costing the whole run's ledger
while the money is already gone.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, db, paths
from ..backends import get_backend
from ..backends.veo import BilledFailure, SafetyBlocked
from ..budget import BudgetExceeded, BudgetGuard, exclusive_run
from ..models import VideoRequest
from ..pricing import InvalidVideoRequest

log = logging.getLogger(__name__)


@dataclass(slots=True)
class GenerateResult:
    generated: int = 0
    failed: int = 0
    skipped_budget: int = 0
    spent_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


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
        negative_prompt=config.negative_prompt_for(series),
        person_generation=video.get("person_generation"),
        output_path=str(output_path),
    )


def _resolve_backend(backend_name: str | None, dry_run: bool) -> str:
    """dry_run always wins. A flag whose whole purpose is safety must not be
    overridable by another flag on the same command line."""
    if dry_run:
        if backend_name and backend_name != "fake":
            log.warning("--dry-run overrides --backend %s; using the fake backend", backend_name)
        return "fake"
    return backend_name or config.load_settings()["video"]["backend"]


def run(limit: int = 3, backend_name: str | None = None, dry_run: bool = False) -> GenerateResult:
    """Generate videos for up to `limit` ideas awaiting generation."""
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    paths.ensure_work_dirs()
    settings = config.load_settings()
    backend = get_backend(_resolve_backend(backend_name, dry_run))
    max_retries = int(settings.get("pipeline", {}).get("max_retries_per_idea", 2))

    result = GenerateResult()
    with exclusive_run("generate"), db.session() as conn:
        guard = BudgetGuard(conn, settings)
        ideas = db.ideas_by_status(conn, "ideated", limit=limit)
        if not ideas:
            log.info("no ideas with status 'ideated'")
            return result

        for idea in ideas:
            idea_id = int(idea["id"])
            attempt = int(idea["attempts"]) + 1
            output = paths.ASSETS_DIR / f"idea_{idea_id:05d}_try{attempt}.mp4"

            try:
                request = build_request(idea, output)
                cost = backend.estimate_cost(request)
            except (InvalidVideoRequest, KeyError) as exc:
                log.error("idea %d has an impossible video config: %s", idea_id, exc)
                db.set_idea_status(conn, idea_id, "failed")
                conn.commit()
                result.failed += 1
                result.errors.append(f"idea {idea_id}: {exc}")
                continue

            try:
                guard.check(cost)
            except BudgetExceeded as exc:
                log.warning("stopping: %s", exc)
                result.skipped_budget = len(ideas) - result.generated - result.failed
                result.errors.append(str(exc))
                break

            billed, video = 0.0, None
            try:
                video = backend.generate(request)
                billed = video.cost_usd
            except SafetyBlocked as exc:
                # Google does not charge for blocked generations.
                log.warning("idea %d blocked by safety filter: %s", idea_id, exc)
                db.insert_generation(
                    conn, idea_id=idea_id, path=None, backend=backend.name,
                    model=request.model, duration_s=0, cost_usd=0.0,
                    outcome="safety_blocked", meta={"error": str(exc)},
                )
                result.errors.append(f"idea {idea_id}: blocked by safety filter")
            except BilledFailure as exc:
                # Charged but unusable. Record the full price or the cap leaks.
                log.error("idea %d failed after billing: %s", idea_id, exc)
                db.insert_generation(
                    conn, idea_id=idea_id, path=None, backend=backend.name,
                    model=request.model, duration_s=request.duration_seconds, cost_usd=cost,
                    outcome="billed_failure", meta={"error": str(exc)},
                )
                billed = cost
                result.errors.append(f"idea {idea_id}: {exc}")
            except Exception as exc:  # noqa: BLE001 - one bad idea must not kill the run
                # Unknown failure. Assume it was billed: over-counting spend is
                # recoverable, under-counting is how a cap silently fails.
                log.error("idea %d failed: %s", idea_id, exc)
                db.insert_generation(
                    conn, idea_id=idea_id, path=None, backend=backend.name,
                    model=request.model, duration_s=request.duration_seconds, cost_usd=cost,
                    outcome="error", meta={"error": str(exc)},
                )
                billed = cost
                result.errors.append(f"idea {idea_id}: {exc}")

            if video is not None:
                db.insert_generation(
                    conn, idea_id=idea_id, path=video.path, backend=video.backend,
                    model=video.model, duration_s=video.duration_s, cost_usd=video.cost_usd,
                    outcome="ok", meta=video.meta,
                )
                db.set_idea_status(conn, idea_id, "generated")

            attempts = db.bump_attempts(conn, idea_id)
            if video is None and attempts >= max_retries:
                db.set_idea_status(conn, idea_id, "failed")

            # Commit before the next paid call, so a crash costs at most this one.
            conn.commit()
            guard.record(billed)
            result.spent_usd += billed
            if video is not None:
                result.generated += 1
                log.info("idea %d generated -> %s ($%.2f)", idea_id, video.path, billed)
            else:
                result.failed += 1

        log.info("budget: %s", guard.summary())
    return result


def generate_one(
    prompt: str,
    output_path: Path,
    backend_name: str | None = None,
    dry_run: bool = False,
) -> float:
    """Ad-hoc single generation, for smoke-testing the backend end to end.

    Bypasses the ideas table but is billed to the same ledger, so repeated smoke
    tests cannot walk past the monthly ceiling. Returns the amount charged.
    """
    settings = config.load_settings()
    video = settings["video"]
    backend = get_backend(_resolve_backend(backend_name, dry_run))
    request = VideoRequest(
        prompt=prompt,
        model=video["model"],
        duration_seconds=int(video.get("duration_seconds", 8)),
        aspect_ratio=video.get("aspect_ratio", "9:16"),
        resolution=video.get("resolution", "720p"),
        generate_audio=bool(video.get("generate_audio", True)),
        negative_prompt=video.get("negative_prompt"),
        person_generation=video.get("person_generation"),
        output_path=str(output_path),
    )

    with exclusive_run("generate"), db.session() as conn:
        guard = BudgetGuard(conn, settings)
        guard.check(backend.estimate_cost(request))
        result = backend.generate(request)
        db.insert_generation(
            conn, idea_id=None, path=result.path, backend=result.backend, model=result.model,
            duration_s=result.duration_s, cost_usd=result.cost_usd, outcome="ok",
            meta={**result.meta, "adhoc": True},
        )
        conn.commit()
        guard.record(result.cost_usd)
        log.info("generated %s ($%.2f). %s", result.path, result.cost_usd, guard.summary())
        return result.cost_usd
