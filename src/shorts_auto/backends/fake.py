"""Offline backend for tests and `--dry-run`. Writes a tiny placeholder file.

Costs are reported as zero so dry runs never move the budget needle.
"""

from __future__ import annotations

from pathlib import Path

from ..models import GeneratedVideo, VideoRequest
from ..pricing import estimate_cost


class FakeBackend:
    name = "fake"

    def estimate_cost(self, request: VideoRequest) -> float:
        # Real list price, so --dry-run still shows what a live run would cost.
        return estimate_cost(request.model, request.resolution, request.duration_seconds)

    def generate(self, request: VideoRequest) -> GeneratedVideo:
        if not request.output_path:
            raise ValueError("VideoRequest.output_path is required")
        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"FAKE_MP4\n" + request.prompt.encode("utf-8"))
        return GeneratedVideo(
            path=str(output),
            backend=self.name,
            model=request.model,
            duration_s=float(request.duration_seconds),
            cost_usd=0.0,
            meta={"fake": True, "would_have_cost_usd": self.estimate_cost(request)},
        )
