"""Per-second list prices so every generation records what it actually cost.

Source: Gemini API pricing page (checked 2026-08). Update alongside the docs —
`report` derives the break-even RPM from these numbers, so a stale table quietly
corrupts the investment decision this whole project exists to inform.
"""

from __future__ import annotations

# model id -> resolution -> USD per second of output
VIDEO_PRICES_USD_PER_SECOND: dict[str, dict[str, float]] = {
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
    "veo-3.1-fast-generate-preview": {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    "veo-3.1-lite-generate-preview": {"720p": 0.05, "1080p": 0.08},
    # Omni Flash bills per token; 720p is ~5,792 tokens/s at $17.50/1M tokens.
    "gemini-omni-flash": {"720p": 0.10, "1080p": 0.10},
}


class UnknownPriceError(KeyError):
    """Raised when a model/resolution pair has no listed price."""


def price_per_second(model: str, resolution: str) -> float:
    try:
        return VIDEO_PRICES_USD_PER_SECOND[model][resolution]
    except KeyError as exc:
        raise UnknownPriceError(
            f"no price listed for model={model!r} resolution={resolution!r}; "
            "add it to pricing.VIDEO_PRICES_USD_PER_SECOND"
        ) from exc


def estimate_cost(model: str, resolution: str, duration_seconds: float) -> float:
    return round(price_per_second(model, resolution) * duration_seconds, 4)
