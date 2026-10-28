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
- A bare wake phrase ("Hey Jarvis.") is answered in code ("Yes, Sir?", cached audio) without a
  Claude call, then the terminal listens for the request; the exchange is added to the
  conversation so Claude has it.
- End of speech: Silero VAD on 512-sample windows, and while listening a frame no louder than
  `JARVIS_QUIET_FACTOR` (1.5) times the room's quiet level before the wake isn't speech, so a hum
  the VAD mistakes for voice can't hold the mic open to the 25 s limit.
- Timing: both sides log `[timing]` lines (wake heard, end of speech and why, speech-to-text,
  each Claude round, first text, text-to-speech, first audio, mic lag) to find slow stages.
- Tapping the button (or a new ListenStart) while Jarvis talks interrupts it.
- Half-duplex for now: the mic is ignored while Jarvis thinks/speaks (no echo loops).
  True barge-in by voice needs echo cancellation — the speakerphone provides it; enable later.

### Permanent listening
"Listen to me from now on" / "stop listening" (or the pill-shaped toggle at the top right of the
screen) switches a terminal into answering anything said to it, no wake word. The brain
recognises the phrases in code (`listenmode.py`), confirms out loud, and sends `ListenMode` to
that terminal only. While it's on, the idle terminal opens the mic after ~0.24 s of clear voice
(`JARVIS_ONSET_FRAMES`), never while Jarvis is speaking or for `JARVIS_ECHO_GUARD_S` after; the
brain drops one-word scraps and speech-to-text's noise phantoms ("Thank you."), and spoken
approve/reject needs the name ("Jarvis, approve"). Off at startup; kept across a brain restart.

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

## Task lists
`brain/jarvis_brain/tasks.py`, saved in `data/tasks.json` (written atomically after every change;
an unreadable file is set aside, not overwritten). Each person has a named list; "my tasks" is
Craig's until voices can be told apart: `ToolBox(speaker=...)` takes a callable returning the
speaker's name, ready for voice identification. Tasks have an optional due date and time
(Brisbane, via `clock.py`), a status (open, on hold, done) and notes Jarvis keeps itself. These
tools change only Jarvis's own notebook, like the theme choice, so they run without an approval
card; deleting moves a task to a bin it can be restored from.

### Reminders
A task can have reminders, saved with it in `data/tasks.json`. `set_task_reminder` (or
`add_task`'s `reminder`) takes a Brisbane date and time or a time relative to the deadline ("an
hour before", "the morning of") and reports the exact time back; it also snoozes the reminder just
given. Jarvis asks whether and when to remind after adding a task. `reminders.py` checks every 30
seconds; a due reminder on an open task is queued on every connected terminal and said, in persona,
once that terminal is quiet (not listening, thinking, speaking or still playing). It's then marked
delivered and saved, so it's said once, even across restarts. `AudioStart(follow_up=True)` makes an
idle terminal open the mic afterwards, and the reminder is added to that terminal's conversation so
"snooze it fifteen minutes" makes sense. One that fell due while the brain was off is still said
unless the task is over an hour past its deadline (or, with no deadline, 12 hours late); done,
deleted and on-hold tasks stay quiet.

## Reading files
`brain/jarvis_brain/files.py`: `list_directory`, `read_file` and `search_files`, mainly for
Craig's Obsidian vault. Strictly read-only (files are only opened for reading), so no approval
card. Paths must be absolute and on the C: drive; each is normalised and resolved (symlinks,
junctions, `..`) and checked before and after resolving, so a link can't lead elsewhere.
Refused, and left out of listings and searches: Jarvis's own checkout (settings, keys, task
data, approval and upgrade machinery) and the upgrade worktrees, plus common secret stores
(`.ssh`, `.aws`, `.claude`, browser profiles, Windows credential stores, `.env` files, key and
password-database files). Reads return up to 12,000 characters with a note on where to read
on; Obsidian canvas files come back as cards and connections; binary files are described, not
read. Searches stop after 25 results or 8 seconds and skip system folders.

## Roadmap
1. Skeleton: wake word → STT → Claude → TTS on the dev PC. Built and tested end to end
   except the Claude call itself (needs an API key in `.env`). Measured on the dev PC
   (RTX 5090): STT ~0.07 s per utterance with large-v3-turbo; first reply audio ~0.1 s
   after the reply text arrives.
2. Pi terminal in the other room (pi/install.sh); brain moved to the brain PC as a service.
3. Email via Microsoft Graph (M365 Business Basic works — no desktop license needed):
   notify on new mail from chosen senders, draft replies, approve on screen, send.
4. Calendar, memory, more tools; voice barge-in; nicer voice (ElevenLabs/Cartesia/Kokoro option).
