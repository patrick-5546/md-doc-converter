"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from md_doc_converter.converters.docx import cli as _cli
from tests.support.docx import (
    PNG,
    hyperlink,
    image,
    paragraph,
    write_docx,
)

COMMAND = [sys.executable, "-m", "md_doc_converter", "docx-to-md"]


class CLITests(unittest.TestCase):
    def test_only_approved_options_are_accepted(self) -> None:
        args = _cli.parse_args(["input.docx"])
        assert args.output is None
        assert args.split_depth == 0
        assert not args.qualify_duplicate_headings
        for flag in (
            "--image-dir",
            "--mode",
            "--review-report",
            "--repair-headings",
            "--normalize-status",
            "--omit-index",
            "--list-style",
        ):
            with (
                self.subTest(flag=flag),
                contextlib.redirect_stderr(io.StringIO()),
                pytest.raises(SystemExit),
            ):
                _cli.parse_args(["input.docx", flag, "value"])

    def test_split_range_is_enforced(self) -> None:
        for value in ("-1", "7", "text"):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                pytest.raises(SystemExit),
            ):
                _cli.parse_args(["input.docx", "--split-depth", value])

    def test_actual_cli_preserves_caller_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-cli-test-") as temporary:
            root = Path(temporary)
            write_docx(
                root / "in" / "Renamed.v2.docx",
                [paragraph("CLI text"), image()],
                relations=(("image", "image", "media/image.png", "Internal"),),
                media={"image.png": PNG},
            )
            result = subprocess.run(
                [
                    *COMMAND,
                    "in/Renamed.v2.docx",
                    "-o",
                    "out/Explicit Name.md",
                    "--keep-intermediates",
                    "debug",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "CLI text" in (root / "out" / "Explicit Name.md").read_text(encoding="utf-8")
            assert (root / "out" / ".images" / "Explicit Name" / "image.png").is_file()
            assert (root / "debug" / "diagnostics.json").is_file()
            assert not (root / "in" / "Renamed.v2.md").exists()

    def test_cli_error_has_no_traceback(self) -> None:
        result = subprocess.run(
            [*COMMAND, "/nonexistent-input.docx"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "docx-to-md: error:" in result.stderr
        assert "Traceback" not in result.stderr

    def test_cli_converts_and_summarizes_what_was_not_applied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-cli-test-") as temporary:
            root = Path(temporary)
            write_docx(root / "in.docx", [hyperlink("Missing", anchor="_Missing")])
            result = subprocess.run(
                [*COMMAND, "in.docx"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert (root / "in.md").read_text(encoding="utf-8") == "Missing\n"
        assert (
            "docx-to-md: warning: links: link to missing Word bookmark '_Missing'; "
            "kept as plain text" in result.stderr
        )
        assert "docx-to-md: transforms (baseline: MarkItDown)" in result.stderr
        assert "  links: not applied (1)" in result.stderr
        assert "  checks: passed" in result.stderr
        assert result.stdout.startswith("converted in.docx -> in.md: 1 page(s)")
        assert "docx-to-md: explain" not in result.stderr

    def test_cli_explains_what_was_not_applied_without_document_text(self) -> None:
        assert not _cli.parse_args(["input.docx"]).explain
        with tempfile.TemporaryDirectory(prefix="docx-cli-test-") as temporary:
            root = Path(temporary)
            write_docx(root / "in.docx", [hyperlink("Zephyr", anchor="_Zanzibar")])
            result = subprocess.run(
                [*COMMAND, "in.docx", "--dry-run", "--explain"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert not (root / "in.md").exists()
        summary, explanation = result.stderr.split(
            "docx-to-md: explain (fixed labels and counts only; no document text)\n"
        )
        assert "  checks: passed\n" in summary
        assert (
            explanation == "  document: paragraphs 1; text boxes 0\n"
            "  links: link to a missing Word bookmark; kept as plain text (1)\n"
            "    from: link 1\n"
            "    name: other hidden 1\n"
            "    bookmark: nowhere 1\n"
        )
