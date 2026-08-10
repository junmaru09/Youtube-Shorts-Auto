"""Stage 5: draw the figures NASA does not have.

NASA holds 51 videos about black holes and three about gravitational waves. A
channel explaining those subjects cannot be built on its library alone. Diagrams
can: they are generated, so supply is unlimited, and a drawn figure explains a
mechanism better than stock footage of a telescope anyway.

Each figure is animated. A still diagram held for ten seconds is dead screen
time; the same figure with its orbit turning or its curve drawing itself holds
attention for the same ten seconds at no extra cost.

The visual language is fixed in `brand.py` so every episode looks like the same
channel.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import brand as brand_mod
from .. import config, db, ffmpeg, paths

log = logging.getLogger(__name__)

FPS = 30
# Frames are drawn at half the timeline's resolution and doubled on encode.
# At 1920x1080 the difference is invisible under motion and halves the drawing
# time, which matters on a two-core machine rendering thirty videos a month.
RENDER_FPS = 15


@dataclass(slots=True)
class DiagramResult:
    drawn: int = 0
    ideas: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _figure(brand: brand_mod.Brand, width: int = 1920, height: int = 1080):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    figure.patch.set_facecolor(brand.palette["bg"])
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_facecolor(brand.palette["bg"])
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)
    return figure, axes


def _font(brand: brand_mod.Brand) -> dict[str, Any]:
    """Matplotlib needs a font with CJK coverage or labels come out as boxes."""
    path = brand.font_path or ffmpeg.find_font()
    from matplotlib import font_manager

    font_manager.fontManager.addfont(path)
    return {"fontname": font_manager.FontProperties(fname=path).get_name()}


# --- the figure kinds ---------------------------------------------------------


# Everything is drawn inside a 0..1 box with a band reserved at each end: the
# title at the top, and the bottom quarter left clear for the burned-in
# subtitles. A timeline's stage labels landed on top of the narration text
# before this was reserved.
TITLE_Y = 0.92
BODY_TOP = 0.84
BODY_BOTTOM = 0.30


def _title(axes, brand, label: str, font: dict) -> None:
    axes.text(0.5, TITLE_Y, label, color=brand.palette["ink"], fontsize=40,
              ha="center", va="center", **font)


def orbit_frames(axes, brand, progress: float, label: str, font: dict) -> None:
    """Two bodies spiralling toward a common centre. Orbits, binaries, mergers."""
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    _title(axes, brand, label, font)

    cx, cy = 0.5, (BODY_TOP + BODY_BOTTOM) / 2
    angle = progress * 4 * math.pi
    separation = 1.0 - 0.5 * progress

    # Unequal radii so it reads as two bodies orbiting a barycentre rather than
    # one object drawn twice.
    for radius, size, colour, phase in (
        (0.26, 900, brand.palette["accent"], 0.0),
        (0.15, 420, brand.palette["sub"], math.pi),
    ):
        rx, ry = radius * separation, radius * separation * 0.55
        theta = [i / 160 * 2 * math.pi for i in range(161)]
        axes.plot([cx + rx * math.cos(t) for t in theta],
                  [cy + ry * math.sin(t) for t in theta],
                  color=brand.palette["grid"], linewidth=1.2, zorder=1)
        axes.scatter([cx + rx * math.cos(angle + phase)],
                     [cy + ry * math.sin(angle + phase)],
                     s=size, color=colour, zorder=3)

    axes.scatter([cx], [cy], s=60, color=brand.palette["grid"], marker="+", zorder=2)


def scale_frames(axes, brand, progress: float, label: str, font: dict) -> None:
    """Nested circles for comparing sizes, revealed outward.

    Labels sit to the right of each circle rather than above it: stacked
    captions collide as soon as two radii are close.
    """
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    _title(axes, brand, label, font)

    # The axes are 0..1 in both directions over a 16:9 frame, so a circle needs
    # its vertical radius stretched by the aspect ratio. The largest tier is
    # sized so that stretch still clears the title.
    aspect = 16 / 9
    cx, cy = 0.40, (BODY_TOP + BODY_BOTTOM) / 2
    tiers = [(0.035, "地球"), (0.10, "太陽"), (0.19, "この天体")]
    for index, (radius, name) in enumerate(tiers):
        if progress < index / len(tiers):
            continue
        theta = [i / 180 * 2 * math.pi for i in range(181)]
        colour = brand.palette["accent"] if index == len(tiers) - 1 else brand.palette["sub"]
        axes.plot([cx + radius * math.cos(t) for t in theta],
                  [cy + radius * aspect * math.sin(t) for t in theta],
                  color=colour, linewidth=2.6)
        label_y = cy + 0.11 * (index - 1)
        axes.plot([cx + radius, 0.66], [cy, label_y], color=colour, linewidth=1.0, alpha=0.5)
        axes.text(0.68, label_y, name, color=colour, fontsize=26,
                  ha="left", va="center", **font)


def curve_frames(axes, brand, progress: float, label: str, font: dict) -> None:
    """A curve drawing itself. Light curves, spectra, decay, rotation curves."""
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    _title(axes, brand, label, font)

    left, right = 0.12, 0.88
    floor, ceiling = BODY_BOTTOM + 0.06, BODY_TOP - 0.06

    for fraction in (0.25, 0.5, 0.75):
        y = floor + (ceiling - floor) * fraction
        axes.plot([left, right], [y, y], color=brand.palette["grid"], linewidth=0.9, zorder=1)
    axes.plot([left, left], [floor, ceiling], color=brand.palette["grid"], linewidth=1.4, zorder=1)
    axes.plot([left, right], [floor, floor], color=brand.palette["grid"], linewidth=1.4, zorder=1)

    count = max(2, int(200 * progress))
    xs = [i / 199 for i in range(count)]
    points = [
        (
            left + (right - left) * x,
            floor + (ceiling - floor) * (0.5 + 0.42 * math.sin(x * 7) * math.exp(-x * 1.6)),
        )
        for x in xs
    ]
    axes.plot([p[0] for p in points], [p[1] for p in points],
              color=brand.palette["accent"], linewidth=3.4, zorder=3)
    if points:
        axes.scatter([points[-1][0]], [points[-1][1]], s=110,
                     color=brand.palette["ink"], zorder=4)


def timeline_frames(axes, brand, progress: float, label: str, font: dict) -> None:
    """A horizontal sequence of events, filling left to right."""
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    _title(axes, brand, label, font)

    left, right = 0.10, 0.90
    y = (BODY_TOP + BODY_BOTTOM) / 2
    marks = [left + (right - left) * i / 4 for i in range(5)]
    filled = left + (right - left) * progress

    axes.plot([left, right], [y, y], color=brand.palette["grid"], linewidth=4)
    axes.plot([left, filled], [y, y], color=brand.palette["accent"], linewidth=4, zorder=2)

    for index, x in enumerate(marks):
        reached = x <= filled
        axes.scatter([x], [y], s=220 if reached else 120,
                     color=brand.palette["accent"] if reached else brand.palette["grid"],
                     zorder=3)
        if reached:
            # Above the line: below it is where the subtitles sit.
            axes.text(x, y + 0.07, f"段階{index + 1}",
                      color=brand.palette["sub"], fontsize=24,
                      ha="center", va="bottom", **font)


KINDS = {
    "orbit": orbit_frames,
    "scale": scale_frames,
    "curve": curve_frames,
    "timeline": timeline_frames,
}

# Which figure suits which chapter. Deterministic so an episode's figures do not
# reshuffle when it is re-rendered.
CHAPTER_KIND = {
    "hook": "curve",
    "intro": "timeline",
    "basis": "scale",
    "main": "orbit",
    "detail": "curve",
    "meaning": "timeline",
    "outro": "scale",
}


def render(
    kind: str,
    label: str,
    seconds: float,
    output: Path,
    brand: brand_mod.Brand,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Draw one animated figure to an mp4."""
    import matplotlib.pyplot as plt

    draw = KINDS.get(kind, curve_frames)
    font = _font(brand)
    frames_dir = output.parent / f"{output.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    total = max(2, int(seconds * RENDER_FPS))
    # One figure, cleared between frames. Building a fresh 1920x1080 figure per
    # frame dominates the runtime, and thirty videos a month makes that matter.
    figure, axes = _figure(brand, width, height)
    try:
        for index in range(total):
            axes.clear()
            axes.set_facecolor(brand.palette["bg"])
            axes.set_xticks([])
            axes.set_yticks([])
            for spine in axes.spines.values():
                spine.set_visible(False)
            draw(axes, brand, index / (total - 1), label, font)
            figure.savefig(frames_dir / f"{index:05d}.png", facecolor=brand.palette["bg"])
        plt.close(figure)

        output.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg._run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(RENDER_FPS), "-i", str(frames_dir / "%05d.png"),
            "-vf", f"fps={FPS},scale={width}:{height}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(output),
        ])
    finally:
        for frame in frames_dir.glob("*.png"):
            frame.unlink(missing_ok=True)
        frames_dir.rmdir()

    return output


def run(limit: int = 1, idea_id: int | None = None) -> DiagramResult:
    """Render every reserved diagram slot."""
    settings = config.load_settings()
    video_cfg = settings.get("video", {})
    width = int(video_cfg.get("width", 1920))
    height = int(video_cfg.get("height", 1080))
    brand = brand_mod.load_brand()
    chapter_keys = [c.key for c in brand_mod.EPISODE_PLAN]

    result = DiagramResult()
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
            script_row = db.get_script(conn, current_id)
            chapters = json.loads(script_row["chapters_json"]) if script_row else []
            slots = db.unfilled_assets(conn, current_id, "diagram")

            if not slots:
                log.info("idea %d has no diagram slots to fill", current_id)
                result.ideas += 1
                continue

            drawn = 0
            for slot in slots:
                chapter_index = slot["chapter"] or 0
                key = chapter_keys[chapter_index] if chapter_index < len(chapter_keys) else "detail"
                title = (
                    chapters[chapter_index].get("title", "")
                    if chapter_index < len(chapters)
                    else ""
                )
                output = (
                    paths.DIAGRAMS_DIR
                    / f"idea_{current_id:05d}"
                    / f"{slot['chapter']:02d}_{slot['order_idx']:03d}.mp4"
                )
                try:
                    render(
                        CHAPTER_KIND.get(key, "curve"),
                        title,
                        float(slot["duration_s"]),
                        output,
                        brand,
                        width,
                        height,
                    )
                except (ffmpeg.FFmpegError, ffmpeg.FFmpegMissing, ffmpeg.FontMissing, OSError) as exc:
                    log.error("diagram failed for idea %d slot %d: %s", current_id, slot["id"], exc)
                    result.failed += 1
                    result.errors.append(f"idea {current_id} slot {slot['id']}: {exc}")
                    continue

                db.set_asset_path(
                    conn, int(slot["id"]), str(output),
                    credit=brand.channel_name, license_ok=True,
                    meta={**json.loads(slot["meta_json"] or "{}"), "figure": CHAPTER_KIND.get(key, "curve")},
                )
                drawn += 1

            conn.commit()
            result.ideas += 1
            result.drawn += drawn
            log.info("idea %d: %d/%d diagrams drawn", current_id, drawn, len(slots))

    return result
