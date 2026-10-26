"""Download voice files into ./voices.

    python brain/scripts/get_voice.py                     # Kokoro (the default voice engine)
    python brain/scripts/get_voice.py kokoro              # same
    python brain/scripts/get_voice.py en_GB-alan-medium   # a Piper voice

Kokoro voices: bm_daniel, bm_george, bm_lewis, bm_fable, bf_emma, bf_isabella, bf_alice, bf_lily
(British); see https://github.com/thewh1teagle/kokoro-onnx. Piper voices:
https://rhasspy.github.io/piper-samples/
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")   # ~310 MB + ~27 MB
VOICES = Path(__file__).resolve().parents[2] / "voices"


def fetch(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"have {dest.name}")
        return
    print(f"fetching {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "kokoro"
    VOICES.mkdir(exist_ok=True)
    if name == "kokoro":
        for f in KOKORO_FILES:
            fetch(f"{KOKORO_BASE}/{f}", VOICES / f)
    else:
        lang_region, speaker, quality = name.split("-")
        lang = lang_region.split("_")[0]
        for suffix in (".onnx", ".onnx.json"):
            fetch(f"{PIPER_BASE}/{lang}/{lang_region}/{speaker}/{quality}/{name}{suffix}",
                  VOICES / f"{name}{suffix}")
    print(f"voice ready in {VOICES}")


if __name__ == "__main__":
    main()
