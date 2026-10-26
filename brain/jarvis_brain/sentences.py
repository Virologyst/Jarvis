"""Split streamed text into sentences so each can be spoken as soon as it's complete."""

from __future__ import annotations

import re

# End of sentence: . ! ? (optionally followed by quotes/brackets) then whitespace.
# Lookbehind skips common abbreviations and decimals like "3.5".
_BOUNDARY = re.compile(r'(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\be\.g)(?<!\bi\.e)[.!?]+["\')\]]*\s+')


class SentenceSplitter:
    def __init__(self, min_chars: int = 12):
        self.buffer = ""
        self.min_chars = min_chars

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return any sentences now complete."""
        self.buffer += text
        out = []
        start = 0
        for m in _BOUNDARY.finditer(self.buffer):
            candidate = self.buffer[start:m.end()].strip()
            if len(candidate) >= self.min_chars:
                out.append(candidate)
                start = m.end()
        self.buffer = self.buffer[start:]
        return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return [rest] if rest else []
