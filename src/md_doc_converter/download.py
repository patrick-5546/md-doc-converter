"""Downloads of the images and stylesheet files that rendering embeds."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit

# A download waits at most this long for each answer from the server, and reads at most this much.
TIMEOUT_SECONDS: float = 30
MAX_BYTES = 20 * 1024 * 1024

# What a URL may contain besides letters, digits and "-._~". Any other character in a path or
# query, such as a space or "é", is sent percent-encoded, as browsers send it.
_URL_CHARACTERS = ":/?#[]@!$&'()*+,;=%"


class DownloadError(Exception):
    """Why a download failed, as a clause such as "the server answered 404 Not Found"."""


def download(url: str) -> tuple[bytes, str]:
    """Download http(s) *url*, returning its content and media type; DownloadError if it fails."""
    parts = urlsplit(url)
    address = urlunsplit(
        parts._replace(
            path=quote(parts.path, safe=_URL_CHARACTERS),
            query=quote(parts.query, safe=_URL_CHARACTERS),
            fragment="",
        )
    )
    request = urllib.request.Request(address, headers={"User-Agent": "md-doc-converter"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = response.read(MAX_BYTES + 1)
            kind = response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        error.close()  # It holds the server's answer open.
        raise DownloadError(f"the server answered {error.code} {error.reason}") from error
    except (OSError, ValueError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise DownloadError(f"it could not be downloaded ({reason})") from error
    except http.client.HTTPException as error:
        # Its message can quote what the server sent instead.
        raise DownloadError("the server's answer is not valid HTTP") from error
    if len(data) > MAX_BYTES:
        raise DownloadError(f"it is larger than {MAX_BYTES // 2**20} MiB")
    return data, kind
