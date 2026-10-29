"""Speech to text with faster-whisper (GPU when it works, CPU otherwise)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _add_pip_cuda_libs() -> None:
    """On Windows, let CTranslate2 find the CUDA DLLs from the nvidia-* pip packages
    (installed by `pip install -e brain[gpu]`) without a system-wide CUDA install."""
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    for root in map(Path, nvidia.__path__):
        for bin_dir in root.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


class Transcriber:
    def __init__(self, model_name: str, device: str = "auto"):
        _add_pip_cuda_libs()
        from faster_whisper import WhisperModel

        attempts = {
            "cuda": [("cuda", "float16")],
            "cpu": [("cpu", "int8")],
            "auto": [("cuda", "float16"), ("cpu", "int8")],
        }[device]
        last_error = None
        for dev, compute in attempts:
            try:
                model = WhisperModel(model_name, device=dev, compute_type=compute)
                # A tiny warm-up surfaces missing CUDA libraries now, not mid-sentence.
                list(model.transcribe(np.zeros(16_000, dtype=np.float32), language="en")[0])
                # ...and load its voice filter, used for every real turn, so the first doesn't wait
                list(model.transcribe(np.zeros(16_000, dtype=np.float32), language="en",
                                      vad_filter=True)[0])
            except Exception as e:  # noqa: BLE001 - any failure means try the next device
                log.warning("speech-to-text on %s failed: %s", dev, e)
                last_error = e
                continue
            self.model, self.device = model, dev
            log.info("speech-to-text: %s on %s (%s)", model_name, dev, compute)
            return
        raise RuntimeError(f"could not load speech-to-text model {model_name}") from last_error

    def transcribe(self, pcm16: bytes) -> str:
        """16 kHz mono int16 PCM -> text."""
        if len(pcm16) < 16_000 * 2 * 0.2:  # under 0.2 s: nothing worth hearing
            return ""
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
