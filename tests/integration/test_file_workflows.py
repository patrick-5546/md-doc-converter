"""Markdown preparation and CSV conversion on real files and folders."""

from __future__ import annotations

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

    result = prepare_markdown(source)
    assert r"* \[1:0\]: prose definition" in result
    assert "[3:0]: fenced code" in result


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

    assert prepare_markdown(docs, respect_gitignore=True) == "# Included page\n"


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
