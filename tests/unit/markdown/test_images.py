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
    prepared = document.render()
    result = preprocess.embed_images(prepared, embed.images)

    # Pandoc reads a short placeholder for the chart's data, which the HTML gets after it.
    assert "Y2hhcnQ=" not in prepared
    assert list(embed.images.values()) == ["Y2hhcnQ="]
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


def test_placeholders_are_unique_to_each_render_and_only_its_own_are_replaced(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.png").write_bytes(b"a")
    first, second = preprocess.ImageEmbedder(tmp_path), preprocess.ImageEmbedder(tmp_path)

    first(MarkdownDocument(f"![a]({tmp_path / 'a.png'})\n"))
    second(MarkdownDocument(f"![a]({tmp_path / 'a.png'})\n"))
    [mine], [other] = first.images, second.images

    assert mine != other
    assert preprocess.IMAGE_PLACEHOLDER.fullmatch(mine)
    # A document cannot know a render's placeholders, and another render's stay text.
    assert preprocess.embed_images(f"{mine} {other} {mine}x", first.images) == (
        f"YQ== {other} YQ==x"
    )


def test_pandoc_keeps_the_placeholders_that_the_image_data_replaces(
    pandoc: str, tmp_path: Path
) -> None:
    # Pandoc drops an image whose data URI it cannot decode, so a placeholder must be base64.
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"chart")
    document = MarkdownDocument(f'![chart]({chart})\n\n<img src="{chart}">\n')
    embed = preprocess.ImageEmbedder(tmp_path)
    embed(document)
    prepared = tmp_path / "prepared.md"
    prepared.write_text(document.render(), encoding="utf-8")

    html = subprocess.run(
        [
            pandoc,
            "--sandbox",
            "--embed-resources",
            "--standalone",
            "--metadata",
            "title=t",
            "--from",
            "markdown-implicit_figures",
            "--to",
            "html",
            str(prepared),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert (
        preprocess.embed_images(html, embed.images).count('src="data:image/png;base64,Y2hhcnQ="')
        == 2
    )


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
    prepared = document.render()

    assert "PHN2Zy8+" not in prepared
    assert list(embed.images.values()) == ["PHN2Zy8+"]
    assert preprocess.embed_images(prepared, embed.images) == (
        "![a](data:image/svg+xml;base64,PHN2Zy8+#top) ![b](data:image/svg+xml;base64,PHN2Zy8+) "
        f"[link]({page})\n\n![c]({page}) ![d]({missing}) ![e]({missing})\n"
    )
    assert [path for path, _ in web.requests] == ["/logo.svg", "/page.html", "/missing.png"]
    assert embed.unembedded == {page, missing}
    assert capsys.readouterr().err.splitlines() == [
        f"WARNING: image {page} is not embedded: the server sent text/html, not an image",
        f"WARNING: image {missing} is not embedded: the server answered 404 Not Found",
    ]
