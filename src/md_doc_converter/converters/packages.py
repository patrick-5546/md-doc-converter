"""Limits on the ZIP packages that Word and Excel files are."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable

MIB = 1024 * 1024
# A crafted package can expand to far more than its own size; its parts are read into memory.
MAX_EXPANDED_SIZE = 1024 * MIB


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
