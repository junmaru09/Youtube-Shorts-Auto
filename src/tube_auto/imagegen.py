"""Concept art for the ideas NASA has no photograph of.

Theory episodes are the ones the image library cannot serve — 51 items for black
holes, 3 for gravitational waves — and the matplotlib figures that fill the gap
are quantitative. They are good at showing an orbit or a light curve and useless
at showing "what the inside of an event horizon might be like". That is the hole
this fills.

Two rules are baked into every prompt and are not caller-adjustable:

- **No text.** Generated lettering is garbled, and garbled lettering on screen is
  the single most obvious sign of an automated channel.
- **No logos, insignia or recognisable people.** The rights work done on NASA
  material would be pointless if the generated frames reintroduced exactly the
  things it excludes.

Calls go over plain HTTPS rather than through a vendor SDK, because this is one
POST and the pipeline already talks to NASA the same way. One dependency fewer.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 120

# Appended to every prompt. See the module docstring.
CONSTRAINTS = (
    "Digital illustration, not a photograph. "
    "No text, no letters, no numbers, no captions, no watermarks. "
    "No logos, no insignia, no agency badges. "
    "No people, no faces, no hands. "
    "Cinematic, wide 16:9 composition with the subject off-centre, "
    "deep space background, high contrast, no borders or frames."
)


class ImageGenError(RuntimeError):
    """The image could not be generated."""


class ImageGenUnavailable(ImageGenError):
    """No API key, so image generation cannot be attempted at all."""


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def build_prompt(subject: str, palette: dict[str, str], style: str = "") -> str:
    """Turn a chapter's subject into a prompt.

    The channel palette goes in so generated frames sit next to the matplotlib
    figures without looking pasted in from somewhere else.
    """
    parts = [subject.strip() or "deep space"]
    if style:
        parts.append(style.strip())
    parts.append(
        f"Colour palette dominated by {palette.get('bg', '#05070F')} background "
        f"with {palette.get('accent', '#FFD34D')} highlights."
    )
    parts.append(CONSTRAINTS)
    return " ".join(parts)


def generate(prompt: str, model: str, destination: Path) -> Path:
    """Generate one image and write it out. Returns the path."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ImageGenUnavailable(
            "GEMINI_API_KEY is not set, so concept art cannot be generated. "
            "Either set it in .env or turn images.enabled off in config/settings.yaml."
        )

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise ImageGenError(f"image API returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ImageGenError(f"image API unreachable: {exc}") from exc

    data = _first_image(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


def _first_image(payload: dict) -> bytes:
    """Pull the image out of the response.

    A refusal comes back as a normal 200 with text instead of an image, so the
    absence of image data is reported as itself rather than as a parse error.
    """
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])

    reason = ""
    for candidate in payload.get("candidates", []):
        reason = candidate.get("finishReason") or reason
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("text"):
                reason = f"{reason} {part['text'][:200]}".strip()
    raise ImageGenError(f"response contained no image ({reason or 'no reason given'})")
