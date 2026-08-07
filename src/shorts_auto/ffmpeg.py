"""Thin ffmpeg wrapper.

Title text is passed via `textfile=` rather than inline: drawtext treats
colons, quotes and backslashes as syntax, and Japanese hooks routinely contain
characters that would otherwise need three levels of escaping.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Checked in order; the first that exists wins.
CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920


class FFmpegError(RuntimeError):
    """ffmpeg or ffprobe exited non-zero."""


class FFmpegMissing(RuntimeError):
    """ffmpeg is not installed."""


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise FFmpegMissing(
            "ffmpeg/ffprobe not found. Install with: sudo apt-get install -y ffmpeg fonts-noto-cjk"
        )


def find_font(configured: str | None = None) -> str:
    if configured and Path(configured).exists():
        return configured
    for candidate in CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FFmpegError(
        "no usable font found for title burn-in. Install fonts-noto-cjk or set "
        "postprocess.font_path in config/settings.yaml"
    )


def _run(cmd: list[str]) -> str:
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc.stdout


def probe(path: Path) -> dict:
    out = _run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
    )
    return json.loads(out)


def video_info(path: Path) -> dict:
    """Width, height, duration and whether an audio track exists."""
    data = probe(path)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise FFmpegError(f"{path} has no video stream")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": float(data["format"].get("duration", 0.0)),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def render_short(
    source: Path,
    output: Path,
    *,
    title_file: Path | None = None,
    font_path: str | None = None,
    font_size: int = 64,
    has_audio: bool = True,
    loudness_target: float = -14.0,
) -> Path:
    """Normalise to 1080x1920, level the audio, and burn the title on top.

    The burned title is what makes the ja and en renders byte-distinct, which
    is why the same source video can go to both channels without tripping
    YouTube's reused-content heuristics.
    """
    require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    filters = [
        f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={SHORTS_WIDTH}:{SHORTS_HEIGHT}",
    ]
    if title_file is not None:
        filters.append(
            "drawtext="
            f"fontfile='{_escape_filter_path(Path(font_path or find_font()))}'"
            f":textfile='{_escape_filter_path(title_file)}'"
            f":fontsize={font_size}"
            ":fontcolor=white"
            ":borderw=6:bordercolor=black@0.85"
            ":line_spacing=12"
            ":x=(w-text_w)/2"
            ":y=h*0.12"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]
    if has_audio:
        cmd += ["-af", f"loudnorm=I={loudness_target}:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd.append(str(output))

    _run(cmd)
    return output


def extract_thumbnail(source: Path, output: Path, at_seconds: float = 1.0) -> Path:
    require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(at_seconds),
            "-i", str(source),
            "-frames:v", "1",
            "-vf", f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={SHORTS_WIDTH}:{SHORTS_HEIGHT}",
            str(output),
        ]
    )
    return output
