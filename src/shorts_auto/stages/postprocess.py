"""Stage 3: turn a raw generation into the upload-ready file.

One idea has one render, enforced by `UNIQUE(idea_id)` on the renders table, so
re-running after a partial failure replaces rather than duplicates. The first
version had no such constraint and a retried postprocess produced two rows, both
of which the publisher then uploaded to the same channel.
"""

from __future__ import annotations

import logging
import os
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, db, ffmpeg, paths

log = logging.getLogger(__name__)

# Rough per-line budgets. CJK glyphs are about twice the advance width of Latin ones.
WRAP_CHARS = {"ja": 12, "en": 26}


@dataclass(slots=True)
class PostprocessResult:
    processed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def wrap_title(title: str, lang: str) -> str:
    """Break a hook into drawtext-friendly lines."""
    width = WRAP_CHARS.get(lang, 24)
    if lang == "ja":
        # No spaces to break on, so wrap purely by character count.
        return "\n".join(title[i : i + width] for i in range(0, len(title), width))
    return "\n".join(textwrap.wrap(title, width=width)) or title


def _postprocess_cfg() -> dict:
    return config.load_settings().get("postprocess", {})


def render_for_idea(idea, generation) -> tuple[Path, Path | None, str]:
    """Render one idea. Returns (video, thumbnail, the text actually burned in)."""
    cfg = _postprocess_cfg()
    source = Path(generation["path"])
    if not source.exists():
        raise ffmpeg.FFmpegError(f"generated file is missing: {source}")

    idea_id = int(idea["id"])
    hook = (idea["hook"] or "").strip()
    burned = wrap_title(hook, idea["lang"]) if hook else ""

    info = ffmpeg.video_info(source)
    output = paths.RENDERS_DIR / f"idea_{idea_id:05d}.mp4"

    # drawtext reads the title from a file rather than the filter string, so
    # Japanese text needs no escaping. The file is transient — the text that was
    # actually burned in is persisted on the render row.
    title_file = None
    try:
        if burned:
            handle, name = tempfile.mkstemp(suffix=".txt", prefix=f"title_{idea_id:05d}_")
            title_file = Path(name)
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(burned)

        ffmpeg.render_short(
            source,
            output,
            title_file=title_file,
            font_path=cfg.get("font_path"),
            font_size=int(cfg.get("font_size", 64)),
            has_audio=info["has_audio"],
            loudness_target=float(cfg.get("loudness_target", -14.0)),
        )
    finally:
        if title_file is not None:
            title_file.unlink(missing_ok=True)

    thumb = paths.THUMBS_DIR / f"idea_{idea_id:05d}.jpg"
    try:
        ffmpeg.extract_thumbnail(output, thumb, at_seconds=min(1.0, max(0.0, info["duration"] / 2)))
    except ffmpeg.FFmpegError as exc:
        # A missing thumbnail is cosmetic; YouTube generates one automatically.
        log.warning("thumbnail extraction failed for idea %d: %s", idea_id, exc)
        return output, None, burned

    return output, thumb, burned


def run(limit: int = 10) -> PostprocessResult:
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    paths.ensure_work_dirs()
    result = PostprocessResult()

    with db.session() as conn:
        ideas = db.ideas_by_status(conn, "generated", limit=limit)
        if not ideas:
            log.info("no ideas with status 'generated'")
            return result

        for idea in ideas:
            idea_id = int(idea["id"])
            generation = db.latest_generation(conn, idea_id)
            if generation is None or not generation["path"]:
                result.failed += 1
                result.errors.append(f"idea {idea_id}: no usable generation on record")
                continue

            try:
                video, thumb, burned = render_for_idea(idea, generation)
            except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, ffmpeg.FontMissing) as exc:
                log.error("idea %d postprocess failed: %s", idea_id, exc)
                result.failed += 1
                result.errors.append(f"idea {idea_id}: {exc}")
                continue

            db.upsert_render(
                conn,
                idea_id=idea_id,
                generation_id=int(generation["id"]),
                path=str(video),
                thumb_path=str(thumb) if thumb else None,
                burned_hook=burned,
            )
            db.set_idea_status(conn, idea_id, "post_processed")
            conn.commit()
            result.processed += 1
            log.info("idea %d rendered -> %s", idea_id, video)

    return result
