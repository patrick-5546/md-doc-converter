"""Find the programs that md-doc-converter runs: on PATH, never in the current folder.

Windows looks for a program in the current folder before PATH, both when it starts a program by
name and in shutil.which. md-doc-converter converts and renders documents from anywhere, so it
looks on PATH alone, as Linux does, and runs each program by its absolute path: a pandoc.exe or
git.exe in a folder of documents never runs.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

# The extensions of the programs that Windows runs when PATHEXT does not list them.
_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def which(name: str, path: str | None = None) -> str | None:
    """The absolute path of the program *name*, or None if there is none.

    A name without a folder is looked for on *path*, by default PATH, and on Windows with each
    extension that PATHEXT lists; a name with a folder is looked for there.
    """
    found = _windows_which(name, path) if sys.platform == "win32" else shutil.which(name, path=path)
    return None if found is None else os.path.abspath(found)  # noqa: PTH100


def _windows_which(name: str, path: str | None) -> str | None:
    folder, file = os.path.split(name)
    if folder:
        folders = [folder]
    else:
        folders = (os.environ.get("PATH", "") if path is None else path).split(os.pathsep)
    pathext = os.environ.get("PATHEXT") or _DEFAULT_PATHEXT
    extensions = [extension.lower() for extension in pathext.split(os.pathsep) if extension]
    # As cmd.exe does: a name that ends with one of the extensions as it is, then with each.
    candidates = [file] if file.lower().endswith(tuple(extensions)) else []
    candidates += [file + extension for extension in extensions]
    for directory in filter(None, folders):
        for candidate in candidates:
            full = os.path.join(directory, candidate)  # noqa: PTH118
            if os.path.isfile(full):  # noqa: PTH113
                return full
    return None


def poppler(command: str) -> str | None:
    """Poppler's *command*: the one in the folder of pdftocairo, which only Poppler has.

    The first *command* on PATH may be another program's: on Windows, Git Bash puts Git's own
    pdftotext, from xpdf, which lacks Poppler's options, before every other.
    """
    cairo = which("pdftocairo")
    return None if cairo is None else which(command, str(Path(cairo).parent))


def resolve(name: str, path: str | None = None) -> str:
    """The absolute path of the program *name*, as which() finds it.

    FileNotFoundError, as starting a program that is not installed raises, when there is none.
    """
    found = which(name, path)
    if found is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)
    return found


def runnable(path: Path) -> bool:
    """Whether the file *path* is a program: on Windows, one with an extension that PATHEXT
    lists, and elsewhere, one with permission to run."""
    found = which(str(path))
    absolute = os.path.abspath(path)  # noqa: PTH100
    return found is not None and os.path.normcase(found) == os.path.normcase(absolute)


def libreoffice() -> str | None:
    """LibreOffice's command, on PATH or where LibreOffice's Windows installer puts it."""
    found = [which("libreoffice"), which("soffice")]
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable, "")
            # soffice.com, unlike soffice.exe, waits for the conversion and shows its output.
            command = Path(base, "LibreOffice", "program", "soffice.com")
            found.append(str(command) if base and command.is_file() else None)
    return next((command for command in found if command is not None), None)
