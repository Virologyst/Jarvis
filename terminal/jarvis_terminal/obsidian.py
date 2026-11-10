"""Jarvis working in Obsidian with Sir, through the Local REST API plugin on this machine.

The brain sends FileRequests whose op starts with "obsidian_"; this answers them by calling the
plugin (https://127.0.0.1:27124 by default, with the key from its settings page in
JARVIS_OBSIDIAN_KEY). Going through Obsidian, rather than writing files, means Jarvis can see
which note is open, open notes on screen, and edit a note Sir has open without clobbering it.

Ops (args): obsidian_active, obsidian_read (note, heading), obsidian_edit (note, action, heading,
text), obsidian_open (note), obsidian_search (query), obsidian_canvas (canvas, cards, links).
A note is a vault path ("Projects/J.A.R.V.I.S.md"), a name ("J.A.R.V.I.S"), or "" for the note
open now. Headings are "Parent > Child". Everything answers (ok, text) for Jarvis to use.
Only 127.0.0.1 is ever called: the plugin's certificate is its own, so it isn't checked.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_URL = "https://127.0.0.1:27124"
TIMEOUT_S = 8.0
MAX_TEXT = 12_000
LIST_CACHE_S = 30.0
MAX_FILES = 5000
CARD_W, CARD_H, GAP = 280, 120, 60
NOTE_JSON = "application/vnd.olrapi.note+json"


class ObsidianError(Exception):
    """Something to tell Sir."""


def split_heading(heading: str | None) -> list[str]:
    """'Ideas > Pricing' (or 'Ideas::Pricing', 'Ideas/Pricing') -> ['Ideas', 'Pricing']."""
    if not heading:
        return []
    parts = re.split(r"\s*(?:>|::|/)\s*", heading.strip().strip("#").strip())
    return [p.lstrip("#").strip() for p in parts if p.strip()]


def section(text: str, path: list[str]) -> str | None:
    """The text under a heading path (heading line included), or None if there's no such heading.
    Each later heading must sit inside the one before it."""
    lines = text.splitlines()
    marks = [(i, len(m.group(1)), m.group(2).strip().lower()) for i, line in enumerate(lines)
             if (m := re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line))]
    start, end, level = -1, len(lines), 0
    for want in (p.lower() for p in path):
        hit = next(((i, n) for i, n, title in marks
                    if start < i < end and n > level and title == want), None)
        if hit is None:
            return None
        start, level = hit
        end = next((i for i, n, _ in marks if i > start and n <= level), len(lines))
    return "\n".join(lines[start:end])


def headings(text: str) -> list[str]:
    return [m.group(2).strip() for m in re.finditer(r"^(#{1,6})\s+(.*?)\s*#*\s*$", text, re.M)]


class ObsidianBridge:
    def __init__(self, key: str | None = None, url: str | None = None, opener=None):
        self.key = (key if key is not None else os.environ.get("JARVIS_OBSIDIAN_KEY", "")).strip()
        self.url = (url or os.environ.get("JARVIS_OBSIDIAN_URL") or DEFAULT_URL).rstrip("/")
        host = urllib.parse.urlparse(self.url).hostname
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("JARVIS_OBSIDIAN_URL must be on this machine (127.0.0.1)")
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE   # the plugin's own certificate, on 127.0.0.1 only
        self._open = opener or self._urlopen
        self._files: tuple[float, list[str]] | None = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    # --- HTTP ---------------------------------------------------------------------------------

    def _urlopen(self, req: urllib.request.Request) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=self._ssl) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def _call(self, method: str, path: str, body: str | bytes | None = None,
              headers: dict | None = None, ok: tuple = (200, 204)) -> str:
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", **(headers or {})})
        try:
            status, text = self._open(req)
        except (urllib.error.URLError, OSError) as e:
            raise ObsidianError("Obsidian isn't answering on this computer. Is it open, with the "
                                "Local REST API plugin switched on?") from e
        if status in (401, 403):
            raise ObsidianError("Obsidian refused the key: JARVIS_OBSIDIAN_KEY on this computer "
                                "needs to match the Local REST API plugin's key.")
        if status == 404:
            raise ObsidianError("Obsidian couldn't find that.")
        if status not in ok:
            try:
                why = json.loads(text).get("message") or text
            except ValueError:
                why = text
            raise ObsidianError(f"Obsidian said no ({status}): {str(why)[:300]}")
        return text

    @staticmethod
    def _q(path: str) -> str:
        return urllib.parse.quote(path, safe="/")

    # --- Finding notes ------------------------------------------------------------------------

    def _all_files(self) -> list[str]:
        if self._files and time.monotonic() - self._files[0] < LIST_CACHE_S:
            return self._files[1]
        found, todo = [], [""]
        while todo and len(found) < MAX_FILES:
            folder = todo.pop()
            listing = json.loads(self._call("GET", "/vault/" + self._q(folder))).get("files", [])
            for name in listing:
                full = folder + name
                if name.endswith("/"):
                    if not name.startswith("."):
                        todo.append(full)
                else:
                    found.append(full)
        self._files = (time.monotonic(), found)
        return found

    def resolve(self, note: str, suffix: str = ".md", must_exist: bool = True) -> str:
        """A vault path for what Sir called a note."""
        note = note.strip().replace("\\", "/").strip("/")
        if not note:
            raise ObsidianError("Which note?")
        files = self._all_files()
        wanted = note if note.lower().endswith(suffix) else note + suffix
        exact = [f for f in files if f.lower() == wanted.lower()]
        if exact:
            return exact[0]
        base = wanted.rsplit("/", 1)[-1].lower()
        named = [f for f in files if f.rsplit("/", 1)[-1].lower() == base]
        if len(named) == 1:
            return named[0]
        if not must_exist and not named:
            return wanted
        stem = base[: -len(suffix)]
        close = named or [f for f in files if f.lower().endswith(suffix)
                          and stem in f.rsplit("/", 1)[-1].lower()]
        if len(close) == 1:
            return close[0]
        if close:
            raise ObsidianError("Several notes match: " + ", ".join(close[:8]) + ". Which one?")
        raise ObsidianError(f"There's no note called {note} in the vault.")

    def _target(self, note: str) -> tuple[str, str]:
        """(API path, vault path) for a note; "" is the note open now."""
        if not note.strip():
            data = json.loads(self._call("GET", "/active/", headers={"Accept": NOTE_JSON}))
            return "/active/", data.get("path", "the open note")
        path = self.resolve(note)
        return "/vault/" + self._q(path), path

    # --- Ops ----------------------------------------------------------------------------------

    def handle(self, op: str, args: dict) -> tuple[bool, str]:
        try:
            if not self.available:
                raise ObsidianError("Obsidian isn't connected on this computer yet "
                                    "(JARVIS_OBSIDIAN_KEY isn't set).")
            fn = getattr(self, "op_" + op.removeprefix("obsidian_"), None)
            if fn is None:
                raise ObsidianError(f"I don't know how to {op} in Obsidian.")
            return True, fn(**{k: v for k, v in (args or {}).items() if v is not None})
        except ObsidianError as e:
            return False, str(e)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("obsidian %s: %s", op, e)
            return False, f"That didn't work in Obsidian ({e})."

    def op_active(self) -> str:
        try:
            data = json.loads(self._call("GET", "/active/", headers={"Accept": NOTE_JSON}))
        except ObsidianError as e:
            if "couldn't find" in str(e):
                return "No note is open in Obsidian right now."
            raise
        text = data.get("content", "")
        return (f"Open in Obsidian: {data.get('path')}\n"
                f"Headings: {', '.join(headings(text)) or 'none'}\n---\n{text[:MAX_TEXT]}"
                + ("\n[...cut short]" if len(text) > MAX_TEXT else ""))

    def op_read(self, note: str = "", heading: str = "") -> str:
        api, path = self._target(note)
        text = self._call("GET", api, headers={"Accept": "text/markdown"})
        parts = split_heading(heading)
        if parts:
            found = section(text, parts)
            if found is None:
                raise ObsidianError(f"{path} has no heading {heading}. Its headings: "
                                    + (", ".join(headings(text)) or "none") + ".")
            text = found
        return f"{path}" + (f" > {heading}" if parts else "") + f":\n{text[:MAX_TEXT]}" + (
            "\n[...cut short]" if len(text) > MAX_TEXT else "")

    def op_edit(self, note: str = "", action: str = "append", text: str = "",
                heading: str = "") -> str:
        action = (action or "append").strip().lower()
        if action == "create":
            path = self.resolve(note, must_exist=False)
            if path in self._all_files():
                raise ObsidianError(f"{path} already exists; add to it instead.")
            self._call("PUT", "/vault/" + self._q(path), text,
                       {"Content-Type": "text/markdown"})
            self._files = None
            return f"Created {path}."
        if action not in ("append", "prepend", "replace"):
            raise ObsidianError("The action must be append, prepend, replace or create.")
        if not text.strip():
            raise ObsidianError("There's nothing to write.")
        api, path = self._target(note)
        parts = split_heading(heading)
        if not parts:
            if action == "replace":
                raise ObsidianError("I only replace a section under a heading, never a whole note.")
            if action == "append":
                self._call("POST", api, text if text.startswith("\n") else "\n" + text,
                           {"Content-Type": "text/markdown"})
                return f"Added to the end of {path}."
            current = self._call("GET", api, headers={"Accept": "text/markdown"})
            body = re.match(r"^---\n.*?\n---\n", current, re.S)   # keep front matter on top
            cut = body.end() if body else 0
            self._call("PUT", api, current[:cut] + text.rstrip("\n") + "\n" + current[cut:],
                       {"Content-Type": "text/markdown"})
            return f"Added to the top of {path}."
        self._patch(api, action, parts, text)
        done = {"append": "Added under", "prepend": "Added at the top of", "replace": "Replaced"}
        return f"{done[action]} {' > '.join(parts)} in {path}."

    def _patch(self, api: str, action: str, parts: list[str], text: str) -> None:
        """Edit under a heading, making the heading if it's missing. The plugin's newer API takes
        a JSON instruction; older releases take headers with a :: path. Try new, then old."""
        content = text if text.endswith("\n") else text + "\n"
        try:
            self._call("PATCH", api, json.dumps({
                "operation": action, "targetType": "heading", "target": parts,
                "content": content, "createTargetIfMissing": True}),
                {"Content-Type": "application/vnd.olrapi.patch-instruction+json",
                 "Markdown-Patch-Version": "2"})
            return
        except ObsidianError as e:
            if not re.search(r"\((400|405|415|422)\)", str(e)):
                raise
            log.info("obsidian: newer PATCH refused (%s); trying the older form", e)
        self._call("PATCH", api, content, {
            "Content-Type": "text/markdown", "Operation": action, "Target-Type": "heading",
            "Target": urllib.parse.quote("::".join(parts)), "Target-Delimiter": "::",
            "Create-Target-If-Missing": "true"})

    def op_open(self, note: str) -> str:
        path = self.resolve(note)
        self._call("POST", "/open/" + self._q(path))
        return f"Opened {path} in Obsidian."

    def op_search(self, query: str) -> str:
        if not query.strip():
            raise ObsidianError("What should I look for?")
        q = urllib.parse.urlencode({"query": query, "contextLength": 80})
        results = json.loads(self._call("POST", "/search/simple/?" + q))
        results = sorted(results, key=lambda r: r.get("score", 0), reverse=True)[:10]
        if not results:
            return f"Nothing in the vault mentions {query}."
        lines = []
        for r in results:
            bits = [" ".join(m.get("context", "").split()) for m in r.get("matches", [])[:2]]
            lines.append(f"- {r.get('filename')}: " + " ... ".join(b for b in bits if b))
        return f"Notes mentioning {query}:\n" + "\n".join(lines)

    # --- Canvas boards ------------------------------------------------------------------------

    def op_canvas(self, canvas: str, cards: list | None = None, links: list | None = None) -> str:
        """Add cards (text, optionally near an existing card) and links (from/to card text,
        optional label) to a .canvas board, making the board if it doesn't exist."""
        path = self.resolve(canvas, suffix=".canvas", must_exist=False)
        exists = path in self._all_files()
        board = json.loads(self._call("GET", "/vault/" + self._q(path))) if exists else {}
        nodes, edges = board.setdefault("nodes", []), board.setdefault("edges", [])

        def label(n: dict) -> str:
            return (n.get("text") or n.get("file") or n.get("label") or "").strip()

        def find(text: str) -> dict | None:
            t = " ".join(str(text).split()).lower()
            hit = [n for n in nodes if " ".join(label(n).split()).lower() == t]
            return hit[0] if hit else next(
                (n for n in nodes if t and t in " ".join(label(n).split()).lower()), None)

        def free_spot(near: dict | None) -> tuple[int, int]:
            if near is None:
                bottom = max((n["y"] + n.get("height", CARD_H) for n in nodes), default=-GAP)
                return 0, bottom + GAP
            x = near["x"] + near.get("width", CARD_W) + GAP * 2
            y = near["y"]
            while any(abs(n["x"] - x) < CARD_W and abs(n["y"] - y) < CARD_H for n in nodes):
                y += CARD_H + GAP // 2
            return x, y

        added = 0
        for card in cards or []:
            card = {"text": card} if isinstance(card, str) else dict(card)
            text = str(card.get("text", "")).strip()
            if not text or find(text) and label(find(text)).lower() == text.lower():
                continue
            near = find(card["near"]) if card.get("near") else None
            x, y = free_spot(near)
            node = {"id": os.urandom(8).hex(), "type": "text", "text": text,
                    "x": x, "y": y, "width": CARD_W, "height": CARD_H}
            nodes.append(node)
            added += 1
            if near is not None:
                edges.append({"id": os.urandom(8).hex(), "fromNode": near["id"], "fromSide": "right",
                              "toNode": node["id"], "toSide": "left"})
        linked, missing = 0, []
        for link in links or []:
            a, b = find(link.get("from", "")), find(link.get("to", ""))
            if not a or not b:
                missing.append(f"{link.get('from')} -> {link.get('to')}")
                continue
            edge = {"id": os.urandom(8).hex(), "fromNode": a["id"], "toNode": b["id"]}
            if link.get("label"):
                edge["label"] = str(link["label"])
            edges.append(edge)
            linked += 1
        self._call("PUT", "/vault/" + self._q(path), json.dumps(board, indent="\t"),
                   {"Content-Type": "application/json"})
        self._files = None
        return (f"{'Updated' if exists else 'Made'} the board {path}: {added} card"
                f"{'' if added == 1 else 's'} and {linked} link{'' if linked == 1 else 's'} added."
                + (f" Couldn't find cards for: {'; '.join(missing)}." if missing else ""))
