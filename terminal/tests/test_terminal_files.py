"""The terminal lets the brain read its drive: read-only, private places refused, and only the
operations it knows. No audio devices or models needed."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

from jarvis_protocol import FileReply, FileRequest, Hello, decode, encode
from jarvis_protocol.fileaccess import FileReader

pytest.importorskip("aiohttp")
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):   # no PortAudio here: the test never touches a real device
    sys.modules["sounddevice"] = types.ModuleType("sounddevice")
from jarvis_terminal import client  # noqa: E402
from jarvis_terminal.files import REPO_ROOT, TerminalFiles  # noqa: E402


@pytest.fixture
def drive(tmp_path: Path) -> Path:
    root = tmp_path / "drive"
    vault = root / "Users" / "craig" / "Documents" / "Obsidian"
    vault.mkdir(parents=True)
    (vault / "Today.md").write_text("# Today\nShip the website.\n", encoding="utf-8")
    (vault / "Map.canvas").write_text(
        '{"nodes": [{"id": "a", "type": "text", "text": "Big idea"}], "edges": []}',
        encoding="utf-8")
    (root / "Users" / "craig" / ".ssh").mkdir()
    (root / "Users" / "craig" / ".ssh" / "id_rsa").write_text("KEY", encoding="utf-8")
    jarvis = root / "Users" / "craig" / "Jarvis"
    jarvis.mkdir()
    (jarvis / ".env").write_text("JARVIS_BRAIN_URL=ws://x", encoding="utf-8")
    (jarvis / "notes.md").write_text("terminal checkout", encoding="utf-8")
    return root


def files(drive: Path) -> TerminalFiles:
    return TerminalFiles(FileReader(roots=[drive], excluded=[drive / "Users" / "craig" / "Jarvis"],
                                    home=drive / "Users" / "craig"))


def test_reads_lists_and_searches_the_vault(drive: Path) -> None:
    f = files(drive)
    vault = drive / "Users" / "craig" / "Documents" / "Obsidian"
    ok, listing = f.handle("list_directory", {"path": str(vault)})
    assert ok and "Today.md" in listing
    ok, text = f.handle("read_file", {"path": str(vault / "Today.md"), "start": 0})
    assert ok and "Ship the website." in text
    ok, canvas = f.handle("read_file", {"path": str(vault / "Map.canvas"), "start": 0})
    assert ok and "Big idea" in canvas
    ok, found = f.handle("search_files", {"query": "website", "root": "", "contents": True})
    assert ok and "Today.md" in found


def test_private_places_and_unknown_ops_are_refused(drive: Path) -> None:
    f = files(drive)
    home = drive / "Users" / "craig"
    for path in [home / ".ssh" / "id_rsa", home / "Jarvis" / ".env", home / "Jarvis" / "notes.md"]:
        ok, said = f.handle("read_file", {"path": str(path), "start": 0})
        assert not ok and "private" in said
    for op in ["write_file", "edit_file", "move_file", "create_folder", "delete", ""]:
        ok, said = f.handle(op, {"path": str(home / "x.md"), "text": "x"})
        assert not ok and "only lets me read" in said
    assert not (home / "x.md").exists()
    assert set(f.ops) == {"list_directory", "read_file", "search_files"}


def test_default_excludes_this_checkout() -> None:
    f = TerminalFiles()
    assert any(str(p).lower() == str(REPO_ROOT.resolve()).lower() for p in f.reader.excluded)


class FakeBrain:
    closed = False

    def __init__(self):
        self.sent: list = []

    async def send_str(self, raw):
        self.sent.append(decode(raw))


class FakePlayer:
    busy = False

    def clear(self): pass
    def close(self): pass


def make_terminal(monkeypatch, f: TerminalFiles | None) -> client.Terminal:
    monkeypatch.setattr(client, "_load_vad", lambda: None)
    monkeypatch.setattr(client, "Player", lambda device=None: FakePlayer())
    t = client.Terminal("ws://x", "office", 0, 0.5, use_wake=False, files=f)
    t.brain_ws = FakeBrain()
    return t


def test_terminal_answers_file_requests(monkeypatch, drive: Path) -> None:
    t = make_terminal(monkeypatch, files(drive))
    assert t.shares_files
    today = drive / "Users" / "craig" / "Documents" / "Obsidian" / "Today.md"
    shown = []

    async def to_ui(payload):
        shown.append(payload)
    t.to_ui = to_ui

    async def go():
        await t._from_brain(encode(FileRequest("r1", "read_file", {"path": str(today), "start": 0})))
        for _ in range(100):
            if t.brain_ws.sent:
                break
            await asyncio.sleep(0.01)
    asyncio.run(go())
    reply = t.brain_ws.sent[0]
    assert isinstance(reply, FileReply) and reply.request_id == "r1" and reply.ok
    assert "Ship the website." in reply.text
    assert shown == []          # file traffic isn't shown on the touchscreen


def test_terminal_without_file_sharing_says_so(monkeypatch) -> None:
    t = make_terminal(monkeypatch, None)
    assert not t.shares_files

    async def go():
        await t.on_file_request(FileRequest("r2", "list_directory", {"path": "C:\\"}))
    asyncio.run(go())
    assert t.brain_ws.sent == [FileReply("r2", False, "This terminal isn't sharing its files.")]


def test_protocol_round_trip_and_old_hello() -> None:
    assert decode(encode(Hello("office", files=True))) == Hello("office", 1, True)
    assert decode('{"type": "hello", "terminal_id": "pi"}') == Hello("pi")   # older terminals
    req = FileRequest("id", "search_files", {"query": "plan", "root": "", "contents": False})
    assert decode(encode(req)) == req
    assert decode(encode(FileReply("id", True, "text"))) == FileReply("id", True, "text")
