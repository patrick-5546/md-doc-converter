"""A local web server for the tests of downloads."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typing_extensions import override


@dataclass
class WebServer:
    """Serves `files`, a body and content type by request path, and `raw` answers as they are.

    Any other path answers 404. `requests` records the path and User-Agent of each request.
    """

    url: str = ""
    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    raw: dict[str, bytes] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)


@contextmanager
def serve() -> Generator[WebServer, None, None]:
    """Run a WebServer on 127.0.0.1, on a free port, until the block ends."""
    site = WebServer()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            site.requests.append((self.path, self.headers.get("User-Agent", "")))
            if self.path in site.raw:
                self.wfile.write(site.raw[self.path])
                return
            if self.path not in site.files:
                self.send_error(404)
                return
            body, kind = site.files[self.path]
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            del format, args

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        # Polling often lets shutdown() return without the default half-second wait.
        thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        site.url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield site
        finally:
            server.shutdown()
            thread.join()
