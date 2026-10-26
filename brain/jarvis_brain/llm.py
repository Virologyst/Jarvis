"""The conversation with Claude, streamed so speech can start early."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

import anthropic

from .persona import SYSTEM_PROMPT

log = logging.getLogger(__name__)

REFUSAL_LINE = "Sorry, I can't help with that one."


class Conversation:
    """One running conversation per terminal.

    History is append-only (thinking blocks are sent back unchanged). After
    `idle_reset_minutes` without a turn, the next turn starts a fresh
    conversation instead of trimming old turns.
    """

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: str,
                 idle_reset_minutes: float):
        self.client = client
        self.model = model
        self.effort = effort
        self.idle_reset_s = idle_reset_minutes * 60
        self.messages: list[dict] = []
        self.last_turn = 0.0

    async def reply(self, user_text: str) -> AsyncIterator[str]:
        """Yield the reply as text pieces. Records the turn when complete."""
        now = time.monotonic()
        if self.messages and now - self.last_turn > self.idle_reset_s:
            log.info("conversation idle, starting fresh")
            self.messages = []
        self.last_turn = now

        self.messages.append({"role": "user", "content": user_text})
        try:
            async with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                # On a safety decline, let the API retry on its recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except BaseException:
            self.messages.pop()  # drop the unanswered turn (also on cancel/barge-in)
            raise

        if final.stop_reason == "refusal":
            log.warning("refusal: %s", getattr(final, "stop_details", None))
            self.messages.pop()
            yield " " + REFUSAL_LINE
            return

        self.messages.append({"role": "assistant", "content": final.content})
