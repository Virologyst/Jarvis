# Architecture and decisions

## Devices

| Device | Role |
|---|---|
| **Brain PC** (Windows, RTX 4090, always on) | Runs the brain as a Windows service under its own `jarvis` account. Shared with the apprentice for 3D printing under a separate standard account. |
| **Pi 5 + touchscreen + USB speakerphone** (another room) | A terminal: wake word, mic, speaker, kiosk page. |
| **Dev PC** (Craig's, RTX 5090) | Where Jarvis is built. Runs brain + terminal together while developing; later just a browser/terminal. |

The brain address is configuration (`JARVIS_BRAIN_URL`), so moving the brain from the dev
PC to the brain PC is a config change, not a code change.

### Why the brain is on a separate always-on PC
- **Uptime:** the dev PC gets rebooted and loaded; Jarvis should keep running (especially once it watches email).
- **Isolation:** an agent with inbox access should not live on the machine holding code and keys.
- **Resources:** local STT on the GPU doesn't compete with dev work.
- Windows is fine: faster-whisper and Piper run natively; the service starts at boot, restarts on crash.
  Set Windows Update active hours and disable sleep/hibernate.

### Why a USB speakerphone
The Pi 5 has no headphone jack, and a conference speakerphone (Jabra Speak, Anker PowerConf)
gives mic + speaker + hardware echo cancellation in one plug, so Jarvis doesn't hear itself.

## One turn

```
terminal                                 brain
idle ── "hey jarvis" / tap ──► ListenStart
listening: 80 ms mic frames ─► (binary)  buffer
silence 0.9 s ──────────────► ListenEnd  ─► faster-whisper ─► Transcript
waiting (mic ignored)                     ─► Claude stream ─► ReplyText pieces
                              ◄─ AudioStart + PCM frames ◄─ Piper, sentence by sentence
idle again once playback drains           State(idle)
```

- Everything streams: Jarvis starts speaking after the **first sentence**, not the whole reply.
- Tapping the button (or a new ListenStart) while Jarvis talks interrupts it.
- Half-duplex for now: the mic is ignored while Jarvis thinks/speaks (no echo loops).
  True barge-in by voice needs echo cancellation — the speakerphone provides it; enable later.

## Protocol
`protocol/jarvis_protocol/messages.py`. One WebSocket per terminal at `ws://<brain>:8765/ws`.
JSON text frames for control; binary frames are 16-bit mono PCM (mic: 16 kHz; speech: rate
announced in `AudioStart`). Unknown message types decode to `None`, so older terminals ignore
newer messages.

The terminal also serves the touchscreen page (`ui/`) on port 8080 and relays brain messages to
it over `/events`. The page sends `talk` and approval `decision` actions back.

## Claude
- Model `claude-opus-5-5`, adaptive thinking, effort `low` by default (conversational speed;
  raise via `JARVIS_EFFORT`).
- Streaming with `client.beta.messages.stream`, server-side refusal fallback
  (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`).
- History is append-only and sent back unchanged (keeps thinking blocks valid). After 10 idle
  minutes the next turn starts a fresh conversation rather than trimming.
- The system prompt (`brain/jarvis_brain/persona.py`) is stable and cached.

## Safety: approval logic lives in code
`brain/jarvis_brain/approvals.py`. The model can only **propose** a side-effecting action (send
an email, etc.). Proposals become `Card(kind="approval")` on every screen; the action runs only
when a terminal sends `Decision(approve)` for that card id. One decision per card; cards expire.

Rules for tools as they're added:
- Tools that read are fine to run directly; tools that **change anything** must go through
  `ApprovalGate.propose()`.
- Email content is untrusted input. Tools get the narrowest scope (reply to *this* message,
  read *this* sender's thread) — no general mailbox search, forwarding, or arbitrary recipients.

## Roadmap
1. Skeleton: wake word → STT → Claude → TTS on the dev PC. Built and tested end to end
   except the Claude call itself (needs an API key in `.env`). Measured on the dev PC
   (RTX 5090): STT ~0.07 s per utterance with large-v3-turbo; first reply audio ~0.1 s
   after the reply text arrives.
2. Pi terminal in the other room (pi/install.sh); brain moved to the brain PC as a service.
3. Email via Microsoft Graph (M365 Business Basic works — no desktop license needed):
   notify on new mail from chosen senders, draft replies, approve on screen, send.
4. Calendar, memory, more tools; voice barge-in; nicer voice (ElevenLabs/Cartesia/Kokoro option).
