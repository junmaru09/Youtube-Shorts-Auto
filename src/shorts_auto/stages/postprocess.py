"""Stage 3: turn a raw generation into per-language, upload-ready renders.

One source video produces one render per language. The burned-in title differs
per language, which is what keeps the ja and en uploads from being byte-identical
files — YouTube treats duplicate uploads across channels as reused content.
"""

from __future__ import annotations

import json
import logging
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, db, ffmpeg, paths

log = logging.getLogger(__name__)

# Rough per-line budgets. CJK glyphs are ~2x the advance width of Latin ones.
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


def render_asset(source_asset, idea, lang: str) -> tuple[Path, Path]:
    """Render one language variant plus its thumbnail. Returns (video, thumb)."""
    cfg = _postprocess_cfg()
    source = Path(source_asset["path"])
    idea_id = int(idea["id"])

    hook = json.loads(idea["hook_json"]).get(lang, "").strip()
    title_file = None
    if hook:
        title_file = paths.RENDERS_DIR / f"idea_{idea_id:05d}_{lang}.txt"
        title_file.write_text(wrap_title(hook, lang), encoding="utf-8")

    info = ffmpeg.video_info(source)
    output = paths.RENDERS_DIR / f"idea_{idea_id:05d}_{lang}.mp4"
    ffmpeg.render_short(
        source,
        output,
        title_file=title_file,
        font_path=cfg.get("font_path"),
        font_size=int(cfg.get("font_size", 64)),
        has_audio=info["has_audio"],
        loudness_target=float(cfg.get("loudness_target", -14.0)),
    )

    thumb = paths.THUMBS_DIR / f"idea_{idea_id:05d}_{lang}.jpg"
    ffmpeg.extract_thumbnail(output, thumb, at_seconds=min(1.0, max(0.0, info["duration"] / 2)))
    return output, thumb


def run(limit: int = 10) -> PostprocessResult:
    paths.ensure_work_dirs()
    result = PostprocessResult()

    with db.session() as conn:
        ideas = db.ideas_by_status(conn, "generated", limit=limit)
        if not ideas:
            log.info("no ideas with status 'generated'")
            return result

        for idea in ideas:
            idea_id = int(idea["id"])
            sources = [a for a in db.assets_for_idea(conn, idea_id) if a["lang"] == "src"]
            if not sources:
                result.failed += 1
                result.errors.append(f"idea {idea_id}: no source asset on disk")
                continue
            source_asset = sources[-1]
            series = config.series_by_id(idea["series_id"])

            try:
                for lang in series.languages:
                    video, thumb = render_asset(source_asset, idea, lang)
                    db.insert_asset(
                        conn,
                        idea_id=idea_id,
                        lang=lang,
                        path=str(video),
                        backend=source_asset["backend"],
                        model=source_asset["model"],
                        duration_s=source_asset["duration_s"],
                        cost_usd=0.0,  # rendering is local; the generation was already billed
                        meta={"thumbnail": str(thumb), "source_asset_id": source_asset["id"]},
                    )
                    log.info("idea %d rendered [%s] -> %s", idea_id, lang, video)
            except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing) as exc:
                log.error("idea %d postprocess failed: %s", idea_id, exc)
                result.failed += 1
                result.errors.append(f"idea {idea_id}: {exc}")
                continue

            db.set_idea_status(conn, idea_id, "post_processed")
            result.processed += 1

    return result
