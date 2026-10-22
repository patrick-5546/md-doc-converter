"""Checks on the ZIP packages that Word and Excel files are, before they are read."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path

MIB = 1024 * 1024
# A crafted package can expand to far more than its own size; its parts are read into memory.
MAX_EXPANDED_SIZE = 1024 * MIB
# Office saves a document encrypted with a password or by rights management, and the 97-2003
# formats (.doc, .xls), as an OLE compound file, which starts with this signature.
_COMPOUND_FILE = bytes.fromhex("d0cf11e0a1b11ae1")


def too_large(entries: Iterable[zipfile.ZipInfo]) -> str | None:
    """Why a package whose members are *entries* is too large to read, or None.

    The declared sizes bound what Python's zipfile decompresses: it reads no member beyond its
    declared size, so they can be checked before any member is read.
    """
    expanded = sum(entry.file_size for entry in entries)
    if expanded <= MAX_EXPANDED_SIZE:
        return None
    return (
        f"expands to {expanded / MIB:,.0f} MiB, more than the {MAX_EXPANDED_SIZE // MIB:,} MiB "
        "that md-doc-converter reads"
    )


def is_compound_file(path: Path) -> bool:
    """Whether *path* is an OLE compound file: encrypted, or in an Office 97-2003 format."""
    with path.open("rb") as file:
        return file.read(len(_COMPOUND_FILE)) == _COMPOUND_FILE
