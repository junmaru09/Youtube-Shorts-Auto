"""Thin ffmpeg wrapper.

Two escaping decisions matter here:

- Title text is passed via `textfile=` rather than inline. drawtext treats colons,
  quotes and backslashes as syntax, and Japanese hooks routinely contain
  characters that would otherwise need three levels of escaping.
- `expansion=none` is set. drawtext expands `%{...}` sequences by default, so a
  title containing `%{` is silently rewritten — verified: `A%{eif:100:d}B` renders
  as `A100B`.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Fonts with CJK coverage, checked in order. Latin-only fonts are deliberately
# absent: falling back to one would burn tofu boxes into Japanese titles and
# nothing downstream would notice.
CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
)

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
DEFAULT_TIMEOUT_SECONDS = 600


class FFmpegError(RuntimeError):
    """ffmpeg or ffprobe exited non-zero, or timed out."""


class FFmpegMissing(RuntimeError):
    """ffmpeg is not installed."""


class FontMissing(RuntimeError):
    """No font with CJK coverage is available."""


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FFmpegMissing(
            f"{', '.join(missing)} not found. Install with: "
            "sudo apt-get install -y ffmpeg fonts-noto-cjk"
        )


def find_font(configured: str | None = None) -> str:
    if configured:
        if not Path(configured).exists():
            raise FontMissing(
                f"postprocess.font_path points at {configured}, which does not exist"
            )
        return configured
    for candidate in CJK_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FontMissing(
        "no font with CJK coverage found, so Japanese titles would render as empty "
        "boxes. Install one with: sudo apt-get install -y fonts-noto-cjk "
        "(or set postprocess.font_path in config/settings.yaml)"
    )


def _run(cmd: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    require_ffmpeg()
    log.debug("$ %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"{cmd[0]} exceeded {timeout}s and was killed") from None
    if proc.returncode != 0:
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc.stdout


def probe(path: Path) -> dict:
    out = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(path)],
        timeout=60,
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
    escaped = str(path).replace("\\", "/")
    for char in (":", "'", "[", "]", ","):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def render_short(
    source: Path,
    output: Path,
    *,
    title_file: Path | None = None,
    font_path: str | None = None,
    font_size: int = 64,
    has_audio: bool = True,
    loudness_target: float = -14.0,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Normalise to 1080x1920, level the audio, and burn the title on top."""
    require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)

    filters = [
        f"scale={SHORTS_WIDTH}:{SHORTS_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={SHORTS_WIDTH}:{SHORTS_HEIGHT}",
    ]
    if title_file is not None:
        filters.append(
            "drawtext="
            f"fontfile='{_escape_filter_path(Path(find_font(font_path)))}'"
            f":textfile='{_escape_filter_path(title_file)}'"
            ":expansion=none"  # do not interpret %{...} in operator-supplied text
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
        cmd += ["-af", f"loudnorm=I={loudness_target}:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd.append(str(output))

    _run(cmd, timeout=timeout)
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
        ],
        timeout=120,
    )
    return output
