"""List prices, so every call records what it actually cost.

Source: https://ai.google.dev/gemini-api/docs/pricing
Last verified: 2026-08-08 (see PRICES_VERIFIED_ON — `doctor` warns when stale)

This table calibrates the spending cap. A wrong entry does not just mis-report a
number: it scales the real ceiling by the same factor, so a price that is half
the truth doubles what the guard will let through. Re-check it when the docs move.
"""

from __future__ import annotations

PRICES_VERIFIED_ON = "2026-08-08"
PRICING_DOC_URL = "https://ai.google.dev/gemini-api/docs/pricing"

# model id -> resolution -> USD per second of output
VIDEO_PRICES_USD_PER_SECOND: dict[str, dict[str, float]] = {
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
    "veo-3.1-fast-generate-preview": {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    "veo-3.1-lite-generate-preview": {"720p": 0.05, "1080p": 0.08},
    # Omni Flash bills per token; 720p is ~5,792 tokens/s at $17.50/1M tokens.
    "gemini-omni-flash": {"720p": 0.10, "1080p": 0.10},
}

# Veo 3.1 accepts only these durations, and 1080p/4k require the full 8 seconds.
VALID_DURATIONS = (4, 6, 8)
EIGHT_SECOND_ONLY_RESOLUTIONS = ("1080p", "4k")

# Per-million-token prices for the ideation model.
LLM_PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}


class UnknownPriceError(KeyError):
    """Raised when a model/resolution pair has no listed price."""


class InvalidVideoRequest(ValueError):
    """The request would be rejected by the API, or is not what the caller meant."""


def price_per_second(model: str, resolution: str) -> float:
    try:
        return VIDEO_PRICES_USD_PER_SECOND[model][resolution]
    except KeyError as exc:
        raise UnknownPriceError(
            f"no price listed for model={model!r} resolution={resolution!r}; "
            f"add it to pricing.VIDEO_PRICES_USD_PER_SECOND after checking {PRICING_DOC_URL}"
        ) from exc


def estimate_cost(model: str, resolution: str, duration_seconds: float) -> float:
    return round(price_per_second(model, resolution) * duration_seconds, 4)


def validate_video_request(model: str, resolution: str, duration_seconds: int) -> None:
    """Reject combinations the API refuses, before spending the call.

    A rejected request wastes wall-clock and an operator's attention; catching it
    locally turns a six-minute round trip into an immediate, explicit error.
    """
    if duration_seconds not in VALID_DURATIONS:
        raise InvalidVideoRequest(
            f"duration_seconds must be one of {VALID_DURATIONS}, got {duration_seconds}"
        )
    if resolution in EIGHT_SECOND_ONLY_RESOLUTIONS and duration_seconds != 8:
        raise InvalidVideoRequest(
            f"{resolution} requires duration_seconds=8, got {duration_seconds}"
        )
    if resolution not in VIDEO_PRICES_USD_PER_SECOND.get(model, {}):
        raise InvalidVideoRequest(
            f"model {model!r} does not support resolution {resolution!r} "
            f"(supported: {sorted(VIDEO_PRICES_USD_PER_SECOND.get(model, {}))})"
        )


def estimate_llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = LLM_PRICES_USD_PER_MTOK.get(model)
    if prices is None:
        # Unknown model: charge the most expensive listed rate rather than zero,
        # so an unrecognised model cannot slip past the budget for free.
        prices = max(LLM_PRICES_USD_PER_MTOK.values(), key=lambda p: p["output"])
    return round(
        input_tokens / 1_000_000 * prices["input"] + output_tokens / 1_000_000 * prices["output"],
        6,
    )
