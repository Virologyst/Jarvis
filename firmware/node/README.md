# Node firmware (ESP32-S3, 2 × INMP441, USB serial)

Wiring: [../../hardware/wiring.md](../../hardware/wiring.md). Protocol: [src/protocol.h](src/protocol.h).

## Build and flash
Plug the DevKit in by the port labelled **USB** (the S3's native USB, not COM/UART), then from this folder:

```bash
pio run -t upload
```

## First run (bring-up)
From `host/` (needs `pip install -r requirements.txt`):

```bash
python tools/monitor.py --id 1 --spacing 50.8
```

That sets the node ID and the measured mic spacing (both saved on the node), then prints a
heartbeat line every 0.5 s with each mic's level, plus bearings when a sound passes the gate.

Bring-up checks (also in wiring.md §6):
1. **Both mics alive.** In a quiet room, M1 and M2 RMS levels sit within a few dB of each other.
2. **Channel map.** Tap M1: only the M1 peak jumps. Tap M2: only M2.
3. **Bearing sense.** Play drone audio from the phone off the M1 end of the line: theta near 0°.
   Straight out from the arrow: ~90°. Off the M2 end: ~180°.
4. **Recording.** `python tools/monitor.py --mode both --record test.wav --seconds 10 --host-doa`
   saves stereo audio and runs the reference estimator on the host next to the node's own.

## Commands
Sent as text lines; `monitor.py` sends the common ones for you.

| Command | Effect |
|---|---|
| `mode off\|bearing\|raw\|both` | What to stream. Boots in `bearing`. |
| `id <n>` | Node ID, saved |
| `spacing <mm>` | Measured M1–M2 hole spacing, saved |
| `c <m/s>` | Speed of sound, saved (343 at ~20 °C) |
| `band <lo> <hi>` | Analysis band in Hz (default 1500 3000) |
| `thresh <dBFS> <coh>` | Detection gate (default -75 0.35) |
| `info` / `reboot` | |

## How a bearing is computed
16 kHz stereo, 1024-sample frames every 512 samples (~31 bearings/s). GCC-PHAT on
1.5–3 kHz, with the cross-spectrum averaged over ~3 frames and only harmonic bins kept,
scanned over 241 delays across ±d/c with parabolic refinement. θ = acos(c·τ/d).
The same maths in numpy is in `host/nodelink/doa.py`, tested by `host/tests/test_offline.py`.

## Windows note
If the build fails with "No such file or directory" under `.pio/build/...`, the project path
is too long for Windows' 260-character limit. Move the project to a shorter path, or build
with a short build folder:

```bash
PLATFORMIO_BUILD_DIR=C:/tmp/acb pio run -t upload
```
