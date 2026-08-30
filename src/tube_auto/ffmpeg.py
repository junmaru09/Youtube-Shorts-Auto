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

# ---------------------------------------------------------------------------
# Video encoders
#
# Quality is expressed as one number on a CRF-like scale (lower is better) and
# translated per encoder, because only libx264 actually has CRF. The hardware
# encoders take a quantiser instead, and each spells it differently.
#
# VAAPI is deliberately absent. It is the right answer for AMD on Linux, but it
# needs `-vaapi_device` plus `hwupload` inside every filter graph — and this
# pipeline's graphs already carry subtitles, drawtext, zoompan and sidechain
# audio. Adding a hardware upload to each is a much larger change than swapping
# `-c:v`, so AMD on Linux stays on libx264 until that is worth doing.
# ---------------------------------------------------------------------------

def _x264(quality: int, preset: str) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(quality)]


def _nvenc(quality: int, preset: str) -> list[str]:
    # p1 fastest .. p7 slowest. p5 with `-tune hq` is the usual quality/speed
    # knee; `-b:v 0` is required or -cq is ignored and NVENC targets a bitrate.
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
            "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]


def _amf(quality: int, preset: str) -> list[str]:
    return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
            "-qp_i", str(quality), "-qp_p", str(quality)]


def _qsv(quality: int, preset: str) -> list[str]:
    return ["-c:v", "h264_qsv", "-preset", "slow", "-global_quality", str(quality)]


VIDEO_ENCODERS = {
    "libx264": _x264,
    "h264_nvenc": _nvenc,
    "h264_amf": _amf,
    "h264_qsv": _qsv,
}

# Hardware first, most-preferred first. libx264 is the floor and always works.
ENCODER_PREFERENCE = ("h264_nvenc", "h264_amf", "h264_qsv", "libx264")

_encoder_cache: str | None = None


def available_encoders() -> list[str]:
    """Encoders this ffmpeg was compiled with. Says nothing about the hardware."""
    try:
        listing = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)
    except (FFmpegError, FFmpegMissing):
        return ["libx264"]
    return [name for name in VIDEO_ENCODERS if f" {name} " in listing]


def encoder_works(name: str) -> bool:
    """Encode one real frame with it.

    Listing an encoder is not evidence the hardware exists — a stock ffmpeg
    build advertises `h264_nvenc` on a machine with no NVIDIA card at all, and
    the failure only appears once a render is already underway.
    """
    if name == "libx264":
        return True
    try:
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
            "-frames:v", "1", *VIDEO_ENCODERS[name](24, "veryfast"),
            "-pix_fmt", "yuv420p", "-f", "null", "-",
        ], timeout=60)
        return True
    except (FFmpegError, FFmpegMissing, KeyError):
        return False


def resolve_encoder(configured: str = "auto") -> str:
    """Which encoder to use. Probed once per process."""
    global _encoder_cache

    if configured and configured != "auto":
        if configured not in VIDEO_ENCODERS:
            raise FFmpegError(
                f"unknown video.encoder {configured!r}; "
                f"choose one of: auto, {', '.join(VIDEO_ENCODERS)}"
            )
        return configured

    if _encoder_cache is None:
        compiled = set(available_encoders())
        for candidate in ENCODER_PREFERENCE:
            if candidate in compiled and encoder_works(candidate):
                _encoder_cache = candidate
                break
        else:
            _encoder_cache = "libx264"
        if _encoder_cache != "libx264":
            log.info("using hardware encoder %s", _encoder_cache)
    return _encoder_cache


def reset_encoder_cache() -> None:
    global _encoder_cache
    _encoder_cache = None


def configured_encoder() -> str:
    """`video.encoder` from settings, or "auto" if the config cannot be read."""
    try:
        from . import config

        return str(config.load_settings().get("video", {}).get("encoder", "auto"))
    except Exception:  # noqa: BLE001 - a missing config must not stop a render
        return "auto"


def video_args(quality: int, preset: str = "veryfast", configured: str | None = None) -> list[str]:
    """The `-c:v` and quality flags for whichever encoder is in use.

    Reads the setting itself rather than taking it as a parameter, so the six
    call sites across assembly and diagrams do not each have to thread it down.
    """
    name = resolve_encoder(configured or configured_encoder())
    return VIDEO_ENCODERS[name](quality, preset)


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
        *video_args(20, "medium"),
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
