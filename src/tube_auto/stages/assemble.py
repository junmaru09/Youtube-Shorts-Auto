"""Stage 6: draw the whiteboard against the narration and produce the video.

The audio is the spine. The narration timeline says when every line starts
and ends and what it does to the stage, so the picture is the stage replayed
line by line (stages/whiteboard.py) with the two characters and the subtitle
band drawn in, then the music bed mixed under the voices.

What is burned in:

- **Subtitles**, in the band at the bottom, coloured by speaker, because a
  large share of this audience watches without sound. An SRT is written too,
  for YouTube's own caption track.
- **A closing card**, because the end-screen API does not exist and the next
  video has to be offered somehow.

There are no chapter cards and no title telop any more: the reference format
has neither. Section changes are a background swap and a question; the title
is the thumbnail's job.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import bgm
from .. import brand as brand_mod
from .. import config, db, ffmpeg, paths
from .. import quality
from . import whiteboard


def _dropped_visuals(script_row) -> int:
    """Lines whose visual was dropped to `hold` at script time: a `hold`
    visual is the script's own, an empty ops list under a non-hold visual
    is a drop."""
    try:
        chapters = json.loads(script_row["chapters_json"])
    except (TypeError, ValueError):
        return 0
    return sum(1 for c in chapters for line in c.get("lines", [])
               if line.get("visual") == ["hold"] and line.get("_dropped"))

log = logging.getLogger(__name__)

SEGMENT_PRESET = "veryfast"
FINAL_PRESET = "veryfast"


@dataclass(slots=True)
class AssembleResult:
    assembled: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)


def _escape(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    for char in (":", "'", "[", "]", ","):
        text = text.replace(char, f"\\{char}")
    return text


def format_chapters(spans: list[dict[str, Any]]) -> str:
    """Description timestamps. YouTube needs the first one to be 00:00."""
    lines = []
    for index, span in enumerate(spans):
        start = 0 if index == 0 else int(span["start_s"])
        stamp = f"{start // 60:02d}:{start % 60:02d}"
        lines.append(f"{stamp} {span['title']}")
    return "\n".join(lines)


# Japanese line breaking: these may not start a line, so a naive split by
# character count strands a lone 。 or ） on the next row.
NO_LINE_START = "。、）」』】〕》〉！？ー・…‥,.!?)]}"
NO_LINE_END = "（「『【〔《〈([{"


def wrap_japanese(text: str, width: int) -> str:
    """Break a line at `width`, without orphaning punctuation.

    Japanese has no spaces, so wrapping is by count — but a break that puts a
    closing mark at the start of a line, or an opening bracket at the end of
    one, reads as a typesetting error.
    """
    if len(text) <= width:
        return text

    lines: list[str] = []
    remaining = text
    while len(remaining) > width:
        cut = width
        while cut > 1 and (remaining[cut] in NO_LINE_START or remaining[cut - 1] in NO_LINE_END):
            cut -= 1
        if cut <= 1:
            cut = width
        lines.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        lines.append(remaining)
    return "\n".join(lines)


def build_subtitles(timeline: list[dict[str, Any]], output: Path, wrap: int = 26) -> Path:
    """An SRT from the narration timeline, wrapped for a 1080p frame."""
    def stamp(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for index, entry in enumerate(timeline, start=1):
        wrapped = wrap_japanese(entry["display"], wrap)
        blocks.append(f"{index}\n{stamp(entry['start_s'])} --> {stamp(entry['end_s'])}\n{wrapped}\n")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(blocks), encoding="utf-8")
    return output


CLOSING_SECONDS = 11.0


def _closing_card(
    output: Path, size: tuple[int, int], fps: int, brand: brand_mod.Brand
) -> Path | None:
    """The card the module docstring has always promised.

    YouTube's end-screen has no API, so the only way to offer the next video is
    to burn the offer into the last few seconds. Until now this was documented
    and not implemented — the episode simply stopped on its last cut.
    """
    width, height = size
    try:
        font = ffmpeg.find_font(brand.font_path)
    except ffmpeg.FontMissing:
        return None

    lines = [
        (brand.channel_name, brand.telop["chapter_size"], brand.palette["accent"], 0.30),
        (brand.tagline, brand.telop["subtitle_size"], brand.palette["sub"], 0.44),
        ("チャンネル登録と、次の一本へ", brand.telop["subtitle_size"], brand.palette["ink"], 0.66),
    ]

    files: list[Path] = []
    draws: list[str] = []
    for index, (text, font_size, colour, y) in enumerate(lines):
        if not text.strip():
            continue
        text_file = output.parent / f"{output.stem}_{index}.txt"
        text_file.write_text(text, encoding="utf-8")
        files.append(text_file)
        draws.append(
            f"drawtext=fontfile='{_escape(font)}':textfile='{_escape(text_file)}'"
            f":expansion=none:fontsize={font_size}:fontcolor={colour}"
            f":x=(w-text_w)/2:y=h*{y}"
        )
    draws.append(
        f"drawbox=x=(iw-{int(width * 0.24)})/2:y={int(height * 0.56)}"
        f":w={int(width * 0.24)}:h=6:color={brand.palette['accent']}:t=fill"
    )

    try:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"color={brand.palette['bg']}:s={width}x{height}:r={fps}:d={CLOSING_SECONDS}",
            "-vf", ",".join(draws),
            *ffmpeg.video_args(22, SEGMENT_PRESET),
            "-pix_fmt", "yuv420p", str(output),
        ], timeout=300)
    except ffmpeg.FFmpegError as exc:
        log.warning("closing card failed, ending on the last cut instead: %s", exc)
        return None
    finally:
        for text_file in files:
            text_file.unlink(missing_ok=True)
    return output


def _concat(parts: list[Path], output: Path) -> Path:
    listing = output.parent / f"{output.stem}_parts.txt"
    listing.write_text("\n".join(f"file '{p.resolve()}'" for p in parts) + "\n", encoding="utf-8")
    try:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(output),
        ], timeout=1800)
    finally:
        listing.unlink(missing_ok=True)
    return output


def build_video(
    idea_id: int,
    assets: list[dict[str, Any]],
    narration: Path,
    timeline: list[dict[str, Any]],
    output: Path,
    brand: brand_mod.Brand,
    spans: list[dict[str, Any]],
    settings: dict[str, Any],
) -> Path:
    """Replay the stage against the audio and mix it all down."""
    video_cfg = settings.get("video", {})
    post_cfg = settings.get("postprocess", {})
    chapter_keys = [c.key for c in brand_mod.EPISODE_PLAN]
    size = (int(video_cfg.get("width", 1920)), int(video_cfg.get("height", 1080)))
    fps = int(video_cfg.get("fps", 30))
    if size != (1920, 1080):
        log.warning("the whiteboard is drawn at 1920x1080; video.width/height %s are ignored", size)

    workdir = output.parent / f"{output.stem}_segments"
    workdir.mkdir(parents=True, exist_ok=True)

    frames = whiteboard.render_frames(timeline, assets, brand, workdir / "frames")
    for problem in frames.problems[:5]:
        log.warning("idea %d: %s", idea_id, problem)
    if frames.entries == 0:
        raise ffmpeg.FFmpegError("no frames; the timeline is empty")
    log.info("idea %d: %d frames, %d listing rows, %.1f min of picture",
             idea_id, frames.frames, frames.entries, frames.seconds / 60)

    picture = whiteboard.frames_to_video(frames.path, workdir / "stage.mp4", fps, frames.seconds)
    segments = [picture]
    closing = _closing_card(workdir / "closing.mp4", size, fps, brand)
    if closing is not None:
        segments.append(closing)
    silent = _concat(segments, workdir / "picture.mp4")

    loudness = float(post_cfg.get("loudness_target", -14.0))
    gain = float(post_cfg.get("bgm_gain_db", -22.0))
    bed, _plan = bgm.build_bed(
        spans, chapter_keys, workdir / "bgm",
        seed=str(idea_id), bgm_dir=Path(post_cfg.get("bgm_dir", "work/bgm")),
    )

    pad = CLOSING_SECONDS if closing is not None else 0.0
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(silent), "-i", str(narration)]
    if bed is not None:
        cmd += ["-i", str(bed)]
        audio_filter = bgm.mix_filter(gain, loudness, pad)
    else:
        audio_filter = bgm.narration_only_filter(loudness, pad)

    cmd += [
        "-filter_complex", audio_filter,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        # loudnorm resamples internally and will happily emit 96 kHz if left to
        # itself, which YouTube then re-encodes. Pin the output rate.
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest",
        str(output),
    ]
    ffmpeg._run(cmd, timeout=3600)

    # The render is already written by this point; the workdir is scratch.
    # Removed whole rather than by pattern — the music bed lives in a
    # subdirectory, and a pattern that only matched *.mp4 left it behind, so
    # rmdir failed and reported a finished render as a failed one.
    shutil.rmtree(workdir, ignore_errors=True)
    return output


def _make_thumbnails(conn, idea_id: int, idea, script_row, assets: list[dict[str, Any]]) -> None:
    """Draw the three thumbnails.

    Advisory: the video is already rendered and committed, and a video with a
    frame YouTube chose is worth far more than no video at all. A failure here
    leaves `thumb_path` NULL, which is exactly what `tube-auto thumbnails` looks
    for, so the work is picked up rather than lost.
    """
    from . import thumbnails

    try:
        hooks = json.loads(script_row["hooks_json"] or "[]")
        script_text = "".join(
            line["display"]
            for chapter in json.loads(script_row["chapters_json"])
            for line in chapter.get("lines", [])
        )
        made = thumbnails.build(idea_id, idea["hook"] or "", hooks, script_text, assets)
    except Exception as exc:  # noqa: BLE001 - never lose a finished render over a JPEG
        log.warning("thumbnails failed for idea %d: %s", idea_id, exc)
        return

    db.replace_assets(conn, idea_id, "thumb", [
        {"path": str(path), "order_idx": index, "license_ok": True,
         "meta": {"variant": thumbnails.VARIANTS[index]}}
        for index, path in enumerate(made)
    ])
    db.set_render_thumb(conn, idea_id, str(made[0]))
    conn.commit()


def run(limit: int = 1, idea_id: int | None = None) -> AssembleResult:
    from .narrate import chapter_spans

    settings = config.load_settings()
    brand = brand_mod.load_brand()

    result = AssembleResult()
    with db.session() as conn:
        if idea_id is not None:
            row = db.get_idea(conn, idea_id)
            ideas = [row] if row else []
        else:
            ideas = db.ideas_by_status(conn, "sourced", limit=limit)

        if not ideas:
            log.info("no ideas with status 'sourced'")
            return result

        for idea in ideas:
            current_id = int(idea["id"])
            narration_row = db.get_narration(conn, current_id)
            script_row = db.get_script(conn, current_id)
            assets = [dict(a) for a in db.get_assets(conn, current_id)]

            if narration_row is None or script_row is None:
                result.failed += 1
                result.errors.append(f"idea {current_id}: narration or script missing")
                continue

            blocked = db.unlicensed_assets(conn, current_id)
            if blocked:
                result.failed += 1
                result.errors.append(
                    f"idea {current_id}: {len(blocked)} asset(s) have no rights clearance; "
                    "refusing to assemble"
                )
                continue

            timeline = json.loads(narration_row["timeline_json"])
            spans = chapter_spans(timeline)

            output = paths.RENDERS_DIR / f"idea_{current_id:05d}.mp4"
            subtitles = paths.RENDERS_DIR / f"idea_{current_id:05d}.srt"
            build_subtitles(timeline, subtitles)

            try:
                build_video(
                    current_id, assets, Path(narration_row["path"]), timeline,
                    output, brand, spans, settings,
                )
            except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, ffmpeg.FontMissing, OSError) as exc:
                log.error("assembly failed for idea %d: %s", current_id, exc)
                result.failed += 1
                result.errors.append(f"idea {current_id}: {exc}")
                continue

            info = ffmpeg.video_info(output)
            report = quality.score(timeline, dropped_visuals=_dropped_visuals(script_row),
                                   thresholds=settings.get("quality", {}))
            report_path = paths.WORK_DIR / "logs" / f"quality_idea{current_id:05d}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(f"# idea {current_id}\n\n{report.describe()}\n", encoding="utf-8")
            if not report.ok:
                log.warning("idea %d is below the quality bar: %s", current_id, "; ".join(report.problems))
                result.errors.append(f"idea {current_id} (品質基準未満、{report_path}): " + "; ".join(report.problems))
            db.upsert_render(
                conn,
                idea_id=current_id,
                path=str(output),
                thumb_path=None,
                duration_s=info["duration"],
                chapters_text=format_chapters(spans),
            )
            db.set_idea_status(conn, current_id, "assembled")
            conn.commit()

            _make_thumbnails(conn, current_id, idea, script_row, assets)

            result.assembled += 1
            result.details.append(
                {"idea_id": current_id, "duration_s": info["duration"], "path": str(output)}
            )
            log.info("idea %d assembled: %.1f min -> %s", current_id, info["duration"] / 60, output)

    return result
