"""Read-only file access: stays on the allowed drive, refuses private places, never writes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis_brain import files as files_mod  # noqa: E402
from jarvis_brain.config import REPO_ROOT  # noqa: E402
from jarvis_brain.files import FileAccessError, FileReader  # noqa: E402
from jarvis_brain.persona import SYSTEM_PROMPT  # noqa: E402
from jarvis_brain.selfupgrade import tool_schema_problems  # noqa: E402
from jarvis_brain.tools import TOOLS, ToolBox  # noqa: E402


@pytest.fixture
def drive(tmp_path: Path) -> Path:
    """A pretend C: drive: a vault, a home with secrets, a Jarvis checkout, and an outside place."""
    root = tmp_path / "drive"
    vault = root / "Users" / "Craig" / "Vault"
    (vault / "Projects").mkdir(parents=True)
    (vault / "Projects" / "Website plan.md").write_text("# Website\nLaunch in March.\n",
                                                       encoding="utf-8")
    (vault / "Ideas.txt").write_text("Coffee machine upgrade", encoding="utf-8")
    (vault / "Map.canvas").write_text(json.dumps({
        "nodes": [{"id": "a", "type": "text", "text": "Business plan"},
                  {"id": "b", "type": "file", "file": "Projects/Website plan.md"}],
        "edges": [{"id": "e", "fromNode": "a", "toNode": "b", "label": "needs"}]}),
        encoding="utf-8")
    (vault / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0data")
    (vault / "blob.dat").write_bytes(b"\0\1\2\3" * 100)
    home = root / "Users" / "Craig"
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY", encoding="utf-8")
    (home / ".env").write_text("SECRET=1", encoding="utf-8")
    (home / "server.pem").write_text("CERT", encoding="utf-8")
    chrome = home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default"
    chrome.mkdir(parents=True)
    (chrome / "Login Data").write_text("passwords", encoding="utf-8")
    jarvis = root / "Users" / "Jarvis" / "Jarvis"
    (jarvis / "brain").mkdir(parents=True)
    (jarvis / "brain" / "approvals.py").write_text("gate", encoding="utf-8")
    (jarvis / ".env").write_text("ANTHROPIC_API_KEY=x", encoding="utf-8")
    (root / "Users" / "Jarvis" / "jarvis-upgrades" / "abc").mkdir(parents=True)
    (tmp_path / "outside.txt").write_text("not on the drive", encoding="utf-8")
    return root


def reader(drive: Path) -> FileReader:
    jarvis = drive / "Users" / "Jarvis" / "Jarvis"
    return FileReader(roots=[drive], excluded=[jarvis, jarvis.parent / "jarvis-upgrades"],
                      home=drive / "Users" / "Craig")


def snapshot(root: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes() if p.is_file() else b"")
            for p in sorted(root.rglob("*"))}


# --- reading what's allowed --------------------------------------------------------------------

def test_lists_and_reads_the_vault(drive: Path) -> None:
    r = reader(drive)
    vault = drive / "Users" / "Craig" / "Vault"
    listing = r.list_directory(str(vault))
    assert "Projects\\" in listing and "Ideas.txt" in listing and "Map.canvas" in listing
    text = r.read_file(str(vault / "Projects" / "Website plan.md"))
    assert "Launch in March." in text


def test_canvas_reads_as_cards_and_connections(drive: Path) -> None:
    text = reader(drive).read_file(str(drive / "Users" / "Craig" / "Vault" / "Map.canvas"))
    assert "Business plan" in text and "note Projects/Website plan.md" in text
    assert "Business plan -> note Projects/Website plan.md (needs)" in text


def test_binary_files_are_described_not_read(drive: Path) -> None:
    r = reader(drive)
    vault = drive / "Users" / "Craig" / "Vault"
    assert "an image" in r.read_file(str(vault / "photo.png"))
    assert "binary" in r.read_file(str(vault / "blob.dat"))


def test_long_files_are_truncated_with_a_notice(drive: Path) -> None:
    big = drive / "Users" / "Craig" / "Vault" / "Long.md"
    big.write_text("x" * (files_mod.READ_LIMIT + 500), encoding="utf-8")
    r = reader(drive)
    first = r.read_file(str(big))
    assert f"Use start {files_mod.READ_LIMIT} to read on" in first
    rest = r.read_file(str(big), files_mod.READ_LIMIT)
    assert rest.count("x") == 500 and "read on" not in rest


def test_search_by_name_and_by_contents(drive: Path) -> None:
    r = reader(drive)
    vault = str(drive / "Users" / "Craig" / "Vault")
    assert "Website plan.md" in r.search_files("website", vault)
    assert "Ideas.txt" not in r.search_files("coffee", vault)
    assert "Ideas.txt" in r.search_files("coffee", vault, contents=True)
    # empty root means the home folder
    assert "Website plan.md" in r.search_files("website plan")


# --- staying inside the drive ------------------------------------------------------------------

def test_refuses_paths_off_the_drive(drive: Path) -> None:
    r = reader(drive)
    for bad in [str(drive.parent / "outside.txt"),
                str(drive / "Users" / ".." / ".." / "outside.txt"),
                "\\\\server\\share\\notes.md", "relative\\notes.md", ""]:
        with pytest.raises(FileAccessError):
            r.read_file(bad)


def test_refuses_alternate_data_streams(drive: Path) -> None:
    with pytest.raises(FileAccessError):
        reader(drive).read_file(str(drive / "Users" / "Craig" / "Vault" / "Ideas.txt") + ":hidden")


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this system can't make symlinks")


def test_symlinks_cant_escape_the_drive(drive: Path) -> None:
    link = drive / "Users" / "Craig" / "Vault" / "sneaky.txt"
    _symlink(link, drive.parent / "outside.txt")
    r = reader(drive)
    with pytest.raises(FileAccessError):
        r.read_file(str(link))
    assert "sneaky" not in r.list_directory(str(link.parent))


def test_symlinks_cant_reach_private_places(drive: Path) -> None:
    link = drive / "Users" / "Craig" / "Vault" / "keys"
    _symlink(link, drive / "Users" / "Craig" / ".ssh")
    r = reader(drive)
    with pytest.raises(FileAccessError):
        r.list_directory(str(link))
    with pytest.raises(FileAccessError):
        r.read_file(str(link / "id_ed25519"))
    assert "keys" not in r.list_directory(str(link.parent))
    assert "id_ed25519" not in r.search_files("id", str(link.parent))


# --- private places ----------------------------------------------------------------------------

def test_refuses_secrets_and_jarvis_itself(drive: Path) -> None:
    r = reader(drive)
    home = drive / "Users" / "Craig"
    jarvis = drive / "Users" / "Jarvis" / "Jarvis"
    private = [home / ".ssh" / "id_ed25519", home / ".env", home / "server.pem",
               home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" /
               "Login Data",
               jarvis / ".env", jarvis / "brain" / "approvals.py",
               jarvis / "brain" / ".." / ".env"]
    for path in private:
        with pytest.raises(FileAccessError, match="private"):
            r.read_file(str(path))
    for folder in [home / ".ssh", jarvis, jarvis / "brain", jarvis.parent / "jarvis-upgrades"]:
        with pytest.raises(FileAccessError, match="private"):
            r.list_directory(str(folder))


def test_private_things_are_hidden_from_listings_and_searches(drive: Path) -> None:
    r = reader(drive)
    home = drive / "Users" / "Craig"
    listing = r.list_directory(str(home))
    assert ".ssh" not in listing and ".env" not in listing and "server.pem" not in listing
    assert "Vault" in listing
    assert "is empty" in r.list_directory(str(drive / "Users" / "Jarvis"))
    found = r.search_files("a", str(drive), contents=True)
    for secret in ["id_ed25519", ".env", "server.pem", "Login Data", "approvals.py"]:
        assert secret not in found
    assert "No files" in r.search_files("approvals", str(drive))


def test_default_exclusions_cover_this_checkout() -> None:
    r = FileReader(roots=[REPO_ROOT.parent])
    with pytest.raises(FileAccessError, match="private"):
        r.read_file(str(REPO_ROOT / "brain" / "jarvis_brain" / "approvals.py"))
    with pytest.raises(FileAccessError, match="private"):
        r.list_directory(str(REPO_ROOT))


def test_default_root_is_only_the_c_drive() -> None:
    r = FileReader()
    if os.name == "nt":
        assert [str(p).upper() for p in r.roots] == ["C:\\"]
        with pytest.raises(FileAccessError, match="C drive"):
            r.list_directory("D:\\")
    else:
        assert r.roots == []
        with pytest.raises(FileAccessError):
            r.list_directory("/")


# --- read-only ---------------------------------------------------------------------------------

def test_nothing_changes_on_disk(drive: Path) -> None:
    before = snapshot(drive)
    r = reader(drive)
    vault = drive / "Users" / "Craig" / "Vault"
    r.list_directory(str(vault))
    r.read_file(str(vault / "Map.canvas"))
    r.search_files("plan", str(drive), contents=True)
    for bad in [str(drive / "Users" / "Craig" / ".env"), str(vault / "New.md")]:
        with pytest.raises(FileAccessError):
            r.read_file(bad)
    assert snapshot(drive) == before
    assert not (vault / "New.md").exists()


def test_module_has_no_writing_operations() -> None:
    source = Path(files_mod.__file__).read_text(encoding="utf-8")
    for op in ["write_", ".write(", "unlink(", "rmdir(", "rmtree", "rename(", "os.replace",
               "mkdir(", "touch(", "chmod(", "shutil", "subprocess", "remove(", '"w"', "'w'",
               '"a"', "'a'", "open(path)", "os.system"]:
        assert op not in source, op
    assert source.count("open(") == source.count('open(path, "rb")')


def test_file_tools_are_read_only_and_show_no_cards(drive: Path) -> None:
    names = {t["name"] for t in TOOLS}
    assert {"list_directory", "read_file", "search_files"} <= names
    for word in ("write", "delete_file", "move", "rename", "create_file", "save_file"):
        assert not any(word in n for n in names), word
    assert tool_schema_problems(TOOLS) == []
    for name in ("list_directory", "read_file", "search_files"):
        assert name in SYSTEM_PROMPT
    assert "summarise" in SYSTEM_PROMPT

    cards = []

    async def show(card) -> None:
        cards.append(card)

    box = ToolBox(upgrades=None, show_card=show, files=reader(drive))
    vault = str(drive / "Users" / "Craig" / "Vault")

    async def go() -> list[str]:
        return [await box.run("list_directory", {"path": vault}),
                await box.run("read_file", {"path": str(Path(vault) / "Ideas.txt"), "start": 0}),
                await box.run("search_files", {"query": "plan", "root": vault, "contents": False}),
                await box.run("read_file", {"path": str(drive / "Users" / "Craig" / ".env"),
                                            "start": 0})]

    listing, text, found, refused = asyncio.run(go())
    assert "Ideas.txt" in listing and "Coffee machine" in text and "Website plan.md" in found
    assert "private" in refused
    assert cards == []
