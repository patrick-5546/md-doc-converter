"""Finding the programs that md-doc-converter runs: on PATH, never in the current folder."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from md_doc_converter import executables

WINDOWS = sys.platform == "win32"


def program(folder: Path, name: str) -> Path:
    """Write a program *name* in *folder*: an .exe on Windows, and elsewhere a file that may run."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{name}.exe" if WINDOWS else name)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_a_program_on_path_is_found_by_its_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = program(tmp_path / "bin", "tool")
    monkeypatch.chdir(tmp_path)
    assert executables.which("tool", "bin") == str(tool)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert executables.resolve("tool") == str(tool)


def test_a_program_in_the_current_folder_is_never_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As a pandoc.exe among documents would be, where Windows looks first.
    program(tmp_path / "documents", "tool")
    monkeypatch.chdir(tmp_path / "documents")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert executables.which("tool") is None
    with pytest.raises(FileNotFoundError, match="tool"):
        executables.resolve("tool")


def test_a_name_with_a_folder_is_looked_for_there(tmp_path: Path) -> None:
    tool = program(tmp_path / "bin", "tool")
    assert executables.which(str(tool)) == str(tool)
    assert executables.which(str(tmp_path / "bin" / "missing")) is None


def test_windows_tries_each_extension_that_pathext_lists(tmp_path: Path) -> None:
    script = tmp_path / "tool.cmd"
    script.write_text("", encoding="utf-8")
    script.chmod(0o755)
    # Elsewhere, a program's name has no extension added.
    assert executables.which("tool", str(tmp_path)) == (str(script) if WINDOWS else None)
    assert executables.which("tool.cmd", str(tmp_path)) == str(script)


def test_a_program_is_a_file_that_may_run(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("", encoding="utf-8")
    # Windows runs a file by its extension; elsewhere, by its permission, which text lacks.
    text.chmod(0o644)
    assert executables.runnable(program(tmp_path, "tool"))
    assert not executables.runnable(text)
    assert not executables.runnable(tmp_path / "missing")


def test_libreoffice_is_found_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    office = program(tmp_path / "bin", "soffice")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert executables.libreoffice() == str(office)


def test_libreoffice_is_found_where_its_windows_installer_puts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office = tmp_path / "LibreOffice" / "program" / "soffice.com"
    office.parent.mkdir(parents=True)
    office.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(variable, str(tmp_path))
    # Elsewhere, LibreOffice's command is on PATH.
    assert executables.libreoffice() == (str(office) if WINDOWS else None)
