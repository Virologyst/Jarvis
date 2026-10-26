"""The conversation with Claude, streamed so speech can start early."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import anthropic

from .persona import SYSTEM_PROMPT

log = logging.getLogger(__name__)

REFUSAL_LINE = "Sorry, I can't help with that one."
MAX_TOOL_ROUNDS = 6   # tool calls per spoken turn, as a runaway guard


class Conversation:
    """One running conversation per terminal.

    History is append-only (thinking blocks are sent back unchanged). After
    `idle_reset_minutes` without a turn, the next turn starts a fresh
    conversation instead of trimming old turns.
    """

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: str,
                 idle_reset_minutes: float, tools: list[dict] | None = None,
                 run_tool: Callable[[str, dict], Awaitable[str]] | None = None):
        self.client = client
        self.model = model
        self.effort = effort
        self.idle_reset_s = idle_reset_minutes * 60
        self.tools = tools or []
        self.run_tool = run_tool
        self.messages: list[dict] = []
        self.last_turn = 0.0

    async def reply(self, user_text: str) -> AsyncIterator[str]:
        """Yield the reply as text pieces, running any tools Claude calls along the way.

        Records the turn when complete; on cancel or error the whole unfinished turn is dropped
        (the history before it is untouched, so it stays append-only).
        """
        now = time.monotonic()
        if self.messages and now - self.last_turn > self.idle_reset_s:
            log.info("conversation idle, starting fresh")
            self.messages = []
        self.last_turn = now

        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                extra = {"tools": self.tools} if self.tools else {}
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
                    **extra,
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final = await stream.get_final_message()

                if final.stop_reason == "refusal":
                    log.warning("refusal: %s", getattr(final, "stop_details", None))
                    del self.messages[turn_start:]
                    yield " " + REFUSAL_LINE
                    return

                self.messages.append({"role": "assistant", "content": final.content})
                if final.stop_reason != "tool_use":
                    return
                self.messages.append({"role": "user", "content": await self._run_tools(final)})
            log.warning("gave up after %d tool rounds", MAX_TOOL_ROUNDS)
        except BaseException:
            del self.messages[turn_start:]  # drop the unfinished turn (also on cancel/barge-in)
            raise

    async def _run_tools(self, message) -> list[dict]:
        """Run every tool call in the message; all results go back in one user message."""
        results = []
        for block in message.content:
            if block.type != "tool_use":
                continue
            result = {"type": "tool_result", "tool_use_id": block.id}
            try:
                if not self.run_tool:
                    raise RuntimeError("no tools available")
                result["content"] = await self.run_tool(block.name, dict(block.input or {}))
            except Exception as e:  # noqa: BLE001 - tell Claude, don't crash the turn
                log.warning("tool %s failed: %s", block.name, e)
                result["content"] = f"Error: {e}"
                result["is_error"] = True
            results.append(result)
        return results
