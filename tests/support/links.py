"""Links to folders: symbolic links, and on Windows junctions, which any user may create."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The kinds of link to a folder that this platform has, as pytest parameters.
FOLDER_LINKS = [
    pytest.param(kind, id=kind)
    for kind in (("symlink", "junction") if sys.platform == "win32" else ("symlink",))
]


def link_folder(kind: str, link: Path, target: Path) -> None:
    """Make *link* a link of the *kind* to the folder *target*.

    A junction needs its folder to exist, so *target* is created for one.
    """
    if sys.platform == "win32" and kind == "junction":
        import _winapi

        target.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
