"""Download a Piper voice into ./voices (default: en_GB-alan-medium, a British male voice).

    python brain/scripts/get_voice.py [voice-name]

Browse voices: https://rhasspy.github.io/piper-samples/
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
VOICES = Path(__file__).resolve().parents[2] / "voices"


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "en_GB-alan-medium"
    lang_region, speaker, quality = name.split("-")
    lang = lang_region.split("_")[0]
    VOICES.mkdir(exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        dest = VOICES / f"{name}{suffix}"
        if dest.exists():
            print(f"have {dest.name}")
            continue
        url = f"{BASE}/{lang}/{lang_region}/{speaker}/{quality}/{name}{suffix}"
        print(f"fetching {url}")
        urllib.request.urlretrieve(url, dest)
    print(f"voice ready in {VOICES}")


if __name__ == "__main__":
    main()
