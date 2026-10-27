"""WebSocket server: one session per connected terminal.

Turn pipeline (all streamed so Jarvis starts talking quickly):
  mic audio -> speech-to-text -> Claude (text pieces) -> sentences -> text-to-speech -> audio out
  (or typed text/pictures from the screen straight into Claude)

Each sentence starts with a mood tag ([amused] ...) that becomes an Emote message timed to its
audio; tags are stripped from everything shown or spoken. A short spoken "approve"/"reject" is
handled here in code and never reaches Claude, and so is switching permanent listening on or
off ("listen to me from now on" / "stop listening"); while it's on, noise and scraps are dropped.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import uuid

import anthropic
from aiohttp import WSMsgType, web

from jarvis_protocol import (
    AudioEnd, AudioStart, Card, CardDone, Decision, Emote, Error, Hello, ListenCancel, ListenEnd,
    ListenMode, ListenStart, ReplyText, SetTheme, State, Transcript, UserInput, WakeCheck, WakeResult,
    Welcome, decode, encode,
)

from .approvals import ApprovalGate
from .config import REPO_ROOT, Settings
from .emotions import TagStripper, parse_sentence
from .listenmode import addressed, is_noise, listen_mode_command
from .llm import Conversation
from .selfupgrade import UpgradeManager, take_rollback_note
from .sentences import SentenceSplitter
from .stt import Transcriber
from .themes import DEFAULT_THEME, THEMES, ThemeStore, resolve
from .tasks import TaskStore
from .vault import Vault
from .tools import TOOLS, ToolBox
from .tts import make_speaker
from .voicecommands import parse_voice_decision, pick_card
from .wakecheck import heard_wake_phrase

log = logging.getLogger(__name__)

AUDIO_CHUNK_BYTES = 8192   # size of speech frames sent to terminals
MAX_UTTERANCE_S = 30       # ignore audio beyond this per turn
MAX_IMAGES = 5             # pictures per typed message
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
WS_MAX_BYTES = 32 * 1024 * 1024


class Brain:
    """Shared, expensive things: models, the Claude client, the approval gate."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stt = Transcriber(settings.stt_model, settings.stt_device)
        self.tts = make_speaker(settings.voice, settings.voice_speed, settings.voices_dir)
        self.client = anthropic.AsyncAnthropic()
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.warning("No ANTHROPIC_API_KEY set (in .env or the environment). Jarvis can "
                        "hear and speak but can't answer until one is added, unless "
                        "`ant auth login` has been run for this account.")
        # Cards are saved here so they survive a restart (see approvals.py)
        self.approvals = ApprovalGate(store=REPO_ROOT / "data" / "approvals.json")
        self.sessions: set[TerminalSession] = set()
        self.upgrades = UpgradeManager(REPO_ROOT, self.approvals, self.announce)
        self.approvals.restore()   # after every owner of saved cards has registered
        self.themes = ThemeStore(REPO_ROOT / "data" / "themes.json")
        self.tasks = TaskStore(REPO_ROOT / "data" / "tasks.json")   # shared by every terminal
        self.vault = Vault.from_env()   # Sir's Obsidian notes (JARVIS_VAULT), if set
        log.info("notes vault: %s", self.vault.root or "not set (JARVIS_VAULT)")
        # Said once to the first terminal that connects: (card title, what to say)
        rolled_back = take_rollback_note(REPO_ROOT)
        self.notice: tuple[str, str] | None = ("Upgrade rolled back", rolled_back) if rolled_back else None

    async def on_startup(self, app: web.Application) -> None:
        """Pick up an upgrade a restart interrupted (needs the event loop, so not in __init__)."""
        resumed = await self.upgrades.resume()
        if resumed and not self.notice:
            self.notice = ("Self-upgrade", resumed[0].upper() + resumed[1:])

    async def decide(self, card_id: str, decision: str) -> str:
        """Approve/reject a card (from a tap or a spoken word) and take it down on every screen."""
        result = await self.approvals.decide(card_id, decision)
        await self.broadcast(CardDone(card_id))
        return result

    async def broadcast(self, msg) -> None:
        for s in list(self.sessions):
            await s.send(msg)

    async def announce(self, text: str, card: Card | None = None) -> None:
        """Tell every terminal something unprompted: show the card, and say it if they're free."""
        for s in list(self.sessions):
            if card:
                await s.send(card)
            if not s.busy:
                asyncio.create_task(s.speak_aside(text))


class TerminalSession:
    def __init__(self, brain: Brain, ws: web.WebSocketResponse):
        self.brain = brain
        self.ws = ws
        self.terminal_id = "unknown"
        self.theme = THEMES[DEFAULT_THEME]
        s = brain.settings
        tools = ToolBox(brain.upgrades, self.send, current_theme=lambda: self.theme.title,
                        set_theme=self.set_theme, tasks=brain.tasks, vault=brain.vault)
        self.conversation = Conversation(brain.client, s.model, s.effort, s.idle_reset_minutes,
                                         tools=TOOLS, run_tool=tools.run)
        self.listening = False
        self.listen_reason = ""
        self.always_listening = False   # permanent listening, per terminal; off at startup
        self.audio = bytearray()
        self.turn: asyncio.Task | None = None

    async def set_theme(self, name: str, announce: bool = True) -> str:
        theme = resolve(name)
        if theme is None:
            raise ValueError(f"no theme called {name!r}; options: {', '.join(THEMES)}")
        self.theme = theme
        self.brain.themes.set(self.terminal_id, theme)
        await self.send(SetTheme(theme.name))
        log.info("[%s] theme -> %s", self.terminal_id, theme.name)
        voice = " It has its own voice, so you'll hear a change." if theme.voice else ""
        return f"Switched this screen to the {theme.title} theme.{voice}"

    async def set_listen_mode(self, on: bool) -> str:
        """Permanent listening on/off for this terminal. Returns the spoken confirmation."""
        self.always_listening = on
        await self.send(ListenMode(on))
        log.info("[%s] permanent listening %s", self.terminal_id, "on" if on else "off")
        if on:
            return "[neutral] Very well, Sir. I'm listening until you tell me to stop."
        return "[neutral] Understood, Sir. I'll wait for my name again."

    async def send(self, msg) -> None:
        if not self.ws.closed:
            await self.ws.send_str(encode(msg))

    @property
    def busy(self) -> bool:
        return self.listening or bool(self.turn and not self.turn.done())

    async def speak_aside(self, text: str) -> None:
        """Say something outside a turn (approval results, announcements), then go idle."""
        await self._speak_text(text)
        await self.send(State("idle"))

    async def on_text(self, raw: str) -> None:
        msg = decode(raw)
        if isinstance(msg, Hello):
            self.terminal_id = msg.terminal_id
            log.info("terminal connected: %s", self.terminal_id)
            await self.send(Welcome())
            self.theme = self.brain.themes.get(self.terminal_id)
            await self.send(SetTheme(self.theme.name))
            for card in self.brain.approvals.pending():
                await self.send(card)
            if self.brain.notice:
                (title, notice), self.brain.notice = self.brain.notice, None
                await self.send(Card(card_id=uuid.uuid4().hex, kind="info", title=title, body=notice))
                asyncio.create_task(self.speak_aside("Sir, " + notice[0].lower() + notice[1:]))
        elif isinstance(msg, ListenStart):
            await self._cancel_turn()  # talking over Jarvis interrupts it
            self.listening, self.audio = True, bytearray()
            self.listen_reason = msg.reason
            await self.send(State("listening"))
        elif isinstance(msg, ListenEnd):
            if self.listening:
                self.listening = False
                pcm, self.audio = bytes(self.audio), bytearray()
                self.turn = asyncio.create_task(self._run_turn(pcm))
        elif isinstance(msg, ListenCancel):
            self.listening, self.audio = False, bytearray()
            await self.send(State("idle"))
        elif isinstance(msg, WakeCheck):
            asyncio.create_task(self._wake_check(msg))
        elif isinstance(msg, Decision):
            result = await self.brain.decide(msg.card_id, msg.decision)
            await self.speak_aside(result)
        elif isinstance(msg, UserInput):
            await self._cancel_turn()
            self.listening, self.audio = False, bytearray()
            self.turn = asyncio.create_task(self._run_typed_turn(msg))
        elif isinstance(msg, SetTheme):
            with contextlib.suppress(ValueError):   # picked on the screen: switch, quietly
                await self.set_theme(msg.theme)
        elif isinstance(msg, ListenMode):
            # The toggle on the screen (say so), or a reconnecting terminal (quietly)
            said = await self.set_listen_mode(msg.on)
            if not msg.quiet and not self.busy:
                asyncio.create_task(self.speak_aside(said))

    def on_audio(self, data: bytes) -> None:
        if self.listening and len(self.audio) < MAX_UTTERANCE_S * 16_000 * 2:
            self.audio.extend(data)

    async def _wake_check(self, msg: WakeCheck) -> None:
        """Speech-to-text second opinion on a borderline wake word."""
        try:
            pcm = base64.b64decode(msg.audio_b64)
            text = await asyncio.to_thread(self.brain.stt.transcribe, pcm)
            ok = heard_wake_phrase(text)
        except Exception as e:  # noqa: BLE001 - a failed check just means "not woken"
            log.warning("wake check failed: %s", e)
            text, ok = "", False
        log.info("[%s] wake check: %r -> %s", self.terminal_id, text, "wake" if ok else "ignore")
        await self.send(WakeResult(msg.check_id, ok, text))

    async def _cancel_turn(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.turn
            await self.send(AudioEnd())

    async def _run_turn(self, pcm: bytes) -> None:
        # Opened by permanent listening (any voice), not by "hey Jarvis" or a tap
        ambient = self.listen_reason == "permanent"

        async def spoken():
            await self.send(State("thinking"))
            text = await asyncio.to_thread(self.brain.stt.transcribe, pcm)
            log.info("[%s] heard: %r", self.terminal_id, text)
            if not text:
                return
            if ambient and listen_mode_command(text) is None and is_noise(text):
                log.info("[%s] ignored as noise: %r", self.terminal_id, text)
                return
            await self.send(Transcript(text))
            if await self._voice_command(text, ambient):
                return
            await self._speak_stream(self.conversation.reply(text))
        await self._guarded(spoken())

    async def _run_typed_turn(self, msg: UserInput) -> None:
        async def typed():
            await self.send(State("thinking"))
            text = msg.text.strip()
            images = [i for i in msg.images if i.get("media_type") in IMAGE_TYPES and i.get("data")]
            images = images[:MAX_IMAGES]
            log.info("[%s] typed: %r (+%d pictures)", self.terminal_id, text, len(images))
            if not images and await self._voice_command(text):
                return
            if not text and not images:
                return
            content = [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"],
                                                    "data": i["data"]}} for i in images]
            content.append({"type": "text", "text": (text or "Have a look at this.") +
                            "\n\n(Typed on the screen, not spoken.)"})
            await self._speak_stream(self.conversation.reply(content))
        await self._guarded(typed())

    async def _voice_command(self, text: str, ambient: bool = False) -> bool:
        """Spoken switches, decided here in code: permanent listening on/off, and 'approve' /
        'reject' for the newest waiting card (or the newest one about what they named:
        'approve the upgrade')."""
        mode = listen_mode_command(text)
        if mode is not None:
            await self._speak_text(await self.set_listen_mode(mode))
            return True
        parsed = parse_voice_decision(text)
        if not parsed:
            return False
        decision, target = parsed
        if ambient and not addressed(text):
            # With the mic always open, a stray "approve" from across the room mustn't count
            await self._speak_text('[neutral] While I\'m always listening, please say "Jarvis, '
                                   'approve", or tap the card, Sir.')
            return True
        pending = self.brain.approvals.pending()
        card = pick_card(pending, target)
        if card is None:
            what = f"no {target}" if target and pending else "nothing"
            await self._speak_text(f"[neutral] There's {what} waiting for approval, Sir.")
            return True
        log.info("[%s] %s by voice: %s", self.terminal_id, decision, card.title)
        result = await self.brain.decide(card.card_id, decision)
        await self._speak_text(result)
        return True

    async def _guarded(self, turn) -> None:
        """Run a turn, turning failures into something Jarvis says instead of a crash."""
        try:
            await turn
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
        """Send reply text as it arrives and speak it sentence by sentence.

        Mood tags are stripped from the text shown on screen; each spoken sentence's tag becomes
        an Emote sent just before its audio.
        """
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_worker(sentences))
        splitter = SentenceSplitter()
        shown = TagStripper()
        try:
            async for piece in pieces:
                if text := shown.feed(piece):
                    await self.send(ReplyText(text))
                for sentence in splitter.feed(piece):
                    sentences.put_nowait(sentence)
            if text := shown.flush():
                await self.send(ReplyText(text))
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
                emotion, clean = parse_sentence(sentence)
                if not any(c.isalnum() for c in clean):
                    continue
                pcm = await asyncio.to_thread(self.brain.tts.synthesize, clean,
                                              self.theme.voice, self.theme.speed)
                if not started:
                    started = True
                    await self.send(State("speaking"))
                    await self.send(AudioStart(self.brain.tts.sample_rate))
                await self.send(Emote(emotion))
                for i in range(0, len(pcm), AUDIO_CHUNK_BYTES):
                    await self.ws.send_bytes(pcm[i:i + AUDIO_CHUNK_BYTES])
        finally:
            if started and not self.ws.closed:
                await self.send(AudioEnd())


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    brain: Brain = request.app["brain"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=WS_MAX_BYTES)   # pictures can be big
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
    app.on_startup.append(app["brain"].on_startup)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health)
    return app
