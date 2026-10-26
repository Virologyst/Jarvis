"""Text to speech with Piper (local, fast on CPU)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Speaker:
    def __init__(self, voice_path: Path):
        from piper import PiperVoice

        if not voice_path.is_file():
            raise FileNotFoundError(
                f"Piper voice not found: {voice_path}\n"
                "Fetch one with: python brain/scripts/get_voice.py"
            )
        self.voice = PiperVoice.load(str(voice_path))
        self.sample_rate = self.voice.config.sample_rate
        self.synthesize("Ready.")  # first call is slow; pay it at start-up, not mid-reply
        log.info("text-to-speech: %s at %d Hz", voice_path.name, self.sample_rate)

    def synthesize(self, text: str) -> bytes:
        """Text -> mono int16 PCM at self.sample_rate."""
        return b"".join(chunk.audio_int16_bytes for chunk in self.voice.synthesize(text))
