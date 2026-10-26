"""The terminal: mic -> wake word / button -> brain -> speaker, plus the touchscreen page.

Local states:
  idle       listening for "hey Jarvis" (or a button tap)
  listening  streaming the mic to the brain until you stop talking
  waiting    brain is thinking or speaking; mic ignored (no echo of Jarvis's voice)
"""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import uuid
import logging
import os
import time
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import WSMsgType, web

from jarvis_protocol import (
    AudioEnd, AudioStart, Decision, Emote, Hello, ListenCancel, ListenEnd, ListenStart,
    SetTheme, State, UserInput, WakeCheck, WakeResult, decode, encode,
)

from .audio import Microphone, Player, refresh_devices, rms
from .wake import MAYBE, WAKE, WakeWord

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parents[2] / "ui"

FRAME_S = 0.08
# Stop listening after this much non-speech following speech. People pause mid-sentence
# ("I'm just testing a... new microphone"), so this needs to be over a second.
SILENCE_TO_END_S = float(os.environ.get("JARVIS_END_SILENCE_S", "1.3"))
# Silero voice detector score (0..1) that counts as speech
VAD_THRESHOLD = float(os.environ.get("JARVIS_VAD_THRESHOLD", "0.5"))
# Give up if nothing is said this long after "hey Jarvis" or a tap
NO_SPEECH_CANCEL_S = float(os.environ.get("JARVIS_LISTEN_TIMEOUT_S", "10"))
MAX_LISTEN_S = 25.0
# After Jarvis answers, keep listening this long for a follow-up without "hey Jarvis" (0 = off)
FOLLOW_UP_S = float(os.environ.get("JARVIS_FOLLOW_UP_S", "10"))
# Floor for "someone is talking". Headset mics with a noise gate read ~0 in silence and only
# ~100-700 for normal speech, so keep this low; raise it for noisy rooms.
MIN_SPEECH_RMS = float(os.environ.get("JARVIS_SPEECH_RMS", "120"))
EYE_HZ = 20                # how often the eye's tracking state goes to the page
MIC_STALL_S = 2.0          # no audio at all for this long = mic unplugged; reopen the default
# Recent audio kept while idle. Sent with a wake, so Jarvis hears the wake phrase too ("Good
# morning Jarvis" gets "Good morning, Sir"), and used for the brain's wake-phrase check.
PREROLL_S = 3.5
# Wake-model score above which a phrase is checked with the brain (see wake.py)
# (0.02: tested on synthetic voices, ordinary sentences stay at ~0.00-0.01)
WAKE_CANDIDATE = float(os.environ.get("JARVIS_WAKE_CANDIDATE", "0.02"))
WAKE_CHECK_MAX_AGE_S = 3.0  # ignore a brain "yes" that arrives later than this


def _load_vad():
    """Silero voice activity detector (ships with openWakeWord). None if unavailable."""
    try:
        from openwakeword.vad import VAD
        vad = VAD()
        log.info("voice detector ready (Silero VAD)")
        return vad
    except Exception as e:
        log.warning("no voice detector (%s); falling back to mic volume to tell when you stop", e)
        return None


class Terminal:
    def __init__(self, brain_url: str, terminal_id: str, ui_port: int,
                 wake_threshold: float, use_wake: bool, mic_device=None, speaker_device=None,
                 eye=None):
        self.eye = eye  # jarvis_terminal.eye.Eye, or None
        self.brain_url = brain_url
        self.terminal_id = terminal_id
        self.ui_port = ui_port
        self.wake = WakeWord(threshold=wake_threshold, candidate=WAKE_CANDIDATE) if use_wake else None
        self.preroll: collections.deque[bytes] = collections.deque(maxlen=int(PREROLL_S / FRAME_S))
        self.theme: str | None = None   # set by the brain (SetTheme)
        self._wake_check: tuple[str, float] | None = None   # (check_id, sent at)
        self.mic_device, self.speaker_device = mic_device, speaker_device

        self.state = "idle"
        self.brain_state = "idle"
        self.brain_ws: aiohttp.ClientWebSocketResponse | None = None
        self.ui_sockets: set[web.WebSocketResponse] = set()
        self.player = Player(speaker_device)
        self.vad = _load_vad()
        self.noise_floor = 100.0
        self._listen = {"started": 0.0, "speech": False, "last_voice": 0.0,
                        "no_speech_s": NO_SPEECH_CANCEL_S}
        self._replied = False

    # ---- outputs -------------------------------------------------------

    async def to_brain(self, msg) -> bool:
        if self.brain_ws is None or self.brain_ws.closed:
            await self.to_ui({"type": "error", "message": "Not connected to the brain"})
            return False
        await self.brain_ws.send_str(encode(msg))
        return True

    async def to_ui(self, payload: dict) -> None:
        raw = json.dumps(payload)
        for ws in list(self.ui_sockets):
            try:
                await ws.send_str(raw)
            except ConnectionError:
                self.ui_sockets.discard(ws)

    async def set_state(self, state: str) -> None:
        self.state = state
        await self.to_ui({"type": "terminal_state", "state": state})

    # ---- talking -------------------------------------------------------

    async def start_listening(self, reason: str, no_speech_s: float = NO_SPEECH_CANCEL_S,
                              with_preroll: bool = False) -> None:
        self.player.clear()  # tapping while Jarvis talks interrupts it
        self._wake_check = None
        if not await self.to_brain(ListenStart(reason)):
            return
        now = time.monotonic()
        self._listen = {"started": now, "speech": False, "last_voice": now, "no_speech_s": no_speech_s}
        if with_preroll and self.preroll:
            # Send the wake phrase too. It counts as speech, so a bare "Good morning Jarvis"
            # ends after the usual pause and gets a reply instead of waiting for more.
            for frame in self.preroll:
                await self.brain_ws.send_bytes(frame)
            self._listen["speech"] = True
        self.preroll.clear()
        self._replied = False
        if self.vad:
            self.vad.reset_states()
        await self.set_state("listening")
        log.info("listening (%s)", reason)

    async def send_wake_check(self) -> None:
        """The wake model half-heard "...Jarvis": ask the brain whether a wake phrase was said."""
        if self._wake_check and time.monotonic() - self._wake_check[1] > WAKE_CHECK_MAX_AGE_S:
            self._wake_check = None   # the brain never answered; don't block future checks
        if self._wake_check or self.state != "idle" or not self.brain_ws or not self.preroll:
            return
        check_id = uuid.uuid4().hex[:8]
        self._wake_check = (check_id, time.monotonic())
        audio = base64.b64encode(b"".join(self.preroll)).decode()
        await self.to_brain(WakeCheck(check_id, audio))

    async def on_wake_result(self, msg: WakeResult) -> None:
        pending = self._wake_check
        if not pending or pending[0] != msg.check_id:
            return
        self._wake_check = None
        fresh = time.monotonic() - pending[1] < WAKE_CHECK_MAX_AGE_S
        if msg.ok and fresh and self.state == "idle":
            await self.start_listening("wake_phrase", with_preroll=True)

    async def stop_listening(self, cancel: bool = False) -> None:
        await self.to_brain(ListenCancel() if cancel else ListenEnd())
        await self.set_state("idle" if cancel else "waiting")
        if self.wake:
            self.wake.reset()

    async def toggle(self, reason: str) -> None:
        """The big button: talk / stop talking / interrupt."""
        if self.state == "listening":
            await self.stop_listening()
        else:
            await self.start_listening(reason)

    # ---- loops ---------------------------------------------------------

    async def _open_mic(self) -> Microphone:
        """Open the mic, retrying until one is plugged in (the screen and eye work without one)."""
        warned = False
        while True:
            try:
                mic = Microphone(asyncio.get_running_loop(), self.mic_device)
                mic.start()
                return mic
            except Exception as e:  # PortAudioError, ValueError (no such device)
                if not warned:
                    log.warning("no microphone (%s); tap-to-talk and wake word are off until one "
                                "is plugged in. Checking every 10 s.", e)
                    warned = True
                await asyncio.sleep(10)
                refresh_devices()

    async def mic_loop(self) -> None:
        while True:
            mic = await self._open_mic()
            log.info("microphone open")
            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(mic.queue.get(), MIC_STALL_S)
                    except asyncio.TimeoutError:
                        # An unplugged mic just stops delivering audio, without an error.
                        # Reopen, picking up whatever mic is the default now.
                        log.warning("microphone stopped sending audio (unplugged?); reopening")
                        break
                    await self._on_frame(frame)
            finally:
                try:
                    mic.stop()
                except Exception:
                    pass
            self.player.close()   # re-scanning devices invalidates open streams; it reopens on demand
            refresh_devices()
            if self.state == "listening":
                await self.stop_listening(cancel=True)

    async def _on_frame(self, frame: bytes) -> None:
        level = rms(frame)
        if self.state == "idle":
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
            self.preroll.append(frame)
            if self.wake and self.brain_ws:
                heard = self.wake.check(frame)
                if heard == WAKE:
                    await self.start_listening("wake", with_preroll=True)
                elif heard == MAYBE:
                    # The score rises partway through "Jarvis": wait for the end of the word
                    asyncio.get_running_loop().call_later(
                        0.4, lambda: asyncio.create_task(self.send_wake_check()))
        elif self.state == "listening":
            await self._listening_frame(frame, level)
        elif self.state == "waiting":
            if self.brain_state == "idle" and not self.player.busy:
                if FOLLOW_UP_S > 0 and self.brain_ws and self._replied:
                    # Jarvis just answered: carry on the conversation without the wake word.
                    # If nothing is said in time it cancels back to idle (no chain).
                    await self.start_listening("follow_up", no_speech_s=FOLLOW_UP_S)
                else:
                    await self.set_state("idle")
                if self.wake:
                    self.wake.reset()

    async def _listening_frame(self, frame: bytes, level: float) -> None:
        if self.brain_ws is None or self.brain_ws.closed:
            await self.set_state("idle")
            return
        await self.brain_ws.send_bytes(frame)
        now = time.monotonic()
        if self.vad:
            # A voice detector copes with quiet syllables and mid-sentence pauses far better
            # than mic volume, and ignores fans and keyboard clatter
            pcm = np.frombuffer(frame, dtype=np.int16)
            is_voice = float(self.vad.predict(pcm, frame_size=640)) >= VAD_THRESHOLD
        else:
            is_voice = level >= max(MIN_SPEECH_RMS, self.noise_floor * 3)
        if is_voice:
            self._listen["speech"] = True
            self._listen["last_voice"] = now
        elapsed = now - self._listen["started"]
        if self._listen["speech"] and now - self._listen["last_voice"] > SILENCE_TO_END_S:
            await self.stop_listening()
        elif not self._listen["speech"] and elapsed > self._listen["no_speech_s"]:
            log.info("nothing said, cancelling")
            await self.stop_listening(cancel=True)
        elif elapsed > MAX_LISTEN_S:
            await self.stop_listening()

    async def brain_loop(self) -> None:
        """Stay connected to the brain, reconnecting if it restarts."""
        delay = 1.0
        async with aiohttp.ClientSession() as http:
            while True:
                try:
                    async with http.ws_connect(self.brain_url, heartbeat=20,
                                               max_msg_size=4 * 1024 * 1024) as ws:
                        self.brain_ws = ws
                        delay = 1.0
                        await ws.send_str(encode(Hello(self.terminal_id)))
                        await self.to_ui({"type": "connection", "connected": True})
                        log.info("connected to brain at %s", self.brain_url)
                        async for msg in ws:
                            if msg.type == WSMsgType.TEXT:
                                await self._from_brain(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                self.player.feed(msg.data)
                except (aiohttp.ClientError, OSError) as e:
                    log.warning("brain unavailable (%s); retrying in %.0fs", e, delay)
                finally:
                    self.brain_ws = None
                    self.brain_state = "idle"
                    await self.to_ui({"type": "connection", "connected": False})
                    if self.state != "idle":
                        await self.set_state("idle")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15.0)

    async def _from_brain(self, raw: str) -> None:
        msg = decode(raw)
        if isinstance(msg, WakeResult):
            await self.on_wake_result(msg)
            return  # nothing for the page to show
        if isinstance(msg, Emote):
            # Show it when its sentence is actually heard, not when it arrives
            delay = self.player.buffered_seconds
            payload = json.loads(raw)
            asyncio.get_running_loop().call_later(
                delay, lambda: asyncio.create_task(self.to_ui(payload)))
            return
        if isinstance(msg, AudioStart):
            self._replied = True  # Jarvis is answering, so a follow-up makes sense afterwards
            self.player.begin(msg.sample_rate)
        elif isinstance(msg, AudioEnd):
            pass  # playback drains on its own
        elif isinstance(msg, State):
            self.brain_state = msg.state
        elif isinstance(msg, SetTheme):
            self.theme = msg.theme   # remembered for pages that connect later
        await self.to_ui(json.loads(raw))  # the page shows everything the brain says

    # ---- touchscreen page ---------------------------------------------

    async def ui_events(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=32 * 1024 * 1024)  # pictures
        await ws.prepare(request)
        self.ui_sockets.add(ws)
        await ws.send_str(json.dumps({"type": "connection", "connected": self.brain_ws is not None}))
        await ws.send_str(json.dumps({"type": "terminal_state", "state": self.state}))
        if self.theme:
            await ws.send_str(json.dumps({"type": "set_theme", "theme": self.theme}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                action = json.loads(msg.data)
                if action.get("action") == "talk":
                    await self.toggle("button")
                elif action.get("action") == "decision":
                    await self.to_brain(Decision(action["card_id"], action["decision"]))
                elif action.get("action") == "say":
                    # Typed text / pictures: stop listening if we were, then send it on
                    if self.state == "listening":
                        await self.stop_listening(cancel=True)
                    self.player.clear()
                    images = [{"media_type": str(i.get("media_type", "")), "data": str(i.get("data", ""))}
                              for i in action.get("images", [])][:5]
                    self._replied = False
                    if await self.to_brain(UserInput(str(action.get("text", "")), images)):
                        await self.set_state("waiting")
                elif action.get("action") == "theme":
                    await self.to_brain(SetTheme(str(action.get("theme", ""))))
        finally:
            self.ui_sockets.discard(ws)
        return ws

    async def serve_ui(self) -> web.AppRunner:
        app = web.Application()
        app.router.add_get("/events", self.ui_events)
        app.router.add_get("/", lambda r: web.FileResponse(UI_DIR / "index.html"))
        app.router.add_static("/", UI_DIR)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.ui_port).start()
        log.info("touchscreen page on http://localhost:%d", self.ui_port)
        return runner

    async def eye_loop(self) -> None:
        """Send the eye's tracking state to the page, and mute it while Jarvis is busy."""
        self.eye.start()
        while True:
            self.eye.mute(self.state == "waiting" or self.player.busy)
            if self.ui_sockets:
                await self.to_ui({"type": "eye", **self.eye.snapshot()})
            await asyncio.sleep(1 / EYE_HZ)

    async def voice_level_loop(self) -> None:
        """While Jarvis speaks, send how loud it is (~20 times a second) so the face moves with it."""
        was_busy = False
        while True:
            busy = self.player.busy
            if self.ui_sockets and (busy or was_busy):
                await self.to_ui({"type": "voice_level", "level": round(self.player.level if busy else 0, 3)})
            was_busy = busy
            await asyncio.sleep(1 / EYE_HZ)

    async def keyboard_loop(self) -> None:
        """Press Enter in the console to talk (handy when developing)."""
        while True:
            await asyncio.to_thread(input)
            await self.toggle("key")

    async def run(self, keyboard: bool) -> None:
        runner = await self.serve_ui()
        tasks = [asyncio.create_task(self.brain_loop()), asyncio.create_task(self.mic_loop())]
        if keyboard:
            tasks.append(asyncio.create_task(self.keyboard_loop()))
        if self.eye:
            tasks.append(asyncio.create_task(self.eye_loop()))
        tasks.append(asyncio.create_task(self.voice_level_loop()))
        try:
            await asyncio.gather(*tasks)
        finally:
            self.player.close()
            await runner.cleanup()
