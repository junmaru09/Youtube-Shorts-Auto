"""List prices, so every call records what it actually cost.

Two things are billed here: the research and script calls, and generated concept
art. Narration is free inside Google's monthly character allowance and is tracked
separately in `db.tts_chars_since`, because going over does not fail, it starts
charging.

Sources: https://ai.google.dev/gemini-api/docs/pricing and Anthropic's pricing page.
Last verified: 2026-08-08 (see PRICES_VERIFIED_ON — `doctor` warns when stale)

This table calibrates the spending cap. A wrong entry does not just mis-report a
number: it scales the real ceiling by the same factor, so a price that is half
the truth doubles what the guard will let through. Re-check it when the docs move.
"""

from __future__ import annotations

PRICES_VERIFIED_ON = "2026-08-08"
PRICING_DOC_URL = "https://ai.google.dev/gemini-api/docs/pricing"

# model id -> USD per generated image.
#
# Billed as output tokens: gemini-2.5-flash-image emits 1,290 tokens per image at
# $30/1M, which is where $0.039 comes from. It is listed per image because that
# is the unit the pipeline can count before spending anything.
IMAGE_PRICES_USD: dict[str, float] = {
    "gemini-2.5-flash-image": 0.039,
    "gemini-2.0-flash-preview-image-generation": 0.039,
}

# Per-million-token prices for the ideation model.
LLM_PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}


class UnknownPriceError(KeyError):
    """Raised when a model/resolution pair has no listed price."""


def price_per_image(model: str) -> float:
    """What one generated image costs.

    An unlisted model is an error, not a free one. Defaulting to zero would let a
    model nobody priced spend the whole budget while the guard reported nothing.
    """
    try:
        return IMAGE_PRICES_USD[model]
    except KeyError as exc:
        raise UnknownPriceError(
            f"no price listed for image model {model!r}; add it to "
            f"pricing.IMAGE_PRICES_USD after checking {PRICING_DOC_URL}"
        ) from exc


def estimate_image_cost(model: str, count: int) -> float:
    """What a batch will cost, before any of it is spent."""
    if count < 0:
        raise ValueError(f"count must not be negative, got {count}")
    return round(price_per_image(model) * count, 4)


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
