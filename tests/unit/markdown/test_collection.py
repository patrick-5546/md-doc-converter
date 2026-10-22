from __future__ import annotations

import contextlib
import io
import re
import shutil
import tempfile
import unittest
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from md_doc_converter.markdown import preprocess

PANDOC = shutil.which("pandoc")


def write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def collected_titles(docs: Path) -> list[str]:
    with contextlib.redirect_stderr(io.StringIO()):
        result = preprocess.collect_directory(docs, defaultdict(Counter))
    return re.findall(r"^# (.+?)\s*$", result, re.MULTILINE)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class DirectoryOrderTest(unittest.TestCase):
    def test_order_keeps_a_title_page_with_its_directory(self) -> None:
        iterdir = Path.iterdir
        for last in ("topic.md", "topic"):

            def listed_last(directory: Path, entry: str = last) -> Iterator[Path]:
                paths = sorted(iterdir(directory), key=lambda path: path.name == entry)
                return iter(paths)

            with self.subTest(listed_last=last), tempfile.TemporaryDirectory() as directory:
                docs = Path(directory) / "docs"
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

                with mock.patch.object(Path, "iterdir", autospec=True, side_effect=listed_last):
                    titles = collected_titles(docs)

                assert titles == ["Topic", "Child", "A", "Z"]

    def test_order_keeps_a_directory_listed_before_the_first_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory) / "docs"
            write_tree(
                docs,
                {
                    ".order": "sub\na\n",
                    "a.md": "# A\n",
                    "sub/page.md": "# Sub page\n",
                },
            )

            assert collected_titles(docs) == ["Sub page", "A"]

    def test_unordered_directory_leads_with_its_first_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory) / "docs"
            write_tree(
                docs,
                {
                    "aaa/page.md": "# Nested\n",
                    "readme.md": "# Readme\n",
                    "zzz.md": "# Last\n",
                },
            )

            assert collected_titles(docs) == ["Readme", "Nested", "Last"]


if __name__ == "__main__":
    unittest.main()
