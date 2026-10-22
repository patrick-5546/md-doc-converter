from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReviewFixTest(unittest.TestCase):
    def test_bit_range_definitions_in_fenced_code_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.md"
            output = root / "output.md"
            source.write_text(
                "* [1:0]: prose definition\n\n```text\n[3:0]: fenced code\n```\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "md_doc_converter.markdown.preprocess",
                    "-i",
                    str(source),
                    "-o",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            result = output.read_text(encoding="utf-8")
            assert r"* \[1:0\]: prose definition" in result
            assert "[3:0]: fenced code" in result

    def test_gitignored_sibling_title_files_are_not_collected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            docs = repository / "docs"
            topic = docs / "topic"
            topic.mkdir(parents=True)
            (repository / ".gitignore").write_text(
                "docs.md\ndocs/topic.md\n",
                encoding="utf-8",
            )
            (repository / "docs.md").write_text("# Ignored root title\n", encoding="utf-8")
            (docs / "topic.md").write_text("# Ignored topic title\n", encoding="utf-8")
            (topic / "page.md").write_text("# Included page\n", encoding="utf-8")
            output = repository / "output.md"
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "md_doc_converter.markdown.preprocess",
                    "--respect-gitignore",
                    "-i",
                    str(docs),
                    "-o",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            result = output.read_text(encoding="utf-8")
            assert result == "# Included page\n"

    def test_recursive_csv_output_preserves_relative_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
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


if __name__ == "__main__":
    unittest.main()
