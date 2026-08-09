"""Veo 3.1 / Gemini API backend.

Generation is a long-running operation: submit, poll, download. Google keeps the
result for two days, so the file is fetched immediately rather than lazily.

Two failure modes are distinguished because they differ in cost:
  - a safety block returns done-but-empty and is **not billed** (per Google's
    docs), so it raises SafetyBlocked and the caller records $0;
  - anything after the operation reports success may well have been billed, so it
    raises BilledFailure and the caller records the full price.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from ..models import GeneratedVideo, VideoRequest
from ..pricing import estimate_cost, validate_video_request

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10
# Google documents 11 seconds to 6 minutes; 15 gives peak-hour headroom.
POLL_TIMEOUT_SECONDS = 900


class SafetyBlocked(RuntimeError):
    """The model refused. Google does not charge for blocked generations."""


class BilledFailure(RuntimeError):
    """Failed after the point where the call was probably billed."""


class VeoBackend:
    name = "veo"

    def __init__(self, api_key: str | None = None) -> None:
        # Credentials are resolved lazily: pricing and budget checks must work
        # without them, so a spent budget reports "budget exhausted" rather than
        # a misleading "no API key".
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai

            key = self._api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in."
                )
            self._client = genai.Client(api_key=key)
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
            "person_generation": request.person_generation,
        }
        supported = set(types.GenerateVideosConfig.model_fields)
        dropped = sorted(k for k, v in wanted.items() if v is not None and k not in supported)
        if dropped:
            # Silently dropping a field still bills the full price while producing
            # something other than what was asked for — e.g. a 16:9 video billed
            # as a Short. Refuse rather than pay for the wrong thing.
            raise RuntimeError(
                f"installed google-genai does not support {dropped}. Upgrade the SDK, or "
                "remove these settings from config/settings.yaml, before generating."
            )
        kwargs = {k: v for k, v in wanted.items() if v is not None}
        return types.GenerateVideosConfig(**kwargs)

    def generate(self, request: VideoRequest) -> GeneratedVideo:
        if not request.output_path:
            raise ValueError("VideoRequest.output_path is required")
        validate_video_request(request.model, request.resolution, request.duration_seconds)

        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        config = self._build_config(request)

        log.info(
            "submitting %s (%ds, %s, %s)",
            request.model,
            request.duration_seconds,
            request.aspect_ratio,
            request.resolution,
        )
        operation = self.client.models.generate_videos(
            model=request.model, prompt=request.prompt, config=config
        )

        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while not operation.done:
            if time.monotonic() > deadline:
                raise BilledFailure(
                    f"Veo generation exceeded {POLL_TIMEOUT_SECONDS}s and is still running; "
                    "assuming it was billed"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
            operation = self.client.operations.get(operation)

        if getattr(operation, "error", None):
            raise SafetyBlocked(f"Veo generation failed: {operation.error}")

        videos = getattr(operation.response, "generated_videos", None)
        if not videos:
            # Done but empty is how a safety filter presents itself.
            raise SafetyBlocked(
                f"Veo returned no video (safety filter or audio processing). "
                f"Response: {operation.response}"
            )

        # Past this point the generation succeeded on Google's side, so any
        # failure here is a billed failure.
        try:
            generated = videos[0]
            self.client.files.download(file=generated.video)
            generated.video.save(str(output))
        except Exception as exc:
            raise BilledFailure(
                f"generation succeeded but the file could not be saved: {exc}. "
                "Google keeps results for 2 days; the spend has been recorded."
            ) from exc

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
                "negative_prompt": request.negative_prompt,
            },
        )
