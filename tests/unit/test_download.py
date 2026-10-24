"""Downloads: what they request and return, and why they fail."""

from __future__ import annotations

import socket

import pytest

from md_doc_converter import download
from tests.support.web_server import WebServer

MIB = 2**20


@pytest.mark.parametrize(
    ("url", "sent"),
    [
        ("/plan.svg#top", "/plan.svg"),
        ("/été plan.svg?name=ü", "/%C3%A9t%C3%A9%20plan.svg?name=%C3%BC"),
        ("/%C3%A9t%C3%A9.svg", "/%C3%A9t%C3%A9.svg"),
    ],
    ids=["without-the-fragment", "encoded-as-browsers-encode-it", "already-encoded"],
)
def test_a_download_returns_the_content_and_its_media_type(
    url: str, sent: str, web: WebServer
) -> None:
    web.files[sent] = (b"<svg/>", "image/svg+xml; charset=utf-8")

    assert download.download(web.url + url) == (b"<svg/>", "image/svg+xml")
    assert web.requests == [(sent, "md-doc-converter")]


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/missing.png", "the server answered 404 Not Found"),
        ("/large.png", "it is larger than 1 MiB"),
        ("/not-http", "the server's answer is not valid HTTP"),
    ],
    ids=["error", "too-large", "not-http"],
)
def test_a_failed_download_says_why(
    path: str, reason: str, web: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download, "MAX_BYTES", MIB)
    web.files["/large.png"] = (b"x" * (MIB + 1), "image/png")
    web.raw["/not-http"] = b"not HTTP\r\n"

    with pytest.raises(download.DownloadError) as caught:
        download.download(web.url + path)

    assert str(caught.value) == reason


@pytest.mark.usefixtures("no_proxy")
def test_a_download_fails_when_no_server_listens() -> None:
    with socket.create_server(("127.0.0.1", 0)) as closed:
        port = closed.getsockname()[1]

    with pytest.raises(
        download.DownloadError, match=r"^it could not be downloaded \(\[(?:Errno|WinError) "
    ):
        download.download(f"http://127.0.0.1:{port}/logo.png")


@pytest.mark.usefixtures("no_proxy")
def test_a_download_waits_only_so_long_for_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download, "TIMEOUT_SECONDS", 0.1)
    with socket.create_server(("127.0.0.1", 0)) as silent:
        url = f"http://127.0.0.1:{silent.getsockname()[1]}/logo.png"

        with pytest.raises(
            download.DownloadError, match=r"^it could not be downloaded \(timed out\)$"
        ):
            download.download(url)
