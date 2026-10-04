"""Stage 5½: draw the whiteboard, one frame per narration line.

The narration timeline says what each line does to the stage (`ops`), who
says it, and how long it takes. This module replays those operations on a
Canvas, renders the stage once per line, and writes a frame list ffmpeg's
concat demuxer can play: every entry is a PNG and a duration.

Two frames per line, not one: the speaking character's mouth is open in one
and closed in the other, and the frame list alternates between them while
the line's audio is loud enough to count as speech. That is the whole of the
lip-sync, and it is what the reference videos do too.
"""

from __future__ import annotations

import logging
import math
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .. import brand as brand_mod
from .. import paths
from ..canvas import AssetLibrary, Canvas, CanvasError, SpriteSet
from ..canvas import style as S

log = logging.getLogger(__name__)

# Mouth flap timing. 8 changes a second is the reference's rate by eye; a
# window below the median syllable length makes each vowel a distinct flap.
FLAP_SECONDS = 0.12
# Fraction of the line's own peak below which the mouth is closed.
SPEECH_THRESHOLD = 0.18


@dataclass(slots=True)
class FrameList:
    path: Path                  # the concat listing
    seconds: float              # what the listing plays for
    frames: int = 0             # distinct PNGs written
    entries: int = 0            # rows in the listing
    problems: list[str] = field(default_factory=list)


def _loudness_windows(audio: Path, window: float) -> list[float]:
    """RMS per window of the line's WAV, normalised to the line's peak."""
    try:
        with wave.open(str(audio)) as handle:
            rate = handle.getframerate()
            width = handle.getsampwidth()
            channels = handle.getnchannels()
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return []
    if width != 2 or not frames:
        return []
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    if channels > 1:
        samples = samples[::channels]
    step = max(1, int(rate * window))
    levels = []
    for start in range(0, len(samples), step):
        chunk = samples[start:start + step]
        levels.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    peak = max(levels) or 1.0
    return [level / peak for level in levels]


def _photo_for(chapter_index: int, assets: list[dict[str, Any]]) -> Path | None:
    """The chapter's first licensed still, if footage gathered one."""
    for asset in assets:
        if asset.get("kind") == "still" and asset.get("chapter") == chapter_index and asset.get("license_ok"):
            path = Path(asset["path"])
            if path.exists():
                return path
    return None


def render_frames(
    timeline: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    brand: brand_mod.Brand,
    workdir: Path,
    library: AssetLibrary | None = None,
    sprites: SpriteSet | None = None,
) -> FrameList:
    """Replay the script's operations and write the frames and their listing."""
    workdir.mkdir(parents=True, exist_ok=True)
    library = library or AssetLibrary(paths.ASSETS_DIR)
    sprites = sprites or SpriteSet(paths.SPRITES_DIR, {role: nav.sprite for role, nav in brand.navigators.items()})
    canvas = Canvas(assets=library)
    listing = workdir / "frames.txt"
    rows: list[str] = []
    result = FrameList(path=listing, seconds=0.0)
    cursor = 0.0
    last_frame: Path | None = None
    current_chapter = -1

    for index, entry in enumerate(timeline):
        speaker = entry.get("speaker", "explainer")
        start, end = float(entry["start_s"]), float(entry["end_s"])

        # the gap before this line: hold the previous frame, mouth closed
        if last_frame is not None and start > cursor + 1e-3:
            rows.append(f"file '{last_frame.resolve()}'\nduration {start - cursor:.3f}")
        cursor = start

        # a new chapter may bring a photo to sit behind the stage
        if entry.get("chapter", 0) != current_chapter:
            current_chapter = int(entry.get("chapter", 0))
            photo = _photo_for(current_chapter, assets)
            canvas.chapter_photo = photo

        canvas.apply({"op": "expression", speaker: entry.get("expression", "normal") or "normal"})
        before = {id(item) for item in canvas.state.items}
        ops = entry.get("ops") or [{"op": "hold"}]
        cleared = any(op.get("op") in ("clear", "background") for op in ops)
        for op in ops:
            try:
                canvas.apply(op)
            except CanvasError as exc:
                # validated at script time; a stale asset can still break here
                result.problems.append(f"line {index}: {exc}")
                break
        added = [item for item in canvas.state.items if id(item) not in before and item.kind != "dim"]

        def compose(stage: Image.Image, mouth: str, tag: str) -> Path:
            img = stage.copy()
            canvas.finish(img, entry.get("display", ""), speaker, sprites, mouth_open=(mouth == "open"))
            path = workdir / f"f{index:04d}_{tag}{mouth}.png"
            img.save(path, compress_level=1)
            result.frames += 1
            return path

        stage = canvas.render_stage()
        frames = {mouth: compose(stage, mouth, "") for mouth in ("closed", "open")}
        # the moments right after something appears: a glow behind it
        glow_frames = None
        if added:
            glow_stage = canvas.render_stage(halo=added)
            glow_frames = {mouth: compose(glow_stage, mouth, "new_") for mouth in ("closed", "open")}

        # a chapter's first line: dissolve from the last frame instead of cutting
        if cleared and last_frame is not None:
            previous = Image.open(last_frame).convert("RGB")
            target = Image.open(frames["closed"]).convert("RGB")
            steps = 3
            for k in range(1, steps + 1):
                blend = Image.blend(previous, target, k / (steps + 1))
                path = workdir / f"f{index:04d}_x{k}.png"
                blend.save(path, compress_level=1)
                result.frames += 1
                rows.append(f"file '{path.resolve()}'\nduration {S.CROSSFADE_SECONDS / steps:.3f}")

        # alternate open/closed while the audio is loud
        seconds = max(0.05, end - start)
        levels = _loudness_windows(Path(entry["audio"]), FLAP_SECONDS) if entry.get("audio") else []
        if not levels:
            rows.append(f"file '{frames['closed'].resolve()}'\nduration {seconds:.3f}")
        else:
            t = 0.0
            flip = False
            for level in levels:
                if t >= seconds:
                    break
                dur = min(FLAP_SECONDS, seconds - t)
                mouth = "open" if (level > SPEECH_THRESHOLD and not flip) else "closed"
                if level > SPEECH_THRESHOLD:
                    flip = not flip
                pick = glow_frames if (glow_frames and t < S.APPEAR_SECONDS) else frames
                rows.append(f"file '{pick[mouth].resolve()}'\nduration {dur:.3f}")
                t += dur
            if t < seconds - 1e-3:
                rows.append(f"file '{frames['closed'].resolve()}'\nduration {seconds - t:.3f}")
        cursor = end
        last_frame = frames["closed"]

    if last_frame is not None:
        # the concat demuxer ignores the last entry's duration unless the
        # file is listed once more after it
        rows.append(f"file '{last_frame.resolve()}'")
    listing.write_text("\n".join(rows) + "\n", encoding="utf-8")
    result.seconds = cursor
    result.entries = len(rows)
    return result


def frames_to_video(listing: Path, output: Path, fps: int, seconds: float, crf: int = 20) -> Path:
    """Play the frame list into a silent H.264 file."""
    from .. import ffmpeg

    ffmpeg._run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", f"fps={fps},format=yuv420p",
        "-t", f"{seconds:.3f}",
        *ffmpeg.video_args(crf, "veryfast"),
        "-pix_fmt", "yuv420p", str(output),
    ], timeout=3600)
    return output
