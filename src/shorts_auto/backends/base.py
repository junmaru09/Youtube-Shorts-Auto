"""The contract every video backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import GeneratedVideo, VideoRequest


@runtime_checkable
class VideoBackend(Protocol):
    """Turn a prompt into an mp4 on disk.

    Implementations must report the *actual* cost they incurred so the budget
    guard and the break-even report stay honest.
    """

    name: str

    def generate(self, request: VideoRequest) -> GeneratedVideo:
        """Block until the video exists at `request.output_path`."""
        ...

    def estimate_cost(self, request: VideoRequest) -> float:
        """Pre-flight price so BudgetGuard can refuse before any money moves."""
        ...
