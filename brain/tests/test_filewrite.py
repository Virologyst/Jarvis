"""Writing on the brain: only through approval cards, only text, never in protected places.
And reading on a terminal: the file tools go to the right machine."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain.approvals import ApprovalGate  # noqa: E402
from jarvis_brain.files import FileAccessError, FileReader  # noqa: E402
from jarvis_brain.filewrite import FileWriter  # noqa: E402
from jarvis_brain.tools import ToolBox  # noqa: E402


@pytest.fixture
def drive(tmp_path: Path) -> Path:
    root = tmp_path / "drive"
    docs = root / "Users" / "Craig" / "Documents"
    docs.mkdir(parents=True)
    (docs / "Notes.md").write_text("# Notes\nBuy milk.\nCall Sam.\n", encoding="utf-8")
    (docs / "tool.exe").write_bytes(b"MZ\0\0")
    (docs / "Project").mkdir()
    (docs / "Project" / "plan.md").write_text("plan", encoding="utf-8")
    (docs / "Scripts").mkdir()
    (docs / "Scripts" / "run.bat").write_text("echo hi", encoding="utf-8")
    home = root / "Users" / "Craig"
    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text("Host x", encoding="utf-8")
    (home / ".local" / "bin").mkdir(parents=True)
    (home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu").mkdir(parents=True)
    jarvis = root / "Users" / "Jarvis" / "Jarvis"
    (jarvis / "brain" / "jarvis_brain").mkdir(parents=True)
    (jarvis / "brain" / "jarvis_brain" / "approvals.py").write_text("gate", encoding="utf-8")
    (jarvis / "data").mkdir()
    (root / "Users" / "Jarvis" / "jarvis-upgrades").mkdir()
    (root / "Windows" / "System32").mkdir(parents=True)
    (root / "Program Files").mkdir()
    return root


def make_reader(drive: Path) -> FileReader:
    jarvis = drive / "Users" / "Jarvis" / "Jarvis"
    return FileReader(roots=[drive], excluded=[jarvis, jarvis.parent / "jarvis-upgrades"],
                      home=drive / "Users" / "Craig")


def writer(drive: Path) -> FileWriter:
    return FileWriter(make_reader(drive))


def docs(drive: Path) -> Path:
    return drive / "Users" / "Craig" / "Documents"


# --- what the writer does once a plan is approved ------------------------------------------------

def test_create_replace_append_and_edit(drive: Path) -> None:
    w, d = writer(drive), docs(drive)
    new = d / "Ideas" / "Coffee.md"
    plan = w.write_text(str(new), "# Coffee\nNew machine.", "create")
    assert "Coffee.md" in plan.title and "New machine." in plan.body
    assert not new.exists()                       # planning changes nothing
    assert "created" in plan.apply()
    assert new.read_text(encoding="utf-8") == "# Coffee\nNew machine."
    assert "added to" in w.write_text(str(new), "Ask Sam.", "append").apply()
    assert new.read_text(encoding="utf-8") == "# Coffee\nNew machine.\nAsk Sam."
    w.write_text(str(new), "Replaced.", "replace").apply()
    assert new.read_text(encoding="utf-8") == "Replaced."
    w.edit_text(str(d / "Notes.md"), "Buy milk.", "Buy oat milk.").apply()
    assert (d / "Notes.md").read_text(encoding="utf-8") == "# Notes\nBuy oat milk.\nCall Sam.\n"
    assert not list(d.rglob("*.jarvis-tmp"))


def test_create_never_overwrites_and_edits_must_be_exact(drive: Path) -> None:
    w, d = writer(drive), docs(drive)
    with pytest.raises(FileAccessError, match="already exists"):
        w.write_text(str(d / "Notes.md"), "gone", "create")
    with pytest.raises(FileAccessError, match="can't find"):
        w.write_text(str(d / "Nope.md"), "x", "append")
    with pytest.raises(FileAccessError, match="couldn't find"):
        w.edit_text(str(d / "Notes.md"), "Buy bread.", "x")
    (d / "Twice.md").write_text("a\na\n", encoding="utf-8")
    with pytest.raises(FileAccessError, match="2 times"):
        w.edit_text(str(d / "Twice.md"), "a", "b")
    # Changed between proposing and approving: nothing is clobbered, it's said instead
    plan = w.write_text(str(d / "Late.md"), "mine", "create")
    (d / "Late.md").write_text("theirs", encoding="utf-8")
    assert "didn't change anything" in plan.apply()
    assert (d / "Late.md").read_text(encoding="utf-8") == "theirs"


def test_folders_moves_and_renames(drive: Path) -> None:
    w, d = writer(drive), docs(drive)
    w.create_folder(str(d / "Archive" / "2026")).apply()
    assert (d / "Archive" / "2026").is_dir()
    plan = w.move(str(d / "Notes.md"), str(d / "Notes 2026.md"))
    assert plan.title.startswith("Rename")
    plan.apply()
    assert (d / "Notes 2026.md").exists() and not (d / "Notes.md").exists()
    w.move(str(d / "Notes 2026.md"), str(d / "Archive")).apply()     # into a folder
    assert (d / "Archive" / "Notes 2026.md").exists()
    w.move(str(d / "Project"), str(d / "Archive" / "Project")).apply()
    assert (d / "Archive" / "Project" / "plan.md").exists()
    (d / "Other.md").write_text("x", encoding="utf-8")
    with pytest.raises(FileAccessError, match="already something"):
        w.move(str(d / "Other.md"), str(d / "Archive" / "Notes 2026.md"))


# --- protected places and things that run --------------------------------------------------------

def test_refuses_protected_places(drive: Path) -> None:
    w = writer(drive)
    home = drive / "Users" / "Craig"
    jarvis = drive / "Users" / "Jarvis" / "Jarvis"
    startup = home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "x.txt"
    for bad in [jarvis / "brain" / "jarvis_brain" / "approvals.py", jarvis / "data" / "tasks.json",
                jarvis / "notes.md", jarvis.parent / "jarvis-upgrades" / "x.md",
                home / ".ssh" / "config.txt", home / ".local" / "bin" / "x.txt", startup,
                home / ".env", home / "server.pem", drive / "Windows" / "System32" / "x.txt",
                drive / "Program Files" / "x.txt", drive / "top.txt",
                drive.parent / "outside.txt"]:
        existed = bad.exists()
        for mode in ("create", "replace", "append"):
            with pytest.raises(FileAccessError):
                w.write_text(str(bad), "x", mode)
        assert bad.exists() == existed
    with pytest.raises(FileAccessError):
        w.edit_text(str(jarvis / "brain" / "jarvis_brain" / "approvals.py"), "gate", "open")
    for folder in [jarvis / "new", home / ".ssh" / "new", drive / "Windows" / "new",
                   home / "AppData" / "new"]:
        with pytest.raises(FileAccessError):
            w.create_folder(str(folder))
    assert (jarvis / "brain" / "jarvis_brain" / "approvals.py").read_text(encoding="utf-8") == "gate"


def test_refuses_programs_and_risky_moves(drive: Path) -> None:
    w, d = writer(drive), docs(drive)
    home = drive / "Users" / "Craig"
    for name in ["run.bat", "setup.ps1", "x.exe", "tool.py", "noext"]:
        with pytest.raises(FileAccessError, match="text files"):
            w.write_text(str(d / name), "x", "create")
    with pytest.raises(FileAccessError, match="program"):
        w.move(str(d / "tool.exe"), str(d / "Project"))
    with pytest.raises(FileAccessError, match="program"):
        w.move(str(d / "Project" / "plan.md"), str(d / "plan.bat"))
    with pytest.raises(FileAccessError, match="programs"):
        w.move(str(d / "Scripts"), str(d / "Project"))
    with pytest.raises(FileAccessError):
        w.move(str(d / "Notes.md"), str(home / ".local" / "bin"))
    with pytest.raises(FileAccessError, match="user folder"):
        w.move(str(home), str(drive / "Users" / "Elsewhere"))
    with pytest.raises(FileAccessError):
        w.move(str(drive / "Users" / "Jarvis"), str(d / "J"))   # holds Jarvis's checkout
    with pytest.raises(FileAccessError, match="inside itself"):
        w.move(str(d / "Project"), str(d / "Project" / "Inner"))


def test_module_never_deletes() -> None:
    from jarvis_brain import filewrite
    source = Path(filewrite.__file__).read_text(encoding="utf-8")
    for op in ["unlink(", "rmdir(", "rmtree", "remove(", "shutil", "subprocess", "os.system"]:
        assert op not in source, op


# --- the tools: cards first, the change only once approved ---------------------------------------

def toolbox(drive: Path, gate: ApprovalGate | None, cards: list, here: str = "",
            terminals: dict | None = None) -> ToolBox:
    async def show(card) -> None:
        cards.append(card)
    return ToolBox(upgrades=None, show_card=show, files=make_reader(drive), approvals=gate,
                   terminal_id=lambda: here, file_terminals=lambda: dict(terminals or {}))


def test_write_tools_only_propose_until_approved(drive: Path) -> None:
    gate, cards = ApprovalGate(), []
    box, d = toolbox(drive, gate, cards), docs(drive)
    target = d / "Meeting.md"

    async def go():
        said = await box.run("write_file", {"path": str(target), "text": "Agenda", "mode": ""})
        assert "approval card" in said and not target.exists()
        assert len(cards) == 1 and cards[0].kind == "approval" and "Agenda" in cards[0].body
        result = await gate.decide(cards[0].card_id, "approve")
        assert "created" in result and target.read_text(encoding="utf-8") == "Agenda"

        await box.run("move_file", {"path": str(target), "to": str(d / "Kept.md")})
        assert "won't" in await gate.decide(cards[1].card_id, "reject")
        assert target.exists() and not (d / "Kept.md").exists()

        refused = await box.run("write_file", {"path": str(drive / "Users" / "Jarvis" / "Jarvis"
                                                             / "x.md"), "text": "x", "mode": ""})
        assert "not allowed" in refused and len(cards) == 2
    asyncio.run(go())


def test_without_an_approval_gate_nothing_is_written(drive: Path) -> None:
    cards = []
    box = toolbox(drive, None, cards)
    said = asyncio.run(box.run("create_folder", {"path": str(docs(drive) / "New")}))
    assert "isn't available" in said and cards == [] and not (docs(drive) / "New").exists()


# --- which machine a read goes to ----------------------------------------------------------------

class FakeTerminal:
    def __init__(self, name: str):
        self.name, self.calls = name, []

    async def __call__(self, op: str, args: dict) -> str:
        self.calls.append((op, args))
        if "Secret" in str(args):
            raise FileAccessError("That's somewhere private")
        return f"{op} on {self.name}"


def test_reads_go_to_the_right_machine(drive: Path) -> None:
    office, laptop = FakeTerminal("office"), FakeTerminal("laptop")
    box = toolbox(drive, ApprovalGate(), [], here="office",
                  terminals={"office": office, "Craig-Laptop": laptop})
    on_brain = str(docs(drive) / "Notes.md")
    vault = str(drive / "Obsidian" / "Today.md")      # not on the brain

    async def go():
        a = await box.run("read_file", {"path": on_brain, "start": 0, "device": ""})
        assert a.startswith("[On the brain]") and "Buy milk" in a and office.calls == []
        b = await box.run("read_file", {"path": vault, "start": 0, "device": ""})
        assert b == "[On the terminal office] read_file on office"
        assert office.calls[-1] == ("read_file", {"path": vault, "start": 0})
        c = await box.run("list_directory", {"path": on_brain, "device": "this terminal"})
        assert c.startswith("[On the terminal office]")
        d = await box.run("search_files", {"query": "plan", "root": "", "contents": True,
                                           "device": "laptop"})
        assert d.startswith("[On the terminal Craig-Laptop]")
        assert laptop.calls[-1] == ("search_files", {"query": "plan", "root": "", "contents": True})
        e = await box.run("list_directory", {"path": vault, "device": "brain"})
        assert e.startswith("I can")          # the brain's own answer, not the terminal's
        f = await box.run("list_directory", {"path": "C:\\Secret", "device": "office"})
        assert "private" in f
        g = await box.run("list_directory", {"path": "C:\\", "device": "kitchen"})
        assert "No terminal called kitchen" in g and "Craig-Laptop" in g
        devices = await box.run("list_file_devices", {})
        assert "office (this terminal)" in devices and "Craig-Laptop" in devices
    asyncio.run(go())


def test_session_asks_its_terminal_and_waits_for_the_reply(monkeypatch) -> None:
    server = pytest.importorskip("jarvis_brain.server")
    from types import SimpleNamespace as NS

    from jarvis_protocol import FileReply, FileRequest, Hello, decode, encode

    class FakeWS:
        closed = False

        def __init__(self):
            self.sent = []

        async def send_str(self, raw):
            self.sent.append(decode(raw))

    brain = NS(settings=NS(model="m", effort="low", idle_reset_minutes=10), client=None,
               upgrades=None, tasks=None, approvals=NS(pending=lambda: []), notice=None,
               themes=NS(get=lambda tid: server.THEMES[server.DEFAULT_THEME]), sessions=set())
    brain.file_terminals = lambda: server.Brain.file_terminals(brain)

    async def go():
        ws = FakeWS()
        session = server.TerminalSession(brain, ws)
        brain.sessions.add(session)
        await session.on_text(encode(Hello("office", files=True)))
        assert set(brain.file_terminals()) == {"office"}

        asking = asyncio.create_task(session.remote_file("read_file", {"path": "C:\\a.md",
                                                                       "start": 0}))
        await asyncio.sleep(0.01)
        request = next(m for m in ws.sent if isinstance(m, FileRequest))
        assert request.op == "read_file" and request.args["path"] == "C:\\a.md"
        await session.on_text(encode(FileReply(request.request_id, True, "# A note")))
        assert await asyncio.wait_for(asking, 1) == "# A note"

        refused = asyncio.create_task(session.remote_file("list_directory", {"path": "C:\\x"}))
        await asyncio.sleep(0.01)
        request = [m for m in ws.sent if isinstance(m, FileRequest)][-1]
        await session.on_text(encode(FileReply(request.request_id, False, "That's private.")))
        with pytest.raises(FileAccessError, match="private"):
            await refused

        gone = asyncio.create_task(session.remote_file("list_directory", {"path": "C:\\x"}))
        await asyncio.sleep(0.01)
        session.close_file_requests()
        with pytest.raises(FileAccessError, match="disconnected"):
            await gone

        monkeypatch.setattr(server, "FILE_REPLY_S", 0.05)
        with pytest.raises(FileAccessError, match="in time"):
            await session.remote_file("list_directory", {"path": "C:\\x"})
        assert session._file_waits == {}

        await session.on_text(encode(Hello("pi")))       # a terminal that doesn't share
        assert brain.file_terminals() == {}
    asyncio.run(go())


def test_a_terminal_that_doesnt_share_files(drive: Path) -> None:
    box = toolbox(drive, ApprovalGate(), [], here="pi")
    vault = str(drive / "nowhere" / "Today.md")

    async def go():
        said = await box.run("read_file", {"path": vault, "start": 0, "device": "this terminal"})
        assert "isn't sharing" in said
        auto = await box.run("read_file", {"path": vault, "start": 0, "device": ""})
        assert "can't find" in auto           # tried on the brain, the only choice
    asyncio.run(go())
