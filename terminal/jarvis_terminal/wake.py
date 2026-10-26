"""Wake word detection with openWakeWord (runs locally; nothing leaves the device unless asked).

The only ready-made model is "hey Jarvis". It scores near 1.0 for "hey Jarvis" and "OK Jarvis",
and somewhere in between for other phrases ending in "Jarvis" ("good morning Jarvis", "are you
awake Jarvis"), while ordinary speech scores ~0. So:
  score >= threshold            -> wake straight away
  candidate <= score < threshold -> "maybe": the terminal asks the brain's speech-to-text to
                                    confirm one of the wake phrases was said (see client.py)
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

WAKE = "wake"
MAYBE = "maybe"


class WakeWord:
    def __init__(self, name: str = "hey_jarvis", threshold: float = 0.5, candidate: float = 0.1):
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models([name])  # no-op once cached
        self.model = Model(wakeword_models=[name], inference_framework="onnx")
        self.threshold = threshold
        self.candidate = candidate
        self._maybe_armed = True
        log.info("wake word ready: %s (wake %.2f, check with the brain from %.2f)",
                 name, threshold, candidate)

    def check(self, frame: bytes) -> str | None:
        """WAKE, MAYBE (once per rise of the score) or None."""
        scores = self.model.predict(np.frombuffer(frame, dtype=np.int16))
        score = max(scores.values(), default=0.0)
        if score >= self.threshold:
            return WAKE
        if score < self.candidate / 2:
            self._maybe_armed = True   # score fell away: the next rise is a new "maybe"
        elif score >= self.candidate and self._maybe_armed:
            self._maybe_armed = False
            return MAYBE
        return None

    def heard(self, frame: bytes) -> bool:
        return self.check(frame) == WAKE

    def reset(self) -> None:
        """Forget recent audio, e.g. after Jarvis spoke, so its own voice can't re-trigger."""
        self.model.reset()
        self._maybe_armed = True
