"""Text to speech: Kokoro (natural, the default) or Piper (lighter, more robotic). Both run locally.

JARVIS_VOICE picks the voice:
  kokoro:bm_daniel                         one Kokoro voice
  kokoro:bm_daniel=0.65,bm_lewis=0.35      a blend of Kokoro voices
  voices/en_GB-alan-medium.onnx            a Piper voice file
JARVIS_VOICE_SPEED sets the pace for Kokoro (1.0 = normal, 1.12 = 12% faster).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

KOKORO_MODEL = "kokoro-v1.0.onnx"
KOKORO_VOICES = "voices-v1.0.bin"


def make_speaker(voice: str, speed: float, voices_dir: Path):
    """Build the speaker described by JARVIS_VOICE."""
    if voice.startswith("kokoro:"):
        return KokoroSpeaker(voice.removeprefix("kokoro:"), speed, voices_dir)
    path = Path(voice)
    if not path.is_absolute():
        path = voices_dir.parent / path
    return PiperSpeaker(path)


class KokoroSpeaker:
    def __init__(self, spec: str, speed: float, voices_dir: Path):
        from kokoro_onnx import Kokoro

        model, voices = voices_dir / KOKORO_MODEL, voices_dir / KOKORO_VOICES
        if not (model.is_file() and voices.is_file()):
            raise FileNotFoundError(
                f"Kokoro files not found in {voices_dir}\n"
                "Fetch them with: python brain/scripts/get_voice.py kokoro"
            )
        self.kokoro = Kokoro(str(model), str(voices))
        self.mix = _parse_mix(spec)
        self.style = sum(w * self.kokoro.get_voice_style(v) for v, w in self.mix.items())
        self.speed = speed
        self.sample_rate = 24_000
        self.synthesize("Ready.")  # first call is slow; pay it at start-up, not mid-reply
        log.info("text-to-speech: Kokoro %s at %.2fx speed", spec, speed)

    def synthesize(self, text: str) -> bytes:
        """Text -> mono int16 PCM at self.sample_rate."""
        audio, rate = self.kokoro.create(text, voice=self.style, speed=self.speed, lang="en-gb")
        self.sample_rate = rate
        return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _parse_mix(spec: str) -> dict[str, float]:
    """'bm_daniel' or 'bm_daniel=0.65,bm_lewis=0.35' -> weights that sum to 1."""
    mix: dict[str, float] = {}
    for part in spec.split(","):
        name, _, weight = part.strip().partition("=")
        mix[name] = float(weight) if weight else 1.0
    total = sum(mix.values())
    return {name: w / total for name, w in mix.items()}


class PiperSpeaker:
    def __init__(self, voice_path: Path):
        from piper import PiperVoice

        if not voice_path.is_file():
            raise FileNotFoundError(
                f"Piper voice not found: {voice_path}\n"
                "Fetch one with: python brain/scripts/get_voice.py"
            )
        self.voice = PiperVoice.load(str(voice_path))
        self.sample_rate = self.voice.config.sample_rate
        self.synthesize("Ready.")
        log.info("text-to-speech: Piper %s at %d Hz", voice_path.name, self.sample_rate)

    def synthesize(self, text: str) -> bytes:
        """Text -> mono int16 PCM at self.sample_rate."""
        return b"".join(chunk.audio_int16_bytes for chunk in self.voice.synthesize(text))
