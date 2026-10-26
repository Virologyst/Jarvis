"""Shared wire format between the Jarvis brain and its terminals.

Transport: one WebSocket per terminal.
- Text frames carry JSON control messages (see messages.py).
- Binary frames carry raw audio: 16-bit little-endian mono PCM.
  Terminal -> brain: microphone audio at MIC_SAMPLE_RATE, only between
  ListenStart and ListenEnd.
  Brain -> terminal: speech audio at the rate given by the preceding AudioStart.
"""

from .messages import *  # noqa: F401,F403
from .config import load_env  # noqa: F401

MIC_SAMPLE_RATE = 16_000
PROTOCOL_VERSION = 1
