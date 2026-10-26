"""Markdown preparation and CSV conversion on real files and folders."""

from __future__ import annotations

import base64
import io
import subprocess
import sys
from pathlib import Path

import pytest

from md_doc_converter.console import Console, activated, shown
from md_doc_converter.markdown.document import MarkdownError
from md_doc_converter.markdown.preprocess import ignored_by_git, prepare_markdown
from tests.support import documents


def test_bit_range_definitions_in_fenced_code_are_unchanged(tmp_path: Path, pandoc: str) -> None:
    source = tmp_path / "input.md"
    source.write_text(
        "* [1:0]: prose definition\n\n```text\n[3:0]: fenced code\n```\n",
        encoding="utf-8",
    )

    result = documents.markdown(pandoc, prepare_markdown(source).document)
    assert r"- \[1:0\]: prose definition" in result
    assert "[3:0]: fenced code" in result


PNG = b"\x89PNG\r\n\x1a\n"
EMBEDDED = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


def test_words_quotes_reach_the_html_as_the_author_typed_them(tmp_path: Path, pandoc: str) -> None:
    # Pandoc reads the prepared document as it parsed it. Written back to Markdown, a curly
    # quote became a straight one, which read back as an apostrophe: the left quote of
    # "Set to \u20181\u20191:" became a right one.
    text = "Set to \u20181\u20191: on."
    source = tmp_path / "input.md"
    source.write_text(f"# Bits\n\n{text} See [bits](#bits).\n", encoding="utf-8")

    html = documents.convert(pandoc, prepare_markdown(source).document, "html5")

    assert text in html


def test_a_page_shows_an_image_from_a_shared_folder_of_its_repository(
    tmp_path: Path, pandoc: str
) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(PNG)
    (tmp_path / "docs").mkdir()
    page = tmp_path / "docs" / "page.md"
    page.write_text("# Page\n\n![Logo](../assets/logo.png)\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)

    prepared = prepare_markdown(page)
    written = documents.markdown(pandoc, prepared.document)

    assert EMBEDDED not in written
    assert f"![Logo]({EMBEDDED})" in prepared.embed_images(written)
    assert prepared.unembedded == frozenset()


def test_a_split_word_document_outside_git_shows_its_images(tmp_path: Path, pandoc: str) -> None:
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

    written = documents.markdown(pandoc, prepared.document)
    assert prepared.embed_images(written).count(EMBEDDED) == 2
    # Both pages show one image, whose data the HTML holds for each.
    assert len(prepared.images) == 1
    assert prepared.unembedded == frozenset()


def test_gitignored_sibling_title_files_are_not_collected(tmp_path: Path, pandoc: str) -> None:
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

    prepared = prepare_markdown(docs, respect_gitignore=True)
    assert documents.markdown(pandoc, prepared.document) == "# Included page\n"


def test_verbose_names_each_file_that_git_ignore_rules_skip_once(tmp_path: Path) -> None:
    # docs/topic.md is both an entry of docs and the title page of docs/topic.
    docs = tmp_path / "docs"
    (docs / "topic").mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("docs.md\ndocs/topic.md\n*.png\n", encoding="utf-8")
    for page in (tmp_path / "docs.md", docs / "topic.md", docs / "topic" / "page.md"):
        page.write_text("# Page\n", encoding="utf-8")
    (docs / "logo.png").write_bytes(PNG)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True)

    console = Console("md-to-html", "verbose", stream=(stream := io.StringIO()))
    with activated(console):
        prepare_markdown(docs, respect_gitignore=True)

    assert [line for line in stream.getvalue().splitlines() if "skipping" in line] == [
        f"md-to-html: skipping {shown(tmp_path / 'docs.md')}, which Git ignores",
        f"md-to-html: skipping {shown(docs / 'topic.md')}, which Git ignores",
    ]


@pytest.mark.parametrize(
    ("rules", "files", "message"),
    [
        pytest.param(
            "page.md\n",
            {"page.md": "# Page\n"},
            "no Markdown to render once Git-ignored files are skipped; "
            "--no-respect-gitignore includes them",
            id="markdown-file",
        ),
        pytest.param(
            "drafts/\n",
            {"drafts/page.md": "# Draft\n"},
            "no Markdown to render once Git-ignored files are skipped; "
            "--no-respect-gitignore includes them",
            id="folder",
        ),
        pytest.param(
            "logo.png\n",
            {"logo.png": "", "blank.md": "\n"},
            "no Markdown to render",
            id="other-file",
        ),
    ],
)
def test_a_folder_left_empty_by_git_ignore_rules_names_the_option_that_includes_them(
    rules: str, files: dict[str, str], message: str, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    for name, text in files.items():
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(text, encoding="utf-8")
    (docs / ".gitignore").write_text(rules, encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True)

    with pytest.raises(MarkdownError) as raised:
        prepare_markdown(docs, respect_gitignore=True)
    assert str(raised.value) == message


def test_git_ignores_a_folder_by_a_rule_for_it_or_a_parent(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    for folder in ("scratch/sub", "scratch/nested", "build", "docs/a", "kept"):
        (outer / folder).mkdir(parents=True)
    (outer / ".gitignore").write_text("/scratch/\nbuild/\ndocs/*\n", encoding="utf-8")
    for repository in (outer, outer / "scratch" / "nested"):
        subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True, capture_output=True)
    expected = {
        ".": False,
        "scratch/sub": True,
        # The top of a work tree, which its own repository answers for, not the enclosing one.
        "scratch/nested": False,
        "build": True,
        "docs": False,
        "docs/a": True,
        "kept": False,
    }
    # Only some Windows users may create symbolic links.
    if sys.platform != "win32":
        # A link is judged as the folder that it leads to, whatever its own name.
        (outer / "alias").symlink_to(outer / "build", target_is_directory=True)
        expected["alias"] = True

    assert {folder: ignored_by_git(outer / folder) for folder in expected} == expected


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
