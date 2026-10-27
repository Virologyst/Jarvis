# Jarvis

A voice assistant for the V Source office. Say **"Hey Jarvis"** or tap the button on a
touchscreen, talk normally, and Jarvis (Claude) answers out loud.

```
 Terminal (Pi 5 / any PC)                  Brain (always-on Windows PC)
 ├─ "hey jarvis" wake word (local)  ──WS──► ├─ speech-to-text (faster-whisper, GPU)
 ├─ mic + speaker (USB speakerphone)        ├─ Claude (persona, memory, tools)
 └─ touchscreen page                ◄─WS──  ├─ text-to-speech (Piper)
                                            └─ approval gate for risky actions
```

## Layout

| Folder | What | Runs on |
|---|---|---|
| `protocol/` | Message types shared by both sides (no dependencies) | everywhere |
| `brain/` | The server: STT → Claude → TTS, tools, approvals | the brain PC |
| `terminal/` | Wake word, mic, speaker, the eye's mic node, serves the touchscreen page | Pi 5, dev PC |
| `ui/` | The touchscreen page (the eye — tap it to talk — conversation, approval cards) | served by a terminal |
| `firmware/` | ESP32-S3 mic node that tells the eye where sound is coming from ([docs/eye.md](docs/eye.md)) | the node |
| `pi/` | Pi install, kiosk autostart, restart/stop helpers | Pi 5 |
| `scripts/` | Dev-machine setup | dev PC |
| `docs/` | [Architecture and decisions](docs/architecture.md), [the eye](docs/eye.md), [self-upgrade](docs/self-upgrade.md), [Obsidian vault](docs/vault.md) | |

Each part has its own `pyproject.toml`, so a Pi installs only `protocol` + `terminal`
(no GPU or Claude libraries).

## Run it on this PC (brain and terminal together)

Needs Python 3.11+ (`py` launcher), a mic and speakers, and a Claude API key.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-dev.ps1
# put ANTHROPIC_API_KEY=... in .env (or run `ant auth login`)
.venv\Scripts\python -m jarvis_brain        # window 1
.venv\Scripts\python -m jarvis_terminal     # window 2
```

Open http://localhost:8080. Say "hey Jarvis", tap the button, or press Enter in the
terminal window. Useful flags: `--no-wake` (button only), `--list-devices`,
`--mic <n>` / `--speaker <n>`, `-v` for debug logs.

## Set up the brain PC (starts at boot, no login needed)

On the always-on Windows PC, as the account the brain runs as, in PowerShell opened with
**Run as Administrator**:

```powershell
git clone https://github.com/Virologyst/Jarvis.git; cd Jarvis
powershell -ExecutionPolicy Bypass -File brain\install.ps1
# put ANTHROPIC_API_KEY=... in .env, then run the script again
```

It registers a "Jarvis brain" scheduled task that runs the supervisor at every boot (needed for
self-upgrades and their rollback), opens the port on the private network, stops the PC
sleeping, then starts the brain and checks it answers. Logs: `logs\brain.log`. Update later
with `git pull` then `Stop-ScheduledTask -TaskName "Jarvis brain"; Start-ScheduledTask -TaskName "Jarvis brain"`
(or re-run the script if dependencies changed). Don't also start the brain by hand: two
brains can't share the port.

## Set up a Pi terminal

```bash
sudo apt install -y libportaudio2
git clone https://github.com/Virologyst/Jarvis.git ~/Jarvis
cp ~/Jarvis/.env.example ~/Jarvis/.env   # set JARVIS_BRAIN_URL=ws://<brain-pc-ip>:8765/ws
~/Jarvis/pi/install.sh
```

Update later with `cd ~/Jarvis && git pull && pi/restart.sh`.

## Configuration

All settings live in `.env` (see [.env.example](.env.example)). Key ones:
`JARVIS_MODEL` (default `claude-opus-5-5`), `JARVIS_EFFORT` (default `low` for snappy
conversation), `JARVIS_STT_MODEL`, `JARVIS_VOICE`, `JARVIS_BRAIN_URL`.
