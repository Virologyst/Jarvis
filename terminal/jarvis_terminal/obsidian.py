"""Jarvis working in Obsidian with Sir, through the Local REST API plugin on this machine.

The brain sends FileRequests whose op starts with "obsidian_"; this answers them by calling the
plugin (https://127.0.0.1:27124 by default, with the key from its settings page in
JARVIS_OBSIDIAN_KEY). Going through Obsidian, rather than writing files, means Jarvis can see
which note is open, open notes on screen, and edit a note Sir has open without clobbering it.

Ops (args): obsidian_active, obsidian_read (note, heading), obsidian_edit (note, action, heading,
text), obsidian_project (note, progress, status, next, log_entry: a project note's properties,
its coloured progress bar and log), obsidian_open (note), obsidian_search (query),
obsidian_canvas (canvas, cards, links,
edit_cards, remove_cards, edit_links, remove_links, preview).
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
from datetime import date

from jarvis_protocol.progress import refresh_bar

log = logging.getLogger(__name__)
STATUSES = ("idea", "planned", "active", "on hold", "done")

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


def section_lines(lines: list[str], path: list[str]) -> tuple[int, int] | None:
    """(heading line, line after the section) for a heading path, or None if there's no such
    heading. Each later heading must sit inside the one before it."""
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
    return start, end


def section(text: str, path: list[str]) -> str | None:
    """The text under a heading path (heading line included), or None if there's no such heading."""
    lines = text.splitlines()
    found = section_lines(lines, path)
    return None if found is None else "\n".join(lines[found[0]:found[1]])


def insert(text: str, new: str, path: list[str], at_top: bool) -> str | None:
    """The note with `new` added at the end (or top) of a heading's section, or of the whole note
    (path empty; the top is below any front matter), keeping lists tight: no blank line between
    the last bullet and the new one. None if the heading isn't there."""
    lines = text.splitlines()
    if path:
        found = section_lines(lines, path)
        if found is None:
            return None
        start, end = found
        start += 1
    else:
        front = re.match(r"^---\n.*?\n---\n", text, re.S)
        start, end = (front.group(0).count("\n") if front else 0), len(lines)
    if at_top:
        spot = start
    else:
        spot = end
        while spot > start and not lines[spot - 1].strip():
            spot -= 1
    added = new.strip("\n").splitlines()
    out = lines[:spot] + added + lines[spot:]
    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")


def _front(text: str) -> tuple[str, str] | None:
    m = re.match(r"^---\n(.*?\n)---\n", text, re.S)
    return (m.group(1), text[m.end():]) if m else None


def _get_prop(front: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", front, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def _set_prop(front: str, key: str, value: str) -> str:
    line = f"{key}: {value}".rstrip()
    if re.search(rf"^{re.escape(key)}:", front, re.M):
        return re.sub(rf"^{re.escape(key)}:.*$", lambda _: line, front, count=1, flags=re.M)
    return front + line + "\n"


def project_update(text: str, progress="", status="", next_step="", log_entry="",
                   today: str = "") -> tuple[str, list[str]]:
    """The note with its project properties changed, the bar redrawn and the changes logged
    (newest first, under the Log heading). Returns (text, what changed)."""
    parts = _front(text.replace("\r\n", "\n"))
    if parts is None:
        raise ObsidianError("That note has no project properties at the top.")
    front, body = parts
    said, entries = [], []
    if str(progress).strip():
        m = re.search(r"\d+", str(progress))
        if not m:
            raise ObsidianError("Progress is a percentage, 0 to 100.")
        value, old = str(max(0, min(100, int(m.group())))), _get_prop(front, "progress") or "0"
        if value != old:
            front = _set_prop(front, "progress", value)
            said.append(f"progress {value}%")
            entries.append(f"Progress: {old}% → {value}%")
    if status.strip():
        want = status.strip().lower().replace("_", " ")
        want = {"hold": "on hold", "paused": "on hold", "finished": "done", "complete": "done",
                "completed": "done", "in progress": "active"}.get(want, want)
        if want not in STATUSES:
            raise ObsidianError(f"Status can be {', '.join(STATUSES)}.")
        if want != _get_prop(front, "status"):
            entries.append(f"Status: {_get_prop(front, 'status') or 'none'} → {want}")
            front = _set_prop(front, "status", want)
            said.append(f"status {want}")
    nxt = " ".join(str(next_step).split())
    if nxt and nxt != _get_prop(front, "next"):
        front = _set_prop(front, "next", nxt)
        said.append(f"next step '{nxt}'")
        entries.append(f"Next step: {nxt}")
    entry = " ".join(str(log_entry).split())
    if entry:
        entries.append(entry)
        said.append("noted it in the log")
    if not said:
        return text, []
    today = today or date.today().isoformat()
    front = _set_prop(front, "updated", today)
    body = refresh_bar(body, _get_prop(front, "progress") or 0)
    lines = body.split("\n")
    at = next((i for i, ln in enumerate(lines) if re.match(r"^#{1,6}\s+Log\s*$", ln)), None)
    new = [f"- {today}: {e}" for e in entries]
    if at is None:
        lines = [*lines[:len(lines) - (1 if lines and not lines[-1] else 0)], "", "## Log", *new, ""]
    else:
        lines[at + 1:at + 1] = new
    return "---\n" + front + "---\n" + "\n".join(lines), said


def headings(text: str) -> list[str]:
    return [m.group(2).strip() for m in re.finditer(r"^(#{1,6})\s+(.*?)\s*#*\s*$", text, re.M)]


# --- Changing canvas boards ---------------------------------------------------------------------
# Obsidian's preset card colours are "1" to "6"; any "#rrggbb" works too.
COLOURS = {"red": "1", "orange": "2", "yellow": "3", "green": "4", "cyan": "5", "blue": "5",
           "teal": "5", "purple": "6", "violet": "6"}
COLOUR_NAMES = {"1": "red", "2": "orange", "3": "yellow", "4": "green", "5": "cyan", "6": "purple"}
NO_COLOUR = {"none", "no colour", "no color", "default", "clear", "remove", "plain"}


def _words(text: str) -> str:
    return " ".join(str(text).split()).lower()


def card_name(n: dict) -> str:
    """What a card shows: its text, file, web address or group label."""
    return str(n.get("text") or n.get("file") or n.get("url") or n.get("label") or "").strip()


def _show(n: dict) -> str:
    name = " ".join(card_name(n).split())
    name = name if len(name) <= 60 else name[:57] + "..."
    what = f'"{name}"' if name else f"an empty {n.get('type', '')} card"
    return f"{what} (id {n.get('id')})"


def find_card(nodes: list, ref) -> dict:
    """The card meant by an id, its exact text, or a part of its text only one card has."""
    want = _words(ref or "")
    if not want:
        raise ObsidianError("Which card? Give its text or id.")
    cards = [n for n in nodes if isinstance(n, dict)]
    for pool in ([n for n in cards if str(n.get("id", "")).lower() == want],
                 [n for n in cards if _words(card_name(n)) == want],
                 [n for n in cards if want in _words(card_name(n))]):
        if len(pool) == 1:
            return pool[0]
        if pool:
            raise ObsidianError(f'Several cards match "{ref}": '
                                + ", ".join(_show(n) for n in pool[:10]) + ". Which one?")
    raise ObsidianError(f'No card matches "{ref}". The cards are: '
                        + (", ".join(_show(n) for n in cards[:15]) or "none")
                        + (" and more" if len(cards) > 15 else "") + ".")


def _show_link(e: dict, nodes: list) -> str:
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}

    def end(key: str) -> str:
        n = by_id.get(e.get(key))
        return f'"{" ".join(card_name(n).split())[:40]}"' if n else "a missing card"

    text = f"{end('fromNode')} -> {end('toNode')}"
    return text + (f' labelled "{e["label"]}"' if e.get("label") else "") + f" (id {e.get('id')})"


def find_link(nodes: list, edges: list, item: dict) -> dict:
    """The link meant by its id, or by the cards it joins (either way round), narrowed by its
    current label if several join them."""
    links = [e for e in edges if isinstance(e, dict)]
    if item.get("id"):
        hit = [e for e in links if str(e.get("id")) == str(item["id"]).strip()]
        if hit:
            return hit[0]
        raise ObsidianError(f"No link has the id {item['id']}.")
    a, b = find_card(nodes, item.get("from")), find_card(nodes, item.get("to"))
    pool = [e for e in links if e.get("fromNode") == a["id"] and e.get("toNode") == b["id"]] or \
        [e for e in links if e.get("fromNode") == b["id"] and e.get("toNode") == a["id"]]
    was = _words(item.get("current_label") or "")
    if was and len(pool) > 1:
        pool = [e for e in pool if _words(e.get("label") or "") == was] or pool
    if len(pool) == 1:
        return pool[0]
    if pool:
        raise ObsidianError(f"Several links join {_show(a)} and {_show(b)}: "
                            + ", ".join(_show_link(e, nodes) for e in pool) + ". Which one?")
    near = [e for e in links if {e.get("fromNode"), e.get("toNode")} & {a["id"], b["id"]}]
    raise ObsidianError(f"No link joins {_show(a)} and {_show(b)}. Their links: "
                        + (", ".join(_show_link(e, nodes) for e in near[:12]) or "none") + ".")


def _number(value, what: str, positive: bool = False) -> float | int:
    try:
        n = float(str(value).strip())
    except ValueError:
        raise ObsidianError(f"The {what} must be a number, not {value}.") from None
    if n != n or n in (float("inf"), float("-inf")) or (positive and n <= 0):
        raise ObsidianError(f"The {what} must be a {'positive ' if positive else ''}number.")
    return int(n) if n == int(n) else n


def _colour(value) -> str | None:
    """Obsidian's colour value for what Sir said, or None to take the colour off."""
    c = _words(value).replace("colour ", "").replace("color ", "")
    if c in NO_COLOUR or not c:
        return None
    if c in COLOURS:
        return COLOURS[c]
    if c in COLOUR_NAMES or re.fullmatch(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})", c):
        return c
    raise ObsidianError(f"I can't colour a card {value}: the colours are red, orange, yellow, "
                        "green, cyan, purple, a #hex colour, or none.")


def canvas_changes(nodes: list, edges: list, edit_cards=None, remove_cards=None,
                   edit_links=None, remove_links=None) -> list[str]:
    """Change cards and links on a board in place and say what changed. Everything is found
    and checked first, so a card or link that can't be found changes nothing."""
    plan: list = []
    for item in edit_cards or []:
        item = item if isinstance(item, dict) else {"card": item}
        node = find_card(nodes, item.get("card") or item.get("id"))
        change = {}
        if item.get("text") is not None:
            if node.get("type", "text") not in ("text", "group"):
                raise ObsidianError(f"{_show(node)} shows a {node.get('type')}, so its text "
                                    "can't be changed; remove it and add a card instead.")
            change["text"] = str(item["text"]).strip()
            if not change["text"]:
                raise ObsidianError(f"The new text for {_show(node)} is empty; remove the card "
                                    "instead?")
        if item.get("color", item.get("colour")) is not None:
            change["color"] = _colour(item.get("color", item.get("colour")))
        for key in ("x", "y"):
            if item.get(key) is not None:
                change[key] = _number(item[key], key)
        for key in ("width", "height"):
            if item.get(key) is not None:
                change[key] = _number(item[key], key, positive=True)
        if not change:
            raise ObsidianError(f"What should change on {_show(node)}? Its text, colour, place "
                                "or size.")
        plan.append(("card", node, change))
    for item in edit_links or []:
        if isinstance(item, dict):
            plan.append(("link", find_link(nodes, edges, item), str(item.get("label") or "").strip()))
    gone_cards = {}
    for ref in remove_cards or []:
        node = find_card(nodes, ref.get("card") or ref.get("id") if isinstance(ref, dict) else ref)
        gone_cards[node["id"]] = node
    gone_links = {}
    for item in remove_links or []:
        if isinstance(item, dict):
            e = find_link(nodes, edges, item)
            gone_links[id(e)] = e

    done = []
    for kind, target, change in plan:
        before = _show(target) if kind == "card" else _show_link(target, nodes)
        if kind == "link":
            if change:
                target["label"] = change
                done.append(f'labelled the link {before} "{change}"')
            else:
                target.pop("label", None)
                done.append(f"took the label off the link {before}")
            continue
        if "text" in change:
            target["label" if target.get("type") == "group" else "text"] = change["text"]
            done.append(f'changed {before} to "{change["text"]}"')
        if "color" in change:
            if change["color"] is None:
                target.pop("color", None)
                done.append(f"took the colour off {before}")
            else:
                target["color"] = change["color"]
                done.append(f"coloured {before} {COLOUR_NAMES.get(change['color'], change['color'])}")
        if "x" in change or "y" in change:
            target.update({k: change[k] for k in ("x", "y") if k in change})
            done.append(f"moved {before} to {target.get('x')}, {target.get('y')}")
        if "width" in change or "height" in change:
            target.update({k: change[k] for k in ("width", "height") if k in change})
            done.append(f"resized {before} to {target.get('width')} by {target.get('height')}")
    for e in gone_links.values():
        done.append(f"removed the link {_show_link(e, nodes)}")
    for node_id, node in gone_cards.items():
        attached = [e for e in edges if isinstance(e, dict) and id(e) not in gone_links
                    and node_id in (e.get("fromNode"), e.get("toNode"))]
        done.append(f"removed the card {_show(node)}"
                    + (f" and its {len(attached)} link{'' if len(attached) == 1 else 's'}"
                       if attached else ""))
        gone_links.update({id(e): e for e in attached})
    if gone_cards or gone_links:
        edges[:] = [e for e in edges if id(e) not in gone_links]
        nodes[:] = [n for n in nodes if not (isinstance(n, dict) and n.get("id") in gone_cards)]
    return done


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

    def _urlopen(self, req: urllib.request.Request) -> tuple[int, str, dict]:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=self._ssl) as r:
                return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})

    def _call(self, method: str, path: str, body: str | bytes | None = None,
              headers: dict | None = None, ok: tuple = (200, 204)) -> str:
        return self._exchange(method, path, body, headers, ok)[0]

    def _exchange(self, method: str, path: str, body: str | bytes | None = None,
                  headers: dict | None = None, ok: tuple = (200, 204)) -> tuple[str, dict]:
        """(text, response headers)."""
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", **(headers or {})})
        try:
            status, text, *rest = self._open(req)
            reply_headers = {k.lower(): v for k, v in (rest[0] if rest else {}).items()}
        except (urllib.error.URLError, OSError) as e:
            raise ObsidianError("Obsidian isn't answering on this computer. Is it open, with the "
                                "Local REST API plugin switched on?") from e
        if status in (401, 403):
            raise ObsidianError("Obsidian refused the key: JARVIS_OBSIDIAN_KEY on this computer "
                                "needs to match the Local REST API plugin's key.")
        if status == 404:
            raise ObsidianError("Obsidian couldn't find that.")
        if status == 412:
            raise ObsidianError("The note kept changing as I wrote (412), so I left it alone. "
                                "Try again in a moment.")
        if status not in ok:
            try:
                why = json.loads(text).get("message") or text
            except ValueError:
                why = text
            raise ObsidianError(f"Obsidian said no ({status}): {str(why)[:300]}")
        return text, reply_headers

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
        if action == "replace":
            if not parts:
                raise ObsidianError("I only replace a section under a heading, never a whole note.")
            self._patch(api, action, parts, text)
            return f"Replaced {' > '.join(parts)} in {path}."
        if not self._splice(api, text, parts, action == "prepend"):
            self._patch(api, action, parts, text)       # no such heading: the plugin makes it
        if parts:
            where = ("at the top of " if action == "prepend" else "under ") + " > ".join(parts)
            return f"Added {where} in {path}."
        return f"Added to the {'top' if action == 'prepend' else 'end'} of {path}."

    def _splice(self, api: str, text: str, parts: list[str], at_top: bool) -> bool:
        """Add text into the note ourselves (the plugin's own append leaves a blank line that
        splits a list), saved only if the note hasn't changed since it was read (If-Match), so
        Sir's typing is never overwritten; tries twice. False if the heading isn't there."""
        for attempt in range(2):
            current, info = self._exchange("GET", api, headers={"Accept": "text/markdown"})
            updated = insert(current, text, parts, at_top)
            if updated is None:
                return False
            tag = info.get("etag")
            try:
                self._call("PUT", api, updated, {"Content-Type": "text/markdown",
                                                 **({"If-Match": tag} if tag else {})})
                return True
            except ObsidianError as e:
                if "(412)" not in str(e) or attempt:
                    raise
                log.info("obsidian: note changed while writing; trying again")
        return True

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

    def op_project(self, note: str = "", progress: str = "", status: str = "", next: str = "",
                   log_entry: str = "") -> str:
        """Update a project note's progress (and its coloured bar), status, next step and log,
        like the brain's vault_update_project, live through Obsidian; saved only if the note
        hasn't changed since it was read."""
        api, path = self._target(note)
        for attempt in range(2):
            current, info = self._exchange("GET", api, headers={"Accept": "text/markdown"})
            updated, said = project_update(current, progress, status, next, log_entry,
                                           date.today().isoformat())
            if not said:
                return f"Nothing changed in {path}."
            tag = info.get("etag")
            try:
                self._call("PUT", api, updated, {"Content-Type": "text/markdown",
                                                 **({"If-Match": tag} if tag else {})})
                return f"Updated {path}: {', '.join(said)}."
            except ObsidianError as e:
                if "(412)" not in str(e) or attempt:
                    raise
        return f"Updated {path}."

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

    def op_canvas(self, canvas: str, cards: list | None = None, links: list | None = None,
                  edit_cards: list | None = None, remove_cards: list | None = None,
                  edit_links: list | None = None, remove_links: list | None = None,
                  preview: bool = False) -> str:
        """Add cards (text, optionally near an existing card) and links (from/to card text,
        optional label) to a .canvas board, making the board if it doesn't exist. Also edits
        existing cards (text, colour, position, size), removes cards (with their links), and
        changes, unlabels or removes links. Every card or link to edit or remove is found
        before anything changes; one that's missing or ambiguous stops the whole change with
        the candidates. preview says what would change without saving."""
        path = self.resolve(canvas, suffix=".canvas", must_exist=False)
        exists = path in self._all_files()
        board = json.loads(self._call("GET", "/vault/" + self._q(path))) if exists else {}
        if not isinstance(board, dict):
            raise ObsidianError(f"{path} isn't a canvas board I can read.")
        changing = edit_cards or remove_cards or edit_links or remove_links
        if changing and not exists:
            raise ObsidianError(f"There's no board called {canvas} to change.")
        if changing and not all(isinstance(board.get(k, []), list) for k in ("nodes", "edges")):
            raise ObsidianError(f"{path} isn't a canvas board I can read.")
        nodes, edges = board.setdefault("nodes", []), board.setdefault("edges", [])

        def label(n: dict) -> str:
            return (n.get("text") or n.get("file") or n.get("label") or "").strip()

        done = canvas_changes(nodes, edges, edit_cards, remove_cards, edit_links,
                              remove_links) if changing else []
        if preview:
            n_cards, n_links = len(cards or []), len(links or [])
            if n_cards or n_links:
                done.append(f"add up to {n_cards} card{'' if n_cards == 1 else 's'}"
                            f" and {n_links} link{'' if n_links == 1 else 's'}")
            return (f"On the board {path} I would: " + "; ".join(done) + "."
                    if done else f"Nothing to change on the board {path}.")

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
        self._call("PUT", "/vault/" + self._q(path),
                   json.dumps(board, indent="\t", ensure_ascii=False),
                   {"Content-Type": "text/markdown"})   # the plugin mangles application/json
        self._files = None
        if done and not (cards or links):
            return f"Updated the board {path}: " + "; ".join(done) + "."
        return (f"{'Updated' if exists else 'Made'} the board {path}: "
                + ("".join(d + "; " for d in done))
                + f"{added} card{'' if added == 1 else 's'} and {linked} link"
                f"{'' if linked == 1 else 's'} added."
                + (f" Couldn't find cards for: {'; '.join(missing)}." if missing else ""))
