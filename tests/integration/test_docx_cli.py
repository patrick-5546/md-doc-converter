"""The docx-to-md command: its options, and conversions run as `python -m md_doc_converter`."""

from __future__ import annotations

import subprocess
import sys
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


def docx_to_md(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run `md-doc-converter docx-to-md` with the arguments, from *cwd*."""
    return subprocess.run(
        [*COMMAND, *arguments], cwd=cwd, check=False, capture_output=True, text=True
    )


def test_options_default_to_one_unqualified_page_beside_the_input() -> None:
    args = _cli.parse_args(["input.docx"])
    assert args.output is None
    assert args.split_depth == 0
    assert not args.qualify_duplicate_headings


@pytest.mark.parametrize(
    "flag",
    [
        "--image-dir",
        "--mode",
        "--review-report",
        "--repair-headings",
        "--normalize-status",
        "--omit-index",
        "--list-style",
    ],
)
def test_only_approved_options_are_accepted(flag: str) -> None:
    with pytest.raises(SystemExit):
        _cli.parse_args(["input.docx", flag, "value"])


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("-1", id="negative"),
        pytest.param("7", id="too-deep"),
        pytest.param("text", id="not-a-number"),
    ],
)
def test_split_range_is_enforced(value: str) -> None:
    with pytest.raises(SystemExit):
        _cli.parse_args(["input.docx", "--split-depth", value])


def test_actual_cli_preserves_caller_relative_paths(tmp_path: Path) -> None:
    write_docx(
        tmp_path / "in" / "Renamed.v2.docx",
        [paragraph("CLI text"), image()],
        relations=(("image", "image", "media/image.png", "Internal"),),
        media={"image.png": PNG},
    )
    result = docx_to_md(
        "in/Renamed.v2.docx",
        "-o",
        "out/Explicit Name.md",
        "--keep-intermediates",
        "debug",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "CLI text" in (tmp_path / "out" / "Explicit Name.md").read_text(encoding="utf-8")
    assert (tmp_path / "out" / ".images" / "Explicit Name" / "image.png").is_file()
    assert (tmp_path / "debug" / "diagnostics.json").is_file()
    assert not (tmp_path / "in" / "Renamed.v2.md").exists()


def test_cli_error_has_no_traceback() -> None:
    result = docx_to_md("/nonexistent-input.docx")
    assert result.returncode == 1
    assert "docx-to-md: error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_converts_and_summarizes_what_was_not_applied(tmp_path: Path) -> None:
    write_docx(tmp_path / "in.docx", [hyperlink("Missing", anchor="_Missing")])
    result = docx_to_md("in.docx", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "in.md").read_text(encoding="utf-8") == "Missing\n"
    assert (
        "docx-to-md: warning: links: link to missing Word bookmark '_Missing'; "
        "kept as plain text" in result.stderr
    )
    assert "docx-to-md: transforms (baseline: MarkItDown)" in result.stderr
    assert "  links: not applied (1)" in result.stderr
    assert "  checks: passed" in result.stderr
    assert result.stdout.startswith("converted in.docx -> in.md: 1 page(s)")
    assert "docx-to-md: explain" not in result.stderr


def test_cli_explains_what_was_not_applied_without_document_text(tmp_path: Path) -> None:
    assert not _cli.parse_args(["input.docx"]).explain
    write_docx(tmp_path / "in.docx", [hyperlink("Zephyr", anchor="_Zanzibar")])
    result = docx_to_md("in.docx", "--dry-run", "--explain", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "in.md").exists()
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
