"""Idea generation via Claude.

The model is forced through a tool schema so the result is always parseable —
a free-text JSON blob fails often enough to matter when this runs unattended.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .models import SeriesConfig

log = logging.getLogger(__name__)

IDEA_TOOL = {
    "name": "submit_ideas",
    "description": "Submit the planned shorts for this series.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_summary": {
                            "type": "string",
                            "description": "One English sentence naming the concrete situation. This is what gets compared against past ideas for novelty, so be specific.",
                        },
                        "video_prompt_scene": {
                            "type": "string",
                            "description": "2-4 sentences of visual direction in English, filling the {scene} slot of the series prompt template. Describe subject, action, camera and setting. Do not mention text, captions or watermarks.",
                        },
                        "hook_ja": {
                            "type": "string",
                            "description": "Japanese title, under 40 characters, following one of the series title patterns.",
                        },
                        "hook_en": {
                            "type": "string",
                            "description": "English title, under 70 characters.",
                        },
                    },
                    "required": [
                        "scene_summary",
                        "video_prompt_scene",
                        "hook_ja",
                        "hook_en",
                    ],
                },
            }
        },
        "required": ["ideas"],
    },
}

SYSTEM_PROMPT = """You plan short vertical AI-generated videos for a YouTube Shorts channel.

Rules you must follow:
- Every idea must be a single continuous 8-second shot. No cuts, no scene changes, no story arc.
- The video has no narration and no on-screen text. The visual alone has to carry it.
- Novelty matters more than polish. YouTube demonetises channels whose uploads are
  template repetitions of each other, so each idea must be clearly distinct from the
  recent ideas you are shown - a different subject AND a different situation, not a
  reworded version of the same shot.
- Respect the series' banned list exactly. If a subject would require a banned element
  to work, pick a different subject.
- Write video_prompt_scene in English regardless of the title language."""


def _build_user_prompt(
    series: SeriesConfig,
    count: int,
    recent: list[str],
    rejections: list[str],
) -> str:
    parts = [
        f"Series: {series.id} — {series.description}",
        "",
        "Title patterns to follow (use the placeholders as inspiration, not literally):",
    ]
    for lang, patterns in series.title_patterns.items():
        for pattern in patterns:
            parts.append(f"  [{lang}] {pattern}")

    if series.subject_pool:
        parts += ["", "Subject pool for inspiration (you may go beyond it):"]
        parts += [f"  - {s}" for s in series.subject_pool]

    if series.banned:
        parts += ["", "BANNED — must not appear in any idea:"]
        parts += [f"  - {b}" for b in series.banned]

    if recent:
        parts += ["", f"Recent ideas in this series ({len(recent)}). Do NOT repeat or rephrase these:"]
        parts += [f"  - {s}" for s in recent]

    if rejections:
        parts += ["", "Recently rejected by the human reviewer, with reasons. Avoid these failure modes:"]
        parts += [f"  - {s}" for s in rejections]

    parts += ["", f"Produce exactly {count} idea(s)."]
    return "\n".join(parts)


class IdeaGenerator:
    def __init__(self, model: str, max_tokens: int = 4096, api_key: str | None = None) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate(
        self,
        series: SeriesConfig,
        count: int,
        recent: list[str] | None = None,
        rejections: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[IDEA_TOOL],
            tool_choice={"type": "tool", "name": "submit_ideas"},
            messages=[
                {
                    "role": "user",
                    "content": _build_user_prompt(
                        series, count, recent or [], rejections or []
                    ),
                }
            ],
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_ideas":
                ideas = block.input.get("ideas", [])
                log.debug("model returned %d ideas for %s", len(ideas), series.id)
                return ideas

        raise RuntimeError(
            f"model did not call submit_ideas; got: {json.dumps([b.type for b in response.content])}"
        )
