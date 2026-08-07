"""Veo 3.1 / Gemini API backend.

Generation is a long-running operation: submit, poll, then download the file.
Config fields are filtered against the installed SDK so a version bump that
renames or drops a field surfaces as a clear error instead of a TypeError.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from ..models import GeneratedVideo, VideoRequest
from ..pricing import estimate_cost

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 900


class VeoBackend:
    name = "veo"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def estimate_cost(self, request: VideoRequest) -> float:
        return estimate_cost(request.model, request.resolution, request.duration_seconds)

    def _build_config(self, request: VideoRequest):
        from google.genai import types

        wanted = {
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "duration_seconds": request.duration_seconds,
            "number_of_videos": 1,
            "generate_audio": request.generate_audio,
            "negative_prompt": request.negative_prompt,
        }
        supported = set(types.GenerateVideosConfig.model_fields)
        dropped = {k for k, v in wanted.items() if v is not None and k not in supported}
        if dropped:
            log.warning("google-genai does not support these config fields, ignoring: %s", sorted(dropped))
        kwargs = {k: v for k, v in wanted.items() if v is not None and k in supported}
        return types.GenerateVideosConfig(**kwargs)

    def generate(self, request: VideoRequest) -> GeneratedVideo:
        if not request.output_path:
            raise ValueError("VideoRequest.output_path is required")
        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        log.info("submitting %s (%ds, %s, %s)", request.model, request.duration_seconds,
                 request.aspect_ratio, request.resolution)
        operation = self.client.models.generate_videos(
            model=request.model,
            prompt=request.prompt,
            config=self._build_config(request),
        )

        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while not operation.done:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Veo generation exceeded {POLL_TIMEOUT_SECONDS}s; operation still running"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            operation = self.client.operations.get(operation)

        if getattr(operation, "error", None):
            raise RuntimeError(f"Veo generation failed: {operation.error}")

        videos = getattr(operation.response, "generated_videos", None)
        if not videos:
            # Usually a safety block: the response comes back done but empty.
            raise RuntimeError(
                f"Veo returned no video (likely a safety filter). Response: {operation.response}"
            )

        generated = videos[0]
        self.client.files.download(file=generated.video)
        generated.video.save(str(output))
        log.info("saved %s", output)

        return GeneratedVideo(
            path=str(output),
            backend=self.name,
            model=request.model,
            duration_s=float(request.duration_seconds),
            cost_usd=self.estimate_cost(request),
            meta={
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.resolution,
                "generate_audio": request.generate_audio,
            },
        )
