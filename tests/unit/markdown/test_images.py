"""Images that rendering embeds: files in the input's repository or folder, and downloads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import MarkdownDocument
from tests.support.web_server import WebServer


def test_only_image_files_inside_the_root_are_embedded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "docs"
    (folder / "pictures").mkdir(parents=True)
    (folder / "pictures" / "chart.png").write_bytes(b"chart")
    (folder / "notes.txt").write_text("notes", encoding="utf-8")
    (tmp_path / "secret.png").write_bytes(b"secret")
    (folder / "pictures" / "escape.png").symlink_to(tmp_path / "secret.png")
    chart, secret = folder / "pictures" / "chart.png", tmp_path / "secret.png"
    kept = [
        str(secret),
        secret.as_uri(),
        str(folder / "pictures" / "escape.png"),
        str(folder / "pictures"),
        str(folder / "notes.txt"),
        "https://example.test/remote.png",
        "ftp://example.test/remote.png",
        str(folder / "missing.png"),
    ]
    document = MarkdownDocument(
        f'![chart]({chart}#part) <img src="{chart}">\n\n'
        + " ".join(f"![kept]({target})" for target in kept)
        + "\n\n![data](data:image/png;base64,ZGF0YQ==) [link](https://example.test/page)\n"
    )
    embed = preprocess.ImageEmbedder(folder)

    embed(document)
    embed(MarkdownDocument(f"![again]({secret})\n"))
    result = document.render()

    assert "data:image/png;base64,Y2hhcnQ=#part" in result
    assert '<img src="data:image/png;base64,Y2hhcnQ=">' in result
    for target in kept:
        assert f"]({target})" in result
    assert "](data:image/png;base64,ZGF0YQ==)" in result
    assert "[link](https://example.test/page)" in result
    assert embed.unembedded == set(kept)
    # Each is reported once, except a missing image, which warn_broken_images reports.
    assert capsys.readouterr().err.splitlines() == [
        f"WARNING: image {secret} is not embedded: it is outside {folder}",
        f"WARNING: image {secret.as_uri()} is not embedded: it is outside {folder}",
        (
            f"WARNING: image {folder / 'pictures' / 'escape.png'} is not embedded: "
            f"it is outside {folder}"
        ),
        f"WARNING: image {folder / 'pictures'} is not embedded: it is not a file",
        f"WARNING: image {folder / 'notes.txt'} is not embedded: it is not an image",
        (
            "WARNING: image https://example.test/remote.png is not embedded: "
            "rendering downloads images only with --download-images"
        ),
        (
            "WARNING: image ftp://example.test/remote.png is not embedded: "
            "it is neither a local file nor an http(s) URL"
        ),
    ]


@pytest.mark.parametrize("git", [True, False], ids=["in-a-repository", "outside-git"])
@pytest.mark.parametrize("kind", ["file", "folder"])
def test_images_are_embedded_from_the_repository_or_the_folder_holding_the_input(
    tmp_path: Path, git: bool, kind: str
) -> None:
    project = tmp_path / "project"
    pages = project / "docs" / "guide"
    pages.mkdir(parents=True)
    if git:
        subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    source = pages if kind == "folder" else pages / "page.md"
    holder = project if git else source.parent.resolve()
    assert preprocess.image_root(source) == holder.resolve()


def test_images_shown_by_url_are_downloaded_once_each(
    web: WebServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    web.files["/logo.svg"] = (b"<svg/>", "image/svg+xml")
    web.files["/page.html"] = (b"<p>page</p>", "text/html")
    logo, page, missing = (f"{web.url}/{name}" for name in ("logo.svg", "page.html", "missing.png"))
    document = MarkdownDocument(
        f"![a]({logo}#top) ![b]({logo}) [link]({page})\n\n"
        f"![c]({page}) ![d]({missing}) ![e]({missing})\n"
    )
    embed = preprocess.ImageEmbedder(tmp_path, download=True)

    embed(document)

    assert document.render() == (
        "![a](data:image/svg+xml;base64,PHN2Zy8+#top) ![b](data:image/svg+xml;base64,PHN2Zy8+) "
        f"[link]({page})\n\n![c]({page}) ![d]({missing}) ![e]({missing})\n"
    )
    assert [path for path, _ in web.requests] == ["/logo.svg", "/page.html", "/missing.png"]
    assert embed.unembedded == {page, missing}
    assert capsys.readouterr().err.splitlines() == [
        f"WARNING: image {page} is not embedded: the server sent text/html, not an image",
        f"WARNING: image {missing} is not embedded: the server answered 404 Not Found",
    ]
