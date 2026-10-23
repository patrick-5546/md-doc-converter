"""Markdown preparation and CSV conversion on real files and folders."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

from md_doc_converter.markdown.preprocess import prepare_markdown


def test_bit_range_definitions_in_fenced_code_are_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "input.md"
    source.write_text(
        "* [1:0]: prose definition\n\n```text\n[3:0]: fenced code\n```\n",
        encoding="utf-8",
    )

    result = prepare_markdown(source).text
    assert r"* \[1:0\]: prose definition" in result
    assert "[3:0]: fenced code" in result


PNG = b"\x89PNG\r\n\x1a\n"
EMBEDDED = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


def test_a_page_shows_an_image_from_a_shared_folder_of_its_repository(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(PNG)
    (tmp_path / "docs").mkdir()
    page = tmp_path / "docs" / "page.md"
    page.write_text("# Page\n\n![Logo](../assets/logo.png)\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)

    prepared = prepare_markdown(page)

    assert f"![Logo]({EMBEDDED})" in prepared.text
    assert prepared.unembedded == frozenset()


def test_a_split_word_document_outside_git_shows_its_images(tmp_path: Path) -> None:
    # docx-to-md's layout: the title page and the images beside the folder of pages.
    (tmp_path / ".images" / "guide").mkdir(parents=True)
    (tmp_path / ".images" / "guide" / "chart.png").write_bytes(PNG)
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n![Chart](.images/guide/chart.png)\n", encoding="utf-8"
    )
    (tmp_path / "guide" / "01-first.md").write_text(
        "# First\n\n![Again](../.images/guide/chart.png)\n", encoding="utf-8"
    )

    prepared = prepare_markdown(tmp_path / "guide")

    assert prepared.text.count(EMBEDDED) == 2
    assert prepared.unembedded == frozenset()


def test_gitignored_sibling_title_files_are_not_collected(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    topic = docs / "topic"
    topic.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text(
        "docs.md\ndocs/topic.md\n",
        encoding="utf-8",
    )
    (tmp_path / "docs.md").write_text("# Ignored root title\n", encoding="utf-8")
    (docs / "topic.md").write_text("# Ignored topic title\n", encoding="utf-8")
    (topic / "page.md").write_text("# Included page\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert prepare_markdown(docs, respect_gitignore=True).text == "# Included page\n"


def test_recursive_csv_output_preserves_relative_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    for subdirectory, value in (("first", "one"), ("second", "two")):
        csv_directory = source / subdirectory
        csv_directory.mkdir(parents=True)
        (csv_directory / "signals.csv").write_text(
            f"Name,Value\nsignal,{value}\n",
            encoding="utf-8",
        )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "md_doc_converter",
            "csv-to-md",
            str(source),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "one" in (output / "first/signals.md").read_text(encoding="utf-8")
    assert "two" in (output / "second/signals.md").read_text(encoding="utf-8")
    assert not (output / "signals.md").exists()
