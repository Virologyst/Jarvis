"""The terminal: mic -> wake word / button -> brain -> speaker, plus the touchscreen page.

Local states:
  idle       listening for "hey Jarvis" (or a button tap)
  listening  streaming the mic to the brain until you stop talking
  waiting    brain is thinking or speaking; mic ignored (no echo of Jarvis's voice)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

from jarvis_protocol import (
    AudioEnd, AudioStart, Decision, Hello, ListenCancel, ListenEnd, ListenStart,
    State, decode, encode,
)

from .audio import Microphone, Player, rms
from .wake import WakeWord

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parents[2] / "ui"

FRAME_S = 0.08
SILENCE_TO_END_S = 0.9     # stop listening after this much quiet following speech
NO_SPEECH_CANCEL_S = 6.0   # give up if nothing is said after waking
MAX_LISTEN_S = 25.0
MIN_SPEECH_RMS = 350.0     # floor for "someone is talking" on a quiet mic


class Terminal:
    def __init__(self, brain_url: str, terminal_id: str, ui_port: int,
                 wake_threshold: float, use_wake: bool, mic_device=None, speaker_device=None):
        self.brain_url = brain_url
        self.terminal_id = terminal_id
        self.ui_port = ui_port
        self.wake = WakeWord(threshold=wake_threshold) if use_wake else None
        self.mic_device, self.speaker_device = mic_device, speaker_device

        self.state = "idle"
        self.brain_state = "idle"
        self.brain_ws: aiohttp.ClientWebSocketResponse | None = None
        self.ui_sockets: set[web.WebSocketResponse] = set()
        self.player = Player(speaker_device)
        self.noise_floor = 100.0
        self._listen = {"started": 0.0, "speech": False, "last_voice": 0.0}

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

    async def start_listening(self, reason: str) -> None:
        self.player.clear()  # tapping while Jarvis talks interrupts it
        if not await self.to_brain(ListenStart(reason)):
            return
        now = time.monotonic()
        self._listen = {"started": now, "speech": False, "last_voice": now}
        await self.set_state("listening")
        log.info("listening (%s)", reason)

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

    async def mic_loop(self) -> None:
        mic = Microphone(asyncio.get_running_loop(), self.mic_device)
        mic.start()
        log.info("microphone open")
        try:
            while True:
                frame = await mic.queue.get()
                level = rms(frame)
                if self.state == "idle":
                    self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
                    if self.wake and self.brain_ws and self.wake.heard(frame):
                        await self.start_listening("wake")
                elif self.state == "listening":
                    await self._listening_frame(frame, level)
                elif self.state == "waiting":
                    if self.brain_state == "idle" and not self.player.busy:
                        await self.set_state("idle")
                        if self.wake:
                            self.wake.reset()
        finally:
            mic.stop()

    async def _listening_frame(self, frame: bytes, level: float) -> None:
        if self.brain_ws is None or self.brain_ws.closed:
            await self.set_state("idle")
            return
        await self.brain_ws.send_bytes(frame)
        now = time.monotonic()
        threshold = max(MIN_SPEECH_RMS, self.noise_floor * 3)
        if level >= threshold:
            self._listen["speech"] = True
            self._listen["last_voice"] = now
        elapsed = now - self._listen["started"]
        if self._listen["speech"] and now - self._listen["last_voice"] > SILENCE_TO_END_S:
            await self.stop_listening()
        elif not self._listen["speech"] and elapsed > NO_SPEECH_CANCEL_S:
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
        if isinstance(msg, AudioStart):
            self.player.begin(msg.sample_rate)
        elif isinstance(msg, AudioEnd):
            pass  # playback drains on its own
        elif isinstance(msg, State):
            self.brain_state = msg.state
        await self.to_ui(json.loads(raw))  # the page shows everything the brain says

    # ---- touchscreen page ---------------------------------------------

    async def ui_events(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.ui_sockets.add(ws)
        await ws.send_str(json.dumps({"type": "connection", "connected": self.brain_ws is not None}))
        await ws.send_str(json.dumps({"type": "terminal_state", "state": self.state}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                action = json.loads(msg.data)
                if action.get("action") == "talk":
                    await self.toggle("button")
                elif action.get("action") == "decision":
                    await self.to_brain(Decision(action["card_id"], action["decision"]))
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
        try:
            await asyncio.gather(*tasks)
        finally:
            self.player.close()
            await runner.cleanup()
