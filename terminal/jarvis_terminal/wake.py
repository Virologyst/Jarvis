"""'Hey Jarvis' detection with openWakeWord (runs locally; nothing leaves the device)."""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class WakeWord:
    def __init__(self, name: str = "hey_jarvis", threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models([name])  # no-op once cached
        self.model = Model(wakeword_models=[name], inference_framework="onnx")
        self.threshold = threshold
        log.info("wake word ready: %s (threshold %.2f)", name, threshold)

    def heard(self, frame: bytes) -> bool:
        scores = self.model.predict(np.frombuffer(frame, dtype=np.int16))
        return max(scores.values(), default=0.0) >= self.threshold

    def reset(self) -> None:
        """Forget recent audio, e.g. after Jarvis spoke, so its own voice can't re-trigger."""
        self.model.reset()
