r"""File system paths as the URL paths that link and image targets hold, and back.

A URL path separates folders with slashes and percent-encodes the characters that would end the
path or start a query or fragment. On Windows, it names the folder C:\docs as /C:/docs, as a
file URL does, and a network share's \\server\share as //server/share.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, unquote

WINDOWS = os.name == "nt"
# A drive, such as C:, at the start of a Windows path or, after a slash, of its URL path.
_DRIVE = re.compile(r"[A-Za-z]:(?=/|$)")
_URL_DRIVE = re.compile(r"/[A-Za-z]:(?=/|$)")


def url_path(path: str, *, windows: bool = WINDOWS) -> str:
    """The URL path of the file system *path*, absolute or relative, percent-encoded."""
    if not windows:
        return quote(path, safe="/")
    path = path.replace("\\", "/")
    if _DRIVE.match(path):
        path = "/" + path
    # Windows allows a colon only after a drive letter.
    return quote(path, safe="/:")


def file_path(path: str, *, windows: bool = WINDOWS) -> str:
    """The file system path that the URL path *path* names, percent-decoded."""
    decoded = unquote(path)
    if not windows:
        return decoded
    if _URL_DRIVE.match(decoded):
        decoded = decoded[1:]
    return decoded.replace("/", "\\")
