"""Idea generation via Claude.

The model is forced through a tool schema so the result always parses — a
free-text JSON blob fails often enough to matter when this runs unattended.

Fields mirror the five-part structure Google's Veo prompting guide recommends
(cinematography, subject, action, context, style) plus explicit audio direction.
Cinematography is separated out because the guide identifies it as the single
most effective lever, and because a blob of prose gives no way to control it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .models import SeriesConfig
from .pricing import estimate_llm_cost

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
                            "description": "One English sentence naming the concrete situation. This is compared against past ideas for novelty, so be specific about subject and situation.",
                        },
                        "cinematography": {
                            "type": "string",
                            "description": "Shot type and camera movement, e.g. 'Extreme macro close-up, slow dolly forward' or 'Low-angle handheld tracking shot'. This is the strongest lever on how the result feels, so be deliberate.",
                        },
                        "subject": {
                            "type": "string",
                            "description": "The main subject with concrete physical detail.",
                        },
                        "action": {
                            "type": "string",
                            "description": "What the subject does across the 8 seconds. One continuous action, no cuts.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Where it happens, including light sources and background detail.",
                        },
                        "audio": {
                            "type": "string",
                            "description": "Ambient noise and sound effects only. Never dialogue, narration or speech. Example: 'Ambient noise: faint soil crumbling. SFX: mandibles scraping on leaf.'",
                        },
                        "hook": {
                            "type": "string",
                            "description": "The video title in the requested language, following one of the series title patterns.",
                        },
                    },
                    "required": [
                        "scene_summary",
                        "cinematography",
                        "subject",
                        "action",
                        "context",
                        "audio",
                        "hook",
                    ],
                },
            }
        },
        "required": ["ideas"],
    },
}

SYSTEM_PROMPT = """You plan short vertical AI-generated videos for a YouTube Shorts channel.

Hard constraints:
- Every idea is a single continuous 8-second shot. No cuts, no scene changes, no story arc.
- There is no narration and no on-screen text. The visual and its ambient sound carry it alone.
- The audio field must contain only ambient sound and effects. Any dialogue, narration or
  speech would tie the video to one language and make it unusable.
- Novelty matters more than polish. YouTube demonetises channels whose uploads are template
  repetitions of each other, so each idea must differ from the recent ideas you are shown in
  BOTH subject and situation - not a reworded version of the same shot.
- Respect the series' banned list exactly. If a subject would need a banned element to work,
  pick a different subject.
- Titles must not claim the footage is real or that you personally filmed it. The video is
  entirely generated; a title asserting otherwise is misleading metadata.
- Write every field except the title in English."""


@dataclass(slots=True)
class IdeaBatch:
    """What the model returned, plus what it cost."""

    ideas: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    truncated: bool = False


def _build_user_prompt(
    series: SeriesConfig,
    lang: str,
    count: int,
    recent: list[str],
    rejections: list[str],
) -> str:
    parts = [
        f"Series: {series.id} — {series.description}",
        f"Title language: {lang}",
        "",
        "Title patterns to follow (placeholders are inspiration, not literal):",
    ]
    parts += [f"  {pattern}" for pattern in series.title_patterns.get(lang, [])]

    if series.subject_pool:
        parts += ["", "Subject pool for inspiration (you may go beyond it):"]
        parts += [f"  - {subject}" for subject in series.subject_pool]

    if series.banned:
        parts += ["", "BANNED — must not appear in any idea:"]
        parts += [f"  - {item}" for item in series.banned]

    if recent:
        parts += ["", f"Recent ideas in this series ({len(recent)}). Do NOT repeat or rephrase:"]
        parts += [f"  - {scene}" for scene in recent]

    if rejections:
        parts += ["", "Recently rejected by the human reviewer. Avoid these failure modes:"]
        parts += [f"  - {item}" for item in rejections]

    parts += ["", f"Produce exactly {count} idea(s)."]
    return "\n".join(parts)


class IdeaGenerator:
    def __init__(self, model: str, max_tokens: int = 4096, api_key: str | None = None) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
                )
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def generate(
        self,
        series: SeriesConfig,
        lang: str,
        count: int,
        recent: list[str] | None = None,
        rejections: list[str] | None = None,
    ) -> IdeaBatch:
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
                        series, lang, count, recent or [], rejections or []
                    ),
                }
            ],
        )

        usage = getattr(response, "usage", None)
        batch = IdeaBatch(
            model=self.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            truncated=response.stop_reason == "max_tokens",
        )
        batch.cost_usd = estimate_llm_cost(self.model, batch.input_tokens, batch.output_tokens)

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_ideas":
                batch.ideas = block.input.get("ideas", [])
                break

        if batch.truncated:
            # A truncated tool call yields a partial final idea. Drop it rather than
            # let a half-written prompt reach a paid video call.
            log.warning(
                "ideation hit max_tokens (%d) for %s/%s; dropping the last, possibly "
                "incomplete idea. Raise llm.max_tokens or lower the per-call count.",
                self.max_tokens, series.id, lang,
            )
            batch.ideas = batch.ideas[:-1]

        if not batch.ideas and not batch.truncated:
            raise RuntimeError(
                f"model did not return any ideas for {series.id}/{lang} "
                f"(stop_reason={response.stop_reason})"
            )
        log.debug("model returned %d ideas for %s/%s", len(batch.ideas), series.id, lang)
        return batch


def build_video_prompt(series: SeriesConfig, idea: dict[str, Any]) -> str:
    """Assemble the prompt in the order Google's Veo guide recommends.

    Negations are deliberately absent: the guide warns that vague negations in the
    positive prompt backfire. Everything that must not appear goes in the
    series' negative_prompt instead.
    """
    return series.prompt_template.format(
        cinematography=idea["cinematography"].strip().rstrip("."),
        subject=idea["subject"].strip().rstrip("."),
        action=idea["action"].strip().rstrip("."),
        context=idea["context"].strip().rstrip("."),
        style=series.style.strip(),
        audio=idea["audio"].strip(),
    )
