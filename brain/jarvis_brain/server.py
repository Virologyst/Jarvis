"""WebSocket server: one session per connected terminal.

Turn pipeline (all streamed so Jarvis starts talking quickly):
  mic audio -> speech-to-text -> Claude (text pieces) -> sentences -> Piper -> audio out
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import anthropic
from aiohttp import WSMsgType, web

from jarvis_protocol import (
    AudioEnd, AudioStart, Decision, Error, Hello, ListenCancel, ListenEnd,
    ListenStart, ReplyText, State, Transcript, Welcome, decode, encode,
)

from .approvals import ApprovalGate
from .config import Settings
from .llm import Conversation
from .sentences import SentenceSplitter
from .stt import Transcriber
from .tts import Speaker

log = logging.getLogger(__name__)

AUDIO_CHUNK_BYTES = 8192   # size of speech frames sent to terminals
MAX_UTTERANCE_S = 30       # ignore audio beyond this per turn


class Brain:
    """Shared, expensive things: models, the Claude client, the approval gate."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stt = Transcriber(settings.stt_model, settings.stt_device)
        self.tts = Speaker(settings.voice)
        self.client = anthropic.AsyncAnthropic()
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.warning("No ANTHROPIC_API_KEY set (in .env or the environment). Jarvis can "
                        "hear and speak but can't answer until one is added, unless "
                        "`ant auth login` has been run for this account.")
        self.approvals = ApprovalGate()
        self.sessions: set[TerminalSession] = set()

    async def broadcast(self, msg) -> None:
        for s in list(self.sessions):
            await s.send(msg)


class TerminalSession:
    def __init__(self, brain: Brain, ws: web.WebSocketResponse):
        self.brain = brain
        self.ws = ws
        self.terminal_id = "unknown"
        s = brain.settings
        self.conversation = Conversation(brain.client, s.model, s.effort, s.idle_reset_minutes)
        self.listening = False
        self.audio = bytearray()
        self.turn: asyncio.Task | None = None

    async def send(self, msg) -> None:
        if not self.ws.closed:
            await self.ws.send_str(encode(msg))

    async def on_text(self, raw: str) -> None:
        msg = decode(raw)
        if isinstance(msg, Hello):
            self.terminal_id = msg.terminal_id
            log.info("terminal connected: %s", self.terminal_id)
            await self.send(Welcome())
            for card in self.brain.approvals.pending():
                await self.send(card)
        elif isinstance(msg, ListenStart):
            await self._cancel_turn()  # talking over Jarvis interrupts it
            self.listening, self.audio = True, bytearray()
            await self.send(State("listening"))
        elif isinstance(msg, ListenEnd):
            if self.listening:
                self.listening = False
                pcm, self.audio = bytes(self.audio), bytearray()
                self.turn = asyncio.create_task(self._run_turn(pcm))
        elif isinstance(msg, ListenCancel):
            self.listening, self.audio = False, bytearray()
            await self.send(State("idle"))
        elif isinstance(msg, Decision):
            result = await self.brain.approvals.decide(msg.card_id, msg.decision)
            await self._speak_text(result)

    def on_audio(self, data: bytes) -> None:
        if self.listening and len(self.audio) < MAX_UTTERANCE_S * 16_000 * 2:
            self.audio.extend(data)

    async def _cancel_turn(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.turn
            await self.send(AudioEnd())

    async def _run_turn(self, pcm: bytes) -> None:
        try:
            await self.send(State("thinking"))
            text = await asyncio.to_thread(self.brain.stt.transcribe, pcm)
            log.info("[%s] heard: %r", self.terminal_id, text)
            if not text:
                await self.send(State("idle"))
                return
            await self.send(Transcript(text))
            await self._speak_stream(self.conversation.reply(text))
        except asyncio.CancelledError:
            raise
        except anthropic.AuthenticationError as e:
            log.error("Claude rejected the API key: %s", e)
            await self.send(Error("Claude rejected the API key"))
            await self._speak_text("My Claude API key isn't working. Please check the brain's settings.")
        except anthropic.APIError as e:
            log.exception("Claude request failed")
            await self.send(Error(f"Claude request failed: {e}"))
            await self._speak_text("Sorry, I couldn't reach my brain just then.")
        except Exception as e:  # noqa: BLE001 - keep the session alive
            await self.send(Error(str(e)))
            if "authentication" in str(e).lower():
                log.error("no Claude credentials: add ANTHROPIC_API_KEY to .env")
                await self._speak_text("I don't have a Claude API key yet. Add one to the brain's settings file.")
            else:
                log.exception("turn failed")
                await self._speak_text("Sorry, something went wrong on my end.")
        finally:
            if not self.ws.closed:
                await self.send(State("idle"))

    async def _speak_text(self, text: str) -> None:
        async def once():
            yield text
        await self._speak_stream(once())

    async def _speak_stream(self, pieces) -> None:
        """Send reply text as it arrives and speak it sentence by sentence."""
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_worker(sentences))
        splitter = SentenceSplitter()
        try:
            async for piece in pieces:
                await self.send(ReplyText(piece))
                for sentence in splitter.feed(piece):
                    sentences.put_nowait(sentence)
            for sentence in splitter.flush():
                sentences.put_nowait(sentence)
            await self.send(ReplyText(done=True))
            sentences.put_nowait(None)
            await speaker
        finally:
            speaker.cancel()

    async def _speak_worker(self, sentences: asyncio.Queue) -> None:
        started = False
        try:
            while (sentence := await sentences.get()) is not None:
                pcm = await asyncio.to_thread(self.brain.tts.synthesize, sentence)
                if not started:
                    started = True
                    await self.send(State("speaking"))
                    await self.send(AudioStart(self.brain.tts.sample_rate))
                for i in range(0, len(pcm), AUDIO_CHUNK_BYTES):
                    await self.ws.send_bytes(pcm[i:i + AUDIO_CHUNK_BYTES])
        finally:
            if started and not self.ws.closed:
                await self.send(AudioEnd())


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    brain: Brain = request.app["brain"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)
    session = TerminalSession(brain, ws)
    brain.sessions.add(session)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await session.on_text(msg.data)
            elif msg.type == WSMsgType.BINARY:
                session.on_audio(msg.data)
    finally:
        brain.sessions.discard(session)
        await session._cancel_turn()
        log.info("terminal disconnected: %s", session.terminal_id)
    return ws


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "terminals": len(request.app["brain"].sessions)})


def build_app(settings: Settings) -> web.Application:
    app = web.Application()
    app["brain"] = Brain(settings)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    return app
