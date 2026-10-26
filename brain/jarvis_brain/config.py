from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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
            # Plain Kokoro "Daniel", 12% faster: picked by ear against the films' J.A.R.V.I.S.
            voice=os.environ.get("JARVIS_VOICE", "kokoro:bm_daniel"),
            voice_speed=float(os.environ.get("JARVIS_VOICE_SPEED", "1.12")),
            voices_dir=REPO_ROOT / "voices",
            idle_reset_minutes=float(os.environ.get("JARVIS_IDLE_RESET_MINUTES", "10")),
        )
