"""Measure a reference video the same way every time.

    python tools/analyze_reference.py "work/reference/some video.mp4"

Writes to work/reference/<slug>/:

    sheet_NN.png     timestamped contact sheets, one frame every 15 s, 5x4 per sheet
    stats.json       cut counts, change frequency, hold distribution, per-minute density
    summary.md       the numbers as prose, to paste into the design document

Two numbers matter most and are printed first:

- hard cuts: whole-frame changes (scene threshold 0.25)
- stage changes: changes inside the centre "stage" (excluding the sprite
  corners and the subtitle band), at a low threshold

A whiteboard-style video has few of the first and many of the second. A
montage has many of both. That ratio is the single clearest signature of the
format, and it comes out of the first reference video as 6 : 179.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

SAMPLE_EVERY = 15          # seconds between contact-sheet frames
HARD_CUT = 0.25            # scene threshold for a whole-frame change
STAGE_CHANGE = 0.03        # threshold inside the stage crop
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def probe(video: Path) -> tuple[float, int, int]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video),
    ], text=True)
    data = json.loads(out)
    stream = data["streams"][0]
    return float(data["format"]["duration"]), int(stream["width"]), int(stream["height"])


def scene_times(video: Path, vf: str) -> list[float]:
    err = run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", vf + ",showinfo", "-an", "-f", "null", "-"])
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", err)]


def contact_sheets(video: Path, out: Path, width: int, height: int) -> int:
    tile_w = 384
    tile_h = int(tile_w * height / width)
    vf = (
        f"fps=1/{SAMPLE_EVERY},"
        f"drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':x=8:y=8:fontsize=22:"
        f"fontcolor=yellow:box=1:boxcolor=black@0.6,"
        f"scale={tile_w}:{tile_h},tile=5x4:padding=4:color=0x222222"
    )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-vf", vf, str(out / "sheet_%02d.png"),
    ], check=True)
    return len(list(out.glob("sheet_*.png")))


def analyse(video: Path) -> dict:
    duration, width, height = probe(video)

    hard = scene_times(video, f"select='gt(scene,{HARD_CUT})'")

    # The stage: centre of the frame, excluding the bottom ~30% (subtitle band
    # and sprites) and a margin either side where sprites can intrude.
    sx, sy = int(width * 0.16), int(height * 0.03)
    sw, sh = int(width * 0.68), int(height * 0.66)
    stage = scene_times(video, f"crop={sw}:{sh}:{sx}:{sy},fps=1,select='gt(scene,{STAGE_CHANGE})'")

    holds = [b - a for a, b in zip(stage, stage[1:])] or [duration]
    per_minute = [0] * (int(duration // 60) + 1)
    for t in stage:
        per_minute[int(t // 60)] += 1

    return {
        "video": video.name,
        "duration_s": round(duration, 1),
        "resolution": f"{width}x{height}",
        "hard_cuts": len(hard),
        "hard_cut_times": [round(t, 1) for t in hard],
        "stage_changes": len(stage),
        "seconds_per_change": round(duration / max(1, len(stage)), 1),
        "hold_median_s": round(statistics.median(holds), 1),
        "hold_p90_s": round(sorted(holds)[int(len(holds) * 0.9)] if len(holds) > 1 else holds[0], 1),
        "hold_max_s": round(max(holds), 1),
        "changes_per_minute": per_minute,
        "quiet_minutes": [m for m, n in enumerate(per_minute) if n == 0],
    }


def summary(stats: dict, sheets: int) -> str:
    ratio = stats["stage_changes"] / max(1, stats["hard_cuts"])
    verdict = (
        "whiteboard (edited, not cut)" if ratio > 10
        else "mixed" if ratio > 3
        else "montage (cut, not edited)"
    )
    lines = [
        f"# {stats['video']}",
        "",
        f"- {stats['duration_s'] / 60:.1f} min, {stats['resolution']}, {sheets} contact sheets",
        f"- hard cuts: **{stats['hard_cuts']}**  ·  stage changes: **{stats['stage_changes']}**  → {verdict}",
        f"- one stage change every {stats['seconds_per_change']} s; "
        f"hold median {stats['hold_median_s']} s, p90 {stats['hold_p90_s']} s, max {stats['hold_max_s']} s",
        f"- hard cuts at: {', '.join(f'{t / 60:.1f}m' for t in stats['hard_cut_times']) or 'none'}"
        "  ← usually background swaps = section boundaries",
    ]
    if stats["quiet_minutes"]:
        lines.append(f"- minutes with no stage change: {stats['quiet_minutes']}  ← intro/outro, or long holds worth a look")
    lines += ["", "changes per minute:", "```",
              " ".join(f"{m:02d}:{n:2d}" for m, n in enumerate(stats["changes_per_minute"])), "```"]
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    video = Path(sys.argv[1])
    if not video.exists():
        print(f"not found: {video}")
        return 1

    slug = re.sub(r"[^\w\-]+", "_", video.stem)[:60].strip("_")
    out = Path("work/reference") / slug
    out.mkdir(parents=True, exist_ok=True)

    _, width, height = probe(video)
    sheets = contact_sheets(video, out, width, height)
    stats = analyse(video)
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    text = summary(stats, sheets)
    (out / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"sheets and stats in {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
