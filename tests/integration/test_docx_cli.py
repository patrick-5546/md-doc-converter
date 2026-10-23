"""The docx-to-md command: its options, and conversions run as `python -m md_doc_converter`."""

from __future__ import annotations

import re
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
    assert args.math == "github"
    assert _cli.parse_args(["input.docx", "--math", "dollars"]).math == "dollars"


def test_unknown_options_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        _cli.parse_args(["input.docx", "--image-dir", "images"])
    assert stopped.value.code == 2
    assert "error: unrecognized arguments: --image-dir images" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "message"),
    [
        # Python 3.14 and later quote the value; earlier versions do not.
        pytest.param("-1", "invalid choice: '?-1'?", id="negative"),
        pytest.param("7", "invalid choice: '?7'?", id="too-deep"),
        pytest.param("text", "invalid int value: 'text'", id="not-a-number"),
    ],
)
def test_split_range_is_enforced(
    value: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        _cli.parse_args(["input.docx", "--split-depth", value])
    assert stopped.value.code == 2
    assert re.search(f"error: argument --split-depth: {message}", capsys.readouterr().err)


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
    assert "docx-to-md: transforms\n" in result.stderr
    assert "  links: not applied (1)" in result.stderr
    assert "  checks: passed" in result.stderr
    assert result.stdout.startswith("converted in.docx -> in.md: 1 page(s)")
