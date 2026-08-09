"""Offline backend for tests and --dry-run.

Writes a real (tiny) mp4 via ffmpeg when available so downstream stages can be
exercised end to end, and falls back to a placeholder file when it is not.
Reports zero cost so dry runs never move the budget needle, while still
surfacing the list price the same request would have incurred.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..models import GeneratedVideo, VideoRequest
from ..pricing import estimate_cost, validate_video_request


class FakeBackend:
    name = "fake"

    def estimate_cost(self, request: VideoRequest) -> float:
        # The real list price, so --dry-run still shows what a live run would cost.
        return estimate_cost(request.model, request.resolution, request.duration_seconds)

    def generate(self, request: VideoRequest) -> GeneratedVideo:
        if not request.output_path:
            raise ValueError("VideoRequest.output_path is required")
        validate_video_request(request.model, request.resolution, request.duration_seconds)

        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        real_media = self._write_placeholder_video(request, output)

        return GeneratedVideo(
            path=str(output),
            backend=self.name,
            model=request.model,
            duration_s=float(request.duration_seconds),
            cost_usd=0.0,
            meta={
                "fake": True,
                "playable": real_media,
                "would_have_cost_usd": self.estimate_cost(request),
            },
        )

    def _write_placeholder_video(self, request: VideoRequest, output: Path) -> bool:
        if shutil.which("ffmpeg") is None:
            output.write_bytes(b"FAKE_MP4\n" + request.prompt.encode("utf-8"))
            return False

        width, height = (720, 1280) if request.aspect_ratio == "9:16" else (1280, 720)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"testsrc=size={width}x{height}:rate=30:duration={request.duration_seconds}",
        ]
        if request.generate_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={request.duration_seconds}",
                    "-c:a", "aac"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)]

        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            output.write_bytes(b"FAKE_MP4\n" + request.prompt.encode("utf-8"))
            return False
        return True
