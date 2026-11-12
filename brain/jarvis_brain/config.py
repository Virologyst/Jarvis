from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Jarvis's own voice (every theme without one of its own; Arc keeps Lily, see themes.py).
# Aim: the films' J.A.R.V.I.S. in spirit only (no actor cloned): calm, refined Received
# Pronunciation, measured, a little dry. Change it here, or per machine with JARVIS_VOICE and
# JARVIS_VOICE_SPEED in .env. Kokoro has no pitch control; for a lower pitch blend in a deeper
# voice, e.g. "kokoro:bm_george=0.75,bm_lewis=0.25".
DEFAULT_VOICE = "kokoro:bm_george"
DEFAULT_VOICE_SPEED = 0.95      # 1.0 = Kokoro's normal pace; a touch slower sounds measured

# The stock British male voices considered, and why.
VOICE_CANDIDATES = {
    "kokoro:bm_george": "chosen: mature, polished RP with a neutral-to-low pitch and an even, "
                        "unhurried delivery; the best-rated British male in Kokoro",
    "kokoro:bm_daniel": "the old default: clear RP but lighter and quicker, less gravitas",
    "kokoro:bm_fable": "warm storyteller; too expressive for a dry, composed butler",
    "kokoro:bm_lewis": "deepest, but rougher and less crisp; best used in a blend for pitch",
    "voices/en_GB-alan-medium.onnx": "Piper Alan: British but flat and robotic",
}


@dataclass
class Settings:
    host: str
    port: int
    model: str
    effort: str
    stt_model: str
    stt_device: str
    voice: str            # "kokoro:<voice>[=w,...]" or a Piper .onnx path (see tts.py)
    voice_speed: float
    voices_dir: Path
    idle_reset_minutes: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("JARVIS_HOST", "0.0.0.0"),
            port=int(os.environ.get("JARVIS_PORT", "8765")),
            model=os.environ.get("JARVIS_MODEL", "claude-opus-5-5"),
            effort=os.environ.get("JARVIS_EFFORT", "low"),
            stt_model=os.environ.get("JARVIS_STT_MODEL", "large-v3-turbo"),
            stt_device=os.environ.get("JARVIS_STT_DEVICE", "auto"),
            voice=os.environ.get("JARVIS_VOICE", DEFAULT_VOICE),
            voice_speed=float(os.environ.get("JARVIS_VOICE_SPEED", DEFAULT_VOICE_SPEED)),
            voices_dir=REPO_ROOT / "voices",
            idle_reset_minutes=float(os.environ.get("JARVIS_IDLE_RESET_MINUTES", "10")),
        )
