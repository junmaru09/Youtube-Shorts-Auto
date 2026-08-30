"""Stage 6: cut the visuals to the narration and produce the video.

The audio is the spine. Every visual was planned against the narration timeline,
so assembly is mostly a matter of laying each asset into the span it was given
and letting the soundtrack decide the length.

Four things are burned in rather than left to YouTube:

- **Subtitles**, because a large share of this audience watches without sound.
- **Chapter cards**, because a twenty-minute video needs visible structure and a
  description timestamp alone does not provide it on screen.
- **The title**, five seconds in rather than at the top. The opening five
  seconds are the measured pattern-interrupt window; spending them on a logo
  spends the only part of the video everyone watches.
- **A closing card**, because the end-screen API does not exist and the next
  video has to be offered somehow.

Stills get a slow push-in. A static frame held for ten seconds reads as dead
air; the same frame moving slightly does not.
"""

from __future__ import annotations

import json
import logging
import math
import random
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import bgm
from .. import brand as brand_mod
from .. import config, db, ffmpeg, paths

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


# NASA release packages open with a slate: a full-frame insignia over a
# "MISSION FEATURE" card, typically five to ten seconds. Nothing in the metadata
# says so, so the only reliable defence is to start well past it.
SLATE_SECONDS = 12.0


def _footage_offset(path: Path, needed: float) -> float:
    """Where to start a clip so a title slate does not end up on screen.

    Skips the head where the clip is long enough to afford it, and falls back to
    a small offset on short clips — which are usually pure renders anyway, since
    packages with slates run to minutes.
    """
    try:
        duration = ffmpeg.video_info(path)["duration"]
    except (ffmpeg.FFmpegError, OSError):
        return 1.0
    if duration <= needed + 2:
        return 0.0
    return min(SLATE_SECONDS, max(1.0, (duration - needed) * 0.25))


def _segment(
    asset: dict[str, Any],
    seconds: float,
    output: Path,
    size: tuple[int, int],
    fps: int,
    seed: int,
) -> Path:
    """Render one cut to a uniform format so concatenation is trivial."""
    width, height = size
    path = Path(asset["path"])
    kind = asset["kind"]

    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},setsar=1"
    )

    if kind in ("footage", "diagram"):
        offset = _footage_offset(path, seconds) if kind == "footage" else 0.0
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{offset:.3f}", "-t", f"{seconds:.3f}", "-i", str(path),
            "-vf", fit, "-an",
        ]
    else:
        # A still, pushed in slowly.
        #
        # zoompan's `d` is output frames *per input frame*. Combined with
        # `-loop 1`, which feeds the image once per frame, anything above d=1
        # multiplies: d=250 against a 208-frame input asked for 52,000 frames
        # and produced a 204MB file for an eight-second cut. Keep d=1 and drive
        # the zoom from `on`, the output frame counter.
        frames = max(2, int(seconds * fps))
        zoom_end = 1.12
        pan_x = "iw/2-(iw/zoom/2)" if seed % 2 else "0"
        pan_y = "ih/2-(ih/zoom/2)" if seed % 3 else "0"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-t", f"{seconds:.3f}", "-r", str(fps), "-i", str(path),
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"zoompan=z='1+{zoom_end - 1:.4f}*on/{frames}':d=1:"
                f"x='{pan_x}':y='{pan_y}':s={width}x{height}:fps={fps},"
                f"setsar=1"
            ),
            "-frames:v", str(frames), "-an",
        ]

    cmd += [
        *ffmpeg.video_args(22, SEGMENT_PRESET),
        "-pix_fmt", "yuv420p", str(output),
    ]
    ffmpeg._run(cmd, timeout=900)
    return output


def _chapter_card(
    title: str, output: Path, size: tuple[int, int], fps: int,
    brand: brand_mod.Brand, seconds: float = 1.6,
) -> Path:
    """A card announcing the next chapter. Structure the viewer can see."""
    width, height = size
    font = ffmpeg.find_font(brand.font_path)
    text_file = output.parent / f"{output.stem}.txt"
    text_file.write_text(title, encoding="utf-8")
    try:
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"color={brand.palette['bg']}:s={width}x{height}:r={fps}:d={seconds}",
            "-vf",
            (
                f"drawtext=fontfile='{_escape(font)}':textfile='{_escape(text_file)}'"
                f":expansion=none:fontsize={brand.telop['chapter_size']}"
                f":fontcolor={brand.palette['ink']}:x=(w-text_w)/2:y=(h-text_h)/2"
                f",drawbox=x=(iw-420)/2:y=ih/2+90:w=420:h=3:"
                f"color={brand.palette['accent']}:t=fill"
            ),
            *ffmpeg.video_args(22, SEGMENT_PRESET),
            "-pix_fmt", "yuv420p", str(output),
        ])
    finally:
        text_file.unlink(missing_ok=True)
    return output


CLOSING_SECONDS = 11.0
# The title appears after the hook, not before it. A pre-roll card spends the
# five seconds that decide whether anyone stays — the measured pattern-interrupt
# window — on branding nobody has a reason to care about yet.
TELOP_START = 5.0
TELOP_SECONDS = 4.5


def _title_telop(brand: brand_mod.Brand, title: str) -> str:
    """A filter fragment putting the episode title on screen during the hook."""
    if not title.strip():
        return ""
    try:
        font = ffmpeg.find_font(brand.font_path)
    except ffmpeg.FontMissing:
        return ""

    text = wrap_japanese(title.strip(), 18).replace("\n", " ")
    size = brand.telop["keyword_size"]
    end = TELOP_START + TELOP_SECONDS
    box = f"boxcolor=black@0.55:box=1:boxborderw={size // 3}"
    return (
        f",drawtext=fontfile='{_escape(font)}':text='{_drawtext_escape(text)}'"
        f":expansion=none:fontsize={size}:fontcolor={brand.palette['ink']}"
        f":borderw=4:bordercolor=black@0.8:{box}"
        f":x=(w-text_w)/2:y=h*0.16"
        # Fade the box and text together at both ends rather than cutting.
        f":alpha='if(lt(t,{TELOP_START}),0,"
        f"if(lt(t,{TELOP_START + 0.4}),(t-{TELOP_START})/0.4,"
        f"if(lt(t,{end - 0.6}),1,if(lt(t,{end}),({end}-t)/0.6,0))))'"
    )


def _drawtext_escape(text: str) -> str:
    """Escape for an inline drawtext `text=` value.

    Chapter cards use `textfile=` and avoid this entirely; the telop cannot,
    because it needs to sit in the same filter graph as the subtitles.
    """
    for char, replacement in (("\\", r"\\"), (":", r"\:"), ("'", r"\'"), ("%", r"\%")):
        text = text.replace(char, replacement)
    return text


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
    subtitles: Path,
    output: Path,
    brand: brand_mod.Brand,
    chapters: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    settings: dict[str, Any],
    title_text: str = "",
) -> Path:
    """Lay the visuals against the audio and mix it all down."""
    video_cfg = settings.get("video", {})
    post_cfg = settings.get("postprocess", {})
    chapter_keys = [c.key for c in brand_mod.EPISODE_PLAN]
    size = (int(video_cfg.get("width", 1920)), int(video_cfg.get("height", 1080)))
    fps = int(video_cfg.get("fps", 30))

    workdir = output.parent / f"{output.stem}_segments"
    workdir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []

    ordered = sorted(
        (a for a in assets if a["path"]),
        key=lambda a: (json.loads(a["meta_json"] or "{}").get("start_s", 0.0), a["order_idx"]),
    )
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for asset in ordered:
        by_chapter.setdefault(asset["chapter"] or 0, []).append(asset)

    for index, span in enumerate(spans):
        title = chapters[index].get("title", "") if index < len(chapters) else span["title"]
        if index > 0 and title:
            segments.append(
                _chapter_card(title, workdir / f"card_{index:02d}.mp4", size, fps, brand)
            )
        for order, asset in enumerate(by_chapter.get(index, [])):
            seconds = float(asset["duration_s"]) or 6.0
            segment = workdir / f"seg_{index:02d}_{order:03d}.mp4"
            try:
                _segment(asset, seconds, segment, size, fps, seed=order)
            except ffmpeg.FFmpegError as exc:
                log.warning("cut failed (%s), skipping: %s", asset["path"], exc)
                continue
            segments.append(segment)

    if not segments:
        raise ffmpeg.FFmpegError("no usable segments; nothing to assemble")

    closing = _closing_card(workdir / "closing.mp4", size, fps, brand)
    if closing is not None:
        segments.append(closing)

    silent = _concat(segments, workdir / "picture.mp4")

    # The picture is cut to the audio, but rounding across a hundred segments
    # drifts. `-shortest` lets the narration decide the final length.
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
        "-filter_complex",
        # BorderStyle=4 paints an opaque box behind the text. NASA imagery runs
        # from black starfields to white press figures, and an outline alone
        # leaves the subtitle unreadable on the bright ones.
        f"[0:v]subtitles='{_escape(subtitles)}':force_style="
        f"'FontSize=22,PrimaryColour=&H00FFFFFF,BorderStyle=4,"
        f"BackColour=&HA0000000,Outline=0,Shadow=0,MarginV=48'"
        + _title_telop(brand, title_text)
        + "[v];"
        + audio_filter,
        "-map", "[v]", "-map", "[a]",
        *ffmpeg.video_args(21, FINAL_PRESET),
        "-pix_fmt", "yuv420p",
        # loudnorm resamples internally and will happily emit 96 kHz if left to
        # itself, which YouTube then re-encodes. Pin the output rate.
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest",
        str(output),
    ]
    ffmpeg._run(cmd, timeout=3600)

    for leftover in workdir.glob("*.mp4"):
        leftover.unlink(missing_ok=True)
    workdir.rmdir()
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

            if narration_row is None or script_row is None or not assets:
                result.failed += 1
                result.errors.append(f"idea {current_id}: narration, script or assets missing")
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
            chapters = json.loads(script_row["chapters_json"])
            spans = chapter_spans(timeline)

            output = paths.RENDERS_DIR / f"idea_{current_id:05d}.mp4"
            subtitles = paths.RENDERS_DIR / f"idea_{current_id:05d}.srt"
            build_subtitles(timeline, subtitles)

            try:
                build_video(
                    current_id, assets, Path(narration_row["path"]), subtitles,
                    output, brand, chapters, spans, settings,
                    title_text=idea["hook"] or "",
                )
            except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, ffmpeg.FontMissing, OSError) as exc:
                log.error("assembly failed for idea %d: %s", current_id, exc)
                result.failed += 1
                result.errors.append(f"idea {current_id}: {exc}")
                continue

            info = ffmpeg.video_info(output)
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
