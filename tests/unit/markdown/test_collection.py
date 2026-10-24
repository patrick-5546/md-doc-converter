"""The order in which a folder's pages are combined: `.order` files and title pages."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.headings import collect_headings


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def collected_titles(docs: Path) -> list[str]:
    result = preprocess.collect_directory(docs, defaultdict(Counter))
    blocks = result["blocks"]
    assert isinstance(blocks, list)
    return [heading.text for heading in collect_headings(blocks) if heading.level == 1]


@pytest.mark.parametrize(
    "last",
    [
        pytest.param("topic.md", id="title-page-listed-last"),
        pytest.param("topic", id="directory-listed-last"),
    ],
)
def test_order_keeps_a_title_page_with_its_directory(
    last: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iterdir = Path.iterdir

    def listed_last(directory: Path) -> Iterator[Path]:
        """List a folder with the entry named `last` at the end."""
        paths = sorted(iterdir(directory), key=lambda path: path.name == last)
        return iter(paths)

    docs = tmp_path / "docs"
    write_tree(
        docs,
        {
            ".order": "topic\na\nz\n",
            "a.md": "# A\n",
            "topic.md": "# Topic\n",
            "topic/child.md": "# Child\n",
            "z.md": "# Z\n",
        },
    )
    monkeypatch.setattr(Path, "iterdir", listed_last)

    titles = collected_titles(docs)

    assert titles == ["Topic", "Child", "A", "Z"]


def test_order_keeps_a_directory_listed_before_the_first_file(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_tree(
        docs,
        {
            ".order": "sub\na\n",
            "a.md": "# A\n",
            "sub/page.md": "# Sub page\n",
        },
    )

    assert collected_titles(docs) == ["Sub page", "A"]


def test_unordered_directory_leads_with_its_first_file(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    write_tree(
        docs,
        {
            "aaa/page.md": "# Nested\n",
            "readme.md": "# Readme\n",
            "zzz.md": "# Last\n",
        },
    )

    assert collected_titles(docs) == ["Readme", "Nested", "Last"]


def test_a_list_that_ends_a_page_does_not_run_on_into_the_next(tmp_path: Path) -> None:
    # Joined as Markdown, the two pages' lists read as one list of three items.
    docs = tmp_path / "docs"
    write_tree(docs, {"a.md": "# A\n\n- one\n- two\n", "b.md": "- three\n"})

    result = preprocess.collect_directory(docs, defaultdict(Counter))

    blocks = result["blocks"]
    assert isinstance(blocks, list)
    lists = [
        block["c"] for block in blocks if isinstance(block, dict) and block["t"] == "BulletList"
    ]
    assert [len(items) if isinstance(items, list) else None for items in lists] == [2, 1]
