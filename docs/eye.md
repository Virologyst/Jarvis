# The eye

Jarvis's face on the touchscreen is an eye that looks at whoever is talking. One ESP32-S3 mic
node, plugged into the terminal (Pi) by USB, works out which direction a sound came from; the
terminal turns that into where the eye should look.

Ported from the EGH490 acoustic-localisation prototype (`acoustic-proto` @ `dac085a`). EGH490
itself is untouched; these are copies.

| Here | Came from (EGH490 `acoustic-proto/`) | Changes |
|---|---|---|
| `firmware/node/` | `firmware/node/` | none |
| `terminal/jarvis_terminal/eye/nodeproto.py` | `host/nodelink/protocol.py` | none |
| `terminal/jarvis_terminal/eye/tracker.py` | `host/eyehost/tracker.py` | none |
| `terminal/jarvis_terminal/eye/registry.py` | `host/eyehost/registry.py` | one-node default layout |
| `terminal/jarvis_terminal/eye/sources.py` | `host/eyehost/sources.py` | simulator uses the registry |
| `ui/eye.js` | `host/eyehost/static/eye.js` | driven by Jarvis's state too |
| `docs/node-wiring.md` | `hardware/wiring.md` | none |

## Hardware

One breadboard node: ESP32-S3-DevKitC-1 + 2 × INMP441 (see [node-wiring.md](node-wiring.md)),
plugged into the Pi by its **"USB"** port (the S3's native USB, not the "COM" port).

- Mount it **against the wall, beside the screen, facing the room**. A two-mic pair can't tell
  front from back, so everyone talking to Jarvis must be in front of it.
- A pair only gives left/right, not height. That's all the eye needs.
- With one node the distance to the speaker is a guess (2 m). That only matters for the small
  correction for the node sitting beside the screen rather than in the middle of it.

## Setup

1. Flash the firmware (see [firmware/node/README.md](../firmware/node/README.md)).
2. In `.env` on the Pi, set where the node is (only used the first time it's seen):
   ```
   JARVIS_EYE_NODE_OFFSET_M=-0.2     # metres from the middle of the screen, negative = left
   JARVIS_EYE_NODE_M1_SIDE=left
   ```
3. The Pi user must be in `dialout` to open the node (`pi/install.sh` checks).
4. Plug the node in. The terminal finds it by Espressif's USB vendor ID and saves its pose to
   `data/eye-nodes.json`, keyed by the board's MAC.

**If the eye looks the wrong way** (you're on its left, it looks right): set `m1_side` to the
other value in `data/eye-nodes.json` (or delete that file and change `JARVIS_EYE_NODE_M1_SIDE`)
and restart the terminal.

## What the eye does

| Jarvis is | The eye |
|---|---|
| not connected to the brain | closed (asleep) |
| idle | drowsy, half open; glances at sounds in the room |
| listening | wide open, following whoever is talking |
| thinking | glances up and away |
| speaking | looks back at the person it's answering |

While Jarvis is thinking or speaking the tracker ignores the node, so the eye doesn't turn to
look at its own speaker.

## Try it without hardware

```
.venv\Scripts\python -m jarvis_terminal --eye-sim
```

A simulated node hears a voice wandering around in front of the screen (12 s on, 12 s off).
`--no-eye` turns the eye's node scanning off.

## Known gaps

- The node's direction finding was tuned for 1.5–3 kHz test tones. Speech works, but the
  firmware band may want widening (e.g. 300 Hz–3.4 kHz) for steadier tracking of voices.
- EGH490's `calibrate.py` (fits node headings from measured spots) isn't ported; with one node
  beside the screen it isn't needed.
