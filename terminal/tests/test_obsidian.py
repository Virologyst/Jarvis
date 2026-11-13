"""The Obsidian bridge against a fake Local REST API (no Obsidian needed)."""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_terminal.obsidian import (ObsidianBridge, ObsidianError, cut, section,  # noqa: E402
                                      split_heading)

NOTE = "---\nstatus: active\n---\n# J.A.R.V.I.S\nIntro\n## Ideas\n- voice\n## Log\n- started\n"


class FakeObsidian:
    """A tiny vault behind the plugin's API: records calls, keeps files in a dict."""

    def __init__(self, files=None, active="Projects/J.A.R.V.I.S.md", old_patch=False):
        self.files = dict(files or {"Projects/J.A.R.V.I.S.md": NOTE, "Home.md": "# Home\n",
                                    "Templates/Project.md": "x", ".obsidian/app.json": "{}"})
        self.active = active
        self.old_patch = old_patch
        self.typing = 0        # saves to refuse (412) because "Sir typed" in between
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
                return 200, self.files[name], {"ETag": str(hash(self.files[name]))}
            if method == "PUT":
                if "if-match" in headers:
                    if self.typing:
                        self.typing -= 1
                        self.files[name] = self.files[name].replace("# Home\n", "# Home\ntyped\n")
                        return 412, json.dumps({"message": "changed"})
                    assert headers["if-match"] == str(hash(self.files[name]))
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


def test_adding_under_a_heading_keeps_the_list_tight():
    b, fake = bridge()
    ok, text = b.handle("obsidian_edit", {"action": "append", "heading": "Ideas",
                                          "text": "- mind mapping\n  - canvas"})
    assert ok and text == "Added under Ideas in Projects/J.A.R.V.I.S.md."
    assert "## Ideas\n- voice\n- mind mapping\n  - canvas\n## Log" in \
        fake.files["Projects/J.A.R.V.I.S.md"]
    b.handle("obsidian_edit", {"action": "prepend", "heading": "Log", "text": "- newest"})
    assert "## Log\n- newest\n- started\n" in fake.files["Projects/J.A.R.V.I.S.md"]
    assert not any(c[0] == "PATCH" for c in fake.calls)


def test_missing_headings_and_replace_use_the_plugin_new_and_old_api():
    for old in (False, True):
        b, fake = bridge(old_patch=old)
        ok, text = b.handle("obsidian_edit", {"action": "append", "heading": "Risks",
                                              "text": "- sync"})
        assert ok and "## Risks\n- sync" in fake.files["Projects/J.A.R.V.I.S.md"]
        ok, text = b.handle("obsidian_edit", {"action": "replace", "heading": "Log",
                                              "text": "- redone"})
        assert text == "Replaced Log in Projects/J.A.R.V.I.S.md."
        assert "## Log\n- redone" in fake.files["Projects/J.A.R.V.I.S.md"]
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    assert patches[-1][2]["target-delimiter"] == "::"


def test_never_overwrites_typing_that_happened_meanwhile():
    b, fake = bridge()
    fake.typing = 1        # Sir types once between Jarvis reading and saving
    b.handle("obsidian_edit", {"note": "Home", "text": "- a"})
    assert fake.files["Home.md"] == "# Home\ntyped\n- a\n"
    fake.typing = 5        # keeps typing: give up rather than overwrite
    ok, text = b.handle("obsidian_edit", {"note": "Home", "text": "- b"})
    assert not ok and "(412)" in text and "- b" not in fake.files["Home.md"]


def test_append_prepend_create_and_never_replace_a_whole_note():
    b, fake = bridge()
    assert b.handle("obsidian_edit", {"note": "Home", "text": "- a"})[1] == "Added to the end of Home.md."
    assert fake.files["Home.md"] == "# Home\n- a\n"
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


BOARD = {
    "nodes": [
        {"id": "g1", "type": "group", "label": "Brain PC", "x": -50, "y": -50, "width": 900,
         "height": 500, "color": "4"},
        {"id": "a1", "type": "text", "text": "Speech to text", "x": 0, "y": 0, "width": 250,
         "height": 60, "styleAttributes": {"textAlign": "center"}},
        {"id": "b2", "type": "text", "text": "Text to speech (Piper)", "x": 400, "y": 0,
         "width": 250, "height": 60, "color": "1"},
        {"id": "c3", "type": "text", "text": "Text to speech notes", "x": 400, "y": 200,
         "width": 250, "height": 60},
        {"id": "f4", "type": "file", "file": "Projects/J.A.R.V.I.S.md", "x": 0, "y": 300,
         "width": 400, "height": 400},
        {"id": "l5", "type": "link", "url": "https://example.com/é", "x": 500, "y": 300,
         "width": 400, "height": 300},
    ],
    "edges": [
        {"id": "e1", "fromNode": "a1", "fromSide": "right", "toNode": "b2", "toSide": "left",
         "label": "then", "toEnd": "arrow"},
        {"id": "e2", "fromNode": "b2", "toNode": "c3"},
        {"id": "e3", "fromNode": "f4", "toNode": "l5", "color": "2"},
    ],
    "extra": {"kept": True},
}


def board_bridge():
    b, fake = bridge()
    fake.files["Boards/J.A.R.V.I.S.canvas"] = json.dumps(BOARD)
    return b, fake


def saved(fake):
    return json.loads(fake.files["Boards/J.A.R.V.I.S.canvas"])


def test_canvas_edits_cards_and_keeps_everything_else():
    b, fake = board_bridge()
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "edit_cards": [
        {"card": "speech to text", "text": "Speech to text (Whisper)", "color": "purple"},
        {"card": "b2", "color": "none", "x": 420, "y": "10", "width": 300},
        {"card": "Brain PC", "text": "Brain PC (4090)"}]})
    assert ok, text
    assert text.startswith("Updated the board Boards/J.A.R.V.I.S.canvas: ")
    assert 'changed "Speech to text" (id a1) to "Speech to text (Whisper)"' in text
    assert "coloured" in text and "purple" in text and "took the colour off" in text
    assert "moved" in text and "420, 10" in text and "resized" in text and "300 by 60" in text
    board = saved(fake)
    nodes = {n["id"]: n for n in board["nodes"]}
    assert nodes["a1"] == {**BOARD["nodes"][1], "text": "Speech to text (Whisper)", "color": "6"}
    assert nodes["b2"] == {"id": "b2", "type": "text", "text": "Text to speech (Piper)",
                           "x": 420, "y": 10, "width": 300, "height": 60}
    assert nodes["g1"] == {**BOARD["nodes"][0], "label": "Brain PC (4090)"}
    assert "text" not in nodes["g1"]
    assert [n for n in board["nodes"] if n["id"] in ("c3", "f4", "l5")] == BOARD["nodes"][3:]
    assert board["edges"] == BOARD["edges"] and board["extra"] == {"kept": True}
    assert "https://example.com/é" in fake.files["Boards/J.A.R.V.I.S.canvas"]   # not escaped


def test_canvas_removes_cards_with_their_links_and_edits_links():
    b, fake = board_bridge()
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S",
                                            "edit_links": [{"from": "Speech to text", "to": "Piper",
                                                            "label": "speaks via"}],
                                            "remove_links": [{"from": "example.com",
                                                              "to": "J.A.R.V.I.S.md"}],
                                            "remove_cards": ["notes"]})
    assert ok, text
    assert 'labelled the link "Speech to text" -> "Text to speech (Piper)"' in text
    assert "removed the link" in text and 'removed the card "Text to speech notes" (id c3) ' \
        "and its 1 link" in text
    board = saved(fake)
    assert [n["id"] for n in board["nodes"]] == ["g1", "a1", "b2", "f4", "l5"]
    assert board["edges"] == [{**BOARD["edges"][0], "label": "speaks via"}]
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S",
                                            "edit_links": [{"from": "a1", "to": "b2"}]})
    assert ok and "took the label off the link" in text
    assert "label" not in saved(fake)["edges"][0]
    assert saved(fake)["edges"][0]["toEnd"] == "arrow"


def test_canvas_ambiguous_or_missing_changes_nothing():
    b, fake = board_bridge()
    before = fake.files["Boards/J.A.R.V.I.S.canvas"]
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S",
                                            "edit_cards": [{"card": "Speech to text",
                                                            "color": "red"}],
                                            "remove_cards": ["text to speech"]})
    assert not ok and 'Several cards match "text to speech"' in text
    assert "(id b2)" in text and "(id c3)" in text and "Which one?" in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "remove_cards": ["GPU"]})
    assert not ok and 'No card matches "GPU". The cards are: "Brain PC" (id g1)' in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "remove_links": [
        {"from": "Speech to text", "to": "notes"}]})
    assert not ok and "No link joins" in text and 'labelled "then" (id e1)' in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "edit_cards": [
        {"card": "J.A.R.V.I.S.md", "text": "x"}]})
    assert not ok and "shows a file" in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "edit_cards": [
        {"card": "a1", "color": "mauve"}]})
    assert not ok and "can't colour" in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "Nope", "remove_cards": ["x"]})
    assert not ok and "no board called Nope" in text
    assert fake.files["Boards/J.A.R.V.I.S.canvas"] == before
    assert "Nope.canvas" not in fake.files


def test_canvas_preview_saves_nothing_and_links_pick_by_label():
    b, fake = board_bridge()
    board = json.loads(json.dumps(BOARD))
    board["edges"].append({"id": "e9", "fromNode": "b2", "toNode": "a1", "label": "back"})
    board["edges"].append({"id": "e8", "fromNode": "a1", "toNode": "b2", "label": "again"})
    fake.files["Boards/J.A.R.V.I.S.canvas"] = before = json.dumps(board)
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "remove_links": [
        {"from": "a1", "to": "b2"}]})
    assert not ok and "Several links join" in text and "(id e1)" in text and "(id e8)" in text
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "preview": True,
                                            "remove_links": [{"from": "a1", "to": "b2",
                                                              "current_label": "again"}]})
    assert ok and text.startswith("On the board Boards/J.A.R.V.I.S.canvas I would: removed "
                                  'the link "Speech to text" -> "Text to speech (Piper)" '
                                  'labelled "again" (id e8)')
    assert fake.files["Boards/J.A.R.V.I.S.canvas"] == before
    ok, text = b.handle("obsidian_canvas", {"canvas": "J.A.R.V.I.S", "remove_links": [
        {"id": "e9"}]})
    assert ok and [e["id"] for e in saved(fake)["edges"]] == ["e1", "e2", "e3", "e8"]


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


MINESWEEPER = ("---\ntype: project\n---\n# Craig's tasks\n- [ ] flags\n\t- [ ] sprite\n"
               "## Notes\n- grid\n# Craig's tasks\n- [ ] moved by mistake\n# Log\n- started\n")
MINE = "Projects/Project Minesweeper.md"


def test_cut_a_passage_or_a_heading_and_never_guess():
    kept, gone, label = cut(MINESWEEPER, path=["Craig's tasks"], which=2)
    assert gone == "# Craig's tasks\n- [ ] moved by mistake" and "heading Craig's tasks" in label
    assert kept == MINESWEEPER.replace("# Craig's tasks\n- [ ] moved by mistake\n", "")
    kept, gone, label = cut(MINESWEEPER, path=["Craig's tasks"], which=1)
    assert gone == "# Craig's tasks\n- [ ] flags\n\t- [ ] sprite\n## Notes\n- grid"
    assert label.endswith("its section, with Notes under it")
    # A log nested under a stray heading stays
    assert cut("# Stray\n- x\n## Log\n- y\n", path=["Stray"])[0] == "## Log\n- y\n"
    with pytest.raises(ObsidianError, match="2 times.*1: line 4; 2: line 9"):
        cut(MINESWEEPER, path=["Craig's tasks"])
    with pytest.raises(ObsidianError, match="only there 2 times"):
        cut(MINESWEEPER, path=["Craig's tasks"], which=3)
    with pytest.raises(ObsidianError, match="no heading Nope.*headings: Craig's tasks"):
        cut(MINESWEEPER, path=["Nope"])
    assert cut(MINESWEEPER, path=["Craig's tasks", "Notes"])[1] == "## Notes\n- grid"
    # An exact passage: whole lines go with their line break, part of a line just the words
    kept, gone, label = cut(MINESWEEPER, "\t- [ ] sprite\n")
    assert kept == MINESWEEPER.replace("\t- [ ] sprite\n", "") and label == "'- [ ] sprite'"
    assert cut("# A\n- big red dog\n", "red ")[0] == "# A\n- big dog\n"
    assert cut("a\r\nb\r\nc\r\n", "b")[0] == "a\r\nc\r\n"
    with pytest.raises(ObsidianError, match="can't find those exact words"):
        cut(MINESWEEPER, "type: project")          # properties are left alone
    with pytest.raises(ObsidianError, match="2 times"):
        cut(MINESWEEPER, "Craig's tasks")
    with pytest.raises(ObsidianError, match="exact words"):
        cut(MINESWEEPER, "  \n")


def test_delete_previews_then_deletes_only_what_the_card_showed():
    b, fake = bridge(files={MINE: MINESWEEPER})
    args = {"note": "Project Minesweeper", "heading": "Craig's tasks", "which": 2}
    ok, plan = b.handle("obsidian_delete", {**args, "preview": True})
    assert ok and plan == ("Delete the heading Craig's tasks and its section from "
                           f"{MINE}:\n# Craig's tasks\n- [ ] moved by mistake")
    assert fake.files[MINE] == MINESWEEPER                          # nothing saved
    ok, text = b.handle("obsidian_delete", args)                    # no card's say-so
    assert not ok and "changed since" in text
    fake.files[MINE] = MINESWEEPER.replace("mistake", "mistake\n- [ ] new")
    ok, text = b.handle("obsidian_delete", {**args, "expect": plan})  # Sir added a line since
    assert not ok and "changed since" in text and "- [ ] new" in fake.files[MINE]
    fake.files[MINE] = MINESWEEPER
    ok, text = b.handle("obsidian_delete", {**args, "expect": plan})
    assert ok and text.startswith("Deleted the heading Craig's tasks")
    assert fake.files[MINE].count("# Craig's tasks") == 1 and "by mistake" not in fake.files[MINE]
    assert "if-match" in [c for c in fake.calls if c[0] == "PUT"][-1][2]
    ok, text = b.handle("obsidian_delete", {"note": "Project Minesweeper", "text": "nowhere",
                                            "preview": True})
    assert not ok and "can't find" in text


def test_new_project_from_the_template_linked_from_the_map():
    template = ("---\ntype: project\nstatus: planned\nprogress: 0\nstarted: {{date}}\n---\n%%\n"
                "HOW TO FILL THIS IN\nsteps\n%%\n# {{title}}\n"
                '<div class="jarvis-progress" title="0% done"></div>\n\n'
                'What it is and what "done" looks like, in a sentence or two. Back to the '
                "[[V Source Development Success Map]] · code: <repo link or folder path>.\n\n"
                "## Log\n- {{date}}: Project started.\n")
    success = "# Map\n## Projects\n- [[J.A.R.V.I.S]]: J\n\n## Tracker\nx\n"
    b, fake = bridge(files={"Templates/Project.md": template,
                            "V Source Development Success Map.md": success})
    ok, text = b.handle("obsidian_new_project", {"name": "Me", "status": "active",
                                                 "about": "Everything Sir has to do"})
    assert ok and text.startswith("Made the project note Projects/Me.md from the template, "
                                  "linked it from the Success Map, and opened it.")
    note = fake.files["Projects/Me.md"]
    assert "HOW TO" not in note and "{{" not in note and "<repo link" not in note
    assert "# Me\n" in note and "status: active\n" in note
    assert "Everything Sir has to do. Back to the [[V Source Development Success Map]]." in note
    assert 'class="jarvis-progress" title="0% done"' in note and "width:0%" in note
    assert fake.files["V Source Development Success Map.md"] == (
        "# Map\n## Projects\n- [[J.A.R.V.I.S]]: J\n- [[Me]]: everything Sir has to do\n\n"
        "## Tracker\nx\n")
    assert ("POST", "/open/Projects/Me.md") in [(c[0], c[1]) for c in fake.calls]
    ok, text = b.handle("obsidian_new_project", {"name": "me"})
    assert not ok and "already a project note called me" in text


ROUTINES = "# Me\n## Routines\n### Weekly\n- Mow the lawns (last done 2026-11-01)\n- Clean the car\n"


def test_project_notes_are_read_for_the_daily_priorities():
    b, fake = bridge(files={"Projects/J.A.R.V.I.S.md": NOTE, "Projects/Me.md": ROUTINES,
                            "Projects/board.canvas": "{}", "Home.md": "# Home\n"})
    ok, text = b.handle("obsidian_project_notes", {})
    assert ok
    assert json.loads(text) == {"notes": {"Projects/J.A.R.V.I.S.md": NOTE,
                                          "Projects/Me.md": ROUTINES}, "left_out": 0}
    assert all(c[0] == "GET" for c in fake.calls)          # read-only


def test_routine_done_dates_one_line_in_the_me_note():
    b, fake = bridge(files={"Projects/Me.md": ROUTINES})
    ok, text = b.handle("obsidian_routine_done", {"routine": "mow the lawns", "day": "2026-11-13"})
    assert ok and "Marked Mow the lawns (weekly) done on 2026-11-13" in text
    assert fake.files["Projects/Me.md"] == ROUTINES.replace("2026-11-01", "2026-11-13")
    put = [c for c in fake.calls if c[0] == "PUT"]
    assert len(put) == 1 and "if-match" in put[0][2]        # only if Sir hasn't typed since
    ok, text = b.handle("obsidian_routine_done", {"routine": "walk the dog", "day": "2026-11-13"})
    assert not ok and "no routine like" in text and "Projects/Me.md" in text
