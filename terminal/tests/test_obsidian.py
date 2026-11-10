"""The Obsidian bridge against a fake Local REST API (no Obsidian needed)."""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_terminal.obsidian import ObsidianBridge, section, split_heading  # noqa: E402

NOTE = "---\nstatus: active\n---\n# J.A.R.V.I.S\nIntro\n## Ideas\n- voice\n## Log\n- started\n"


class FakeObsidian:
    """A tiny vault behind the plugin's API: records calls, keeps files in a dict."""

    def __init__(self, files=None, active="Projects/J.A.R.V.I.S.md", old_patch=False):
        self.files = dict(files or {"Projects/J.A.R.V.I.S.md": NOTE, "Home.md": "# Home\n",
                                    "Templates/Project.md": "x", ".obsidian/app.json": "{}"})
        self.active = active
        self.old_patch = old_patch
        self.calls = []

    def __call__(self, req):
        url = urllib.parse.urlparse(req.full_url)
        path, method = urllib.parse.unquote(url.path), req.get_method()
        body = (req.data or b"").decode()
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((method, path, headers, body))
        if headers.get("authorization") != "Bearer k":
            return 401, "{}"
        if path == "/active/":
            path = "/vault/" + self.active if self.active else None
            if path is None:
                return 404, "{}"
        if path.startswith("/vault/") and path.endswith("/") or path == "/vault/":
            folder = path[len("/vault/"):]
            names = {f[len(folder):].split("/")[0] + ("/" if "/" in f[len(folder):] else "")
                     for f in self.files if f.startswith(folder)}
            return 200, json.dumps({"files": sorted(names)})
        if path.startswith("/vault/"):
            name = path[len("/vault/"):]
            if method == "GET":
                if name not in self.files:
                    return 404, "{}"
                if "note+json" in headers.get("accept", ""):
                    return 200, json.dumps({"path": name, "content": self.files[name]})
                return 200, self.files[name]
            if method == "PUT":
                self.files[name] = body
                return 204, ""
            if method == "POST":
                self.files[name] += body
                return 204, ""
            if method == "PATCH":
                if "patch-instruction" in headers.get("content-type", ""):
                    if self.old_patch:
                        return 400, json.dumps({"message": "unknown content type"})
                    ins = json.loads(body)
                    target, op, text = ins["target"], ins["operation"], ins["content"]
                else:
                    target = urllib.parse.unquote(headers["target"]).split("::")
                    op, text = headers["operation"], body
                found = section(self.files[name], target)
                if found is None:
                    self.files[name] += f"\n## {target[-1]}\n{text}"
                elif op == "append":
                    self.files[name] = self.files[name].replace(found, found + "\n" + text.rstrip("\n"))
                elif op == "replace":
                    head = found.splitlines()[0]
                    self.files[name] = self.files[name].replace(found, head + "\n" + text.rstrip("\n"))
                return 200, ""
        if path.startswith("/open/"):
            return 200, ""
        if path == "/search/simple/":
            q = urllib.parse.parse_qs(url.query)["query"][0]
            return 200, json.dumps([{"filename": f, "score": 1, "matches": [
                {"context": c[max(0, c.find(q) - 5): c.find(q) + 20]}]}
                for f, c in self.files.items() if q in c])
        return 404, "{}"


def bridge(**kw):
    fake = FakeObsidian(**kw)
    return ObsidianBridge("k", "https://127.0.0.1:27124", opener=fake), fake


def test_only_this_machine_and_needs_a_key():
    with pytest.raises(ValueError):
        ObsidianBridge("k", "https://example.com:27124")
    b = ObsidianBridge("", "https://127.0.0.1:27124")
    assert not b.available
    assert b.handle("obsidian_active", {}) == (
        False, "Obsidian isn't connected on this computer yet (JARVIS_OBSIDIAN_KEY isn't set).")


def test_open_note_and_reading_a_section():
    b, _ = bridge()
    ok, text = b.handle("obsidian_active", {})
    assert ok and text.startswith("Open in Obsidian: Projects/J.A.R.V.I.S.md\n"
                                  "Headings: J.A.R.V.I.S, Ideas, Log\n")
    ok, text = b.handle("obsidian_read", {"note": "jarvis", "heading": "Ideas"})
    assert not ok and "no note called jarvis" in text
    ok, text = b.handle("obsidian_read", {"note": "j.a.r.v.i.s", "heading": "J.A.R.V.I.S > Ideas"})
    assert ok and text == "Projects/J.A.R.V.I.S.md > J.A.R.V.I.S > Ideas:\n## Ideas\n- voice"
    ok, text = b.handle("obsidian_read", {"note": "Home", "heading": "Nope"})
    assert not ok and "has no heading Nope. Its headings: Home." in text


def test_writing_under_headings_new_and_old_api():
    for old in (False, True):
        b, fake = bridge(old_patch=old)
        ok, text = b.handle("obsidian_edit", {"action": "append", "heading": "Ideas",
                                              "text": "- mind mapping\n  - canvas"})
        assert ok and text == "Added under Ideas in Projects/J.A.R.V.I.S.md."
        assert "- voice\n- mind mapping\n  - canvas" in fake.files["Projects/J.A.R.V.I.S.md"]
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert patches[-1][2]["target-delimiter"] == "::"


def test_append_prepend_create_and_never_replace_a_whole_note():
    b, fake = bridge()
    assert b.handle("obsidian_edit", {"note": "Home", "text": "- a"})[1] == "Added to the end of Home.md."
    assert fake.files["Home.md"] == "# Home\n\n- a"
    b.handle("obsidian_edit", {"action": "prepend", "text": "Top line"})
    assert fake.files["Projects/J.A.R.V.I.S.md"].startswith("---\nstatus: active\n---\nTop line\n# J")
    ok, text = b.handle("obsidian_edit", {"note": "Home", "action": "replace", "text": "x"})
    assert not ok and "never a whole note" in text
    assert b.handle("obsidian_edit", {"note": "Ideas/Pricing", "action": "create",
                                      "text": "# Pricing"}) == (True, "Created Ideas/Pricing.md.")
    ok, text = b.handle("obsidian_edit", {"note": "Home", "action": "create", "text": "x"})
    assert not ok and "already exists" in text


def test_open_and_search():
    b, fake = bridge()
    assert b.handle("obsidian_open", {"note": "home"}) == (True, "Opened Home.md in Obsidian.")
    assert ("POST", "/open/Home.md") in [(c[0], c[1]) for c in fake.calls]
    ok, text = b.handle("obsidian_search", {"query": "voice"})
    assert ok and text.startswith("Notes mentioning voice:\n- Projects/J.A.R.V.I.S.md:")


def test_canvas_grows_a_map():
    b, fake = bridge()
    ok, text = b.handle("obsidian_canvas", {"canvas": "Ideas board", "cards": [
        {"text": "V Source"}, {"text": "Pricing", "near": "V Source"},
        {"text": "Hosting", "near": "V Source"}],
        "links": [{"from": "Pricing", "to": "Hosting", "label": "affects"},
                  {"from": "Pricing", "to": "Nowhere"}]})
    assert ok and text == ("Made the board Ideas board.canvas: 3 cards and 1 link added. "
                           "Couldn't find cards for: Pricing -> Nowhere.")
    board = json.loads(fake.files["Ideas board.canvas"])
    root, pricing, hosting = board["nodes"]
    assert pricing["x"] > root["x"] and hosting["y"] > pricing["y"]          # no overlap
    assert len(board["edges"]) == 3 and board["edges"][-1]["label"] == "affects"
    ok, text = b.handle("obsidian_canvas", {"canvas": "ideas board", "cards": [{"text": "Pricing"}]})
    assert text.startswith("Updated the board Ideas board.canvas: 0 cards")


def test_wrong_key_and_obsidian_closed():
    fake = FakeObsidian()
    b = ObsidianBridge("wrong", "https://127.0.0.1:27124", opener=fake)
    ok, text = b.handle("obsidian_active", {})
    assert not ok and "refused the key" in text

    def closed(req):
        raise OSError("refused")

    b = ObsidianBridge("k", "https://127.0.0.1:27124", opener=closed)
    assert "Is it open" in b.handle("obsidian_active", {})[1]


def test_heading_helpers():
    assert split_heading("Ideas > Pricing") == ["Ideas", "Pricing"]
    assert split_heading("## Ideas::Pricing") == ["Ideas", "Pricing"]
    assert section("# A\n## B\nx\n# C\n## B\ny", ["C", "B"]) == "## B\ny"
