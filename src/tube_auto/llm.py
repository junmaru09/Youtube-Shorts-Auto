"""Claude access, with the cost of every call reported back.

Calls are always forced through a tool schema. Free-text JSON fails often enough
to matter when this runs unattended, and a truncated tool call is detectable
(`stop_reason == "max_tokens"`) where a truncated JSON blob just looks like a
parse error.

Nothing here decides whether a call is affordable. The caller checks the budget
and records the returned cost, so a stage cannot spend without also logging it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .pricing import estimate_llm_cost

log = logging.getLogger(__name__)


class Truncated(RuntimeError):
    """The model hit max_tokens before finishing the tool call."""


class NoToolCall(RuntimeError):
    """The model answered without calling the tool it was given."""


@dataclass(slots=True)
class LLMResponse:
    payload: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""
    raw_text: str = ""

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


@dataclass(slots=True)
class LLMClient:
    model: str
    max_tokens: int = 32000
    api_key: str | None = None
    _client: Any = field(default=None, init=False, repr=False)

    @property
    def client(self):
        if self._client is None:
            import anthropic

            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
                )
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def call_tool(
        self,
        *,
        system: str,
        user: str,
        tool: dict[str, Any],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Run one forced tool call and return its input plus what it cost."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Streamed, because the script call asks for well over 16K output
        # tokens — display and spoken text for every line, plus the model's own
        # thinking, which counts against max_tokens on Sonnet 5 — and a
        # non-streaming request that size hits the SDK's HTTP timeout.
        with self.client.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        result = LLMResponse(
            payload={},
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_llm_cost(self.model, input_tokens, output_tokens),
            stop_reason=getattr(response, "stop_reason", "") or "",
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
                result.payload = dict(block.input)
                break
            if getattr(block, "type", None) == "text":
                result.raw_text = block.text

        if result.truncated:
            # A truncated tool call yields half-written arguments. Refuse rather
            # than let a partial script reach narration.
            raise Truncated(
                f"{self.model} hit max_tokens ({kwargs['max_tokens']}) before finishing "
                f"{tool['name']}. Raise llm.max_tokens, or ask for less in one call."
            )
        if not result.payload:
            raise NoToolCall(
                f"{self.model} did not call {tool['name']} "
                f"(stop_reason={result.stop_reason}). Text was: {result.raw_text[:200]}"
            )

        log.debug(
            "%s: %d in / %d out tokens, $%.4f",
            tool["name"], input_tokens, output_tokens, result.cost_usd,
        )
        return result
