"""The PDF printer's --timeout-seconds, run with the real Node.js and browser."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from md_doc_converter.rendering.pipeline import PDF_PRINTER
from md_doc_converter.toolchain.environment import render_environment, toolchain

PAGE = (
    "<!doctype html><html><head><title>Timeout</title>{script}</head>"
    "<body><h1>Timeout</h1></body></html>"
)
# A script that never returns keeps the page from loading, which stalls the first
# browser step deterministically.
STALL = "<script>while (true) {}</script>"


def print_pdf(
    directory: Path, timeout: str, script: str = "", *, node_modules: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Print a page with *script* from *directory*; return the result and the PDF's path."""
    html = directory / "page.html"
    html.write_text(PAGE.format(script=script), encoding="utf-8")
    pdf = html.with_suffix(".pdf")
    tools = toolchain()
    command = ["node", str(PDF_PRINTER)]
    if node_modules:
        command.extend(("--node-modules", str(tools.node_modules)))
    command.extend(("--timeout-seconds", timeout, "-o", str(pdf), str(html)))
    result = subprocess.run(
        command,
        cwd=directory,
        env=render_environment(tools),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return result, pdf


def test_zero_timeout_is_accepted(tmp_path: Path) -> None:
    result, pdf = print_pdf(tmp_path, "0")
    assert result.returncode == 0, result.stderr
    assert pdf.read_bytes().startswith(b"%PDF-")


def test_stalled_page_names_the_setting_to_raise(tmp_path: Path) -> None:
    result, pdf = print_pdf(tmp_path, "1", STALL)
    assert result.returncode == 1, result.stderr
    assert "exceeded 1 seconds; raise --pdf-timeout" in result.stderr
    assert not pdf.exists()


@pytest.mark.parametrize(
    ("timeout", "node_modules"),
    [
        pytest.param("", True, id="empty-timeout"),
        pytest.param("-1", True, id="negative-timeout"),
        pytest.param("1.5", True, id="fractional-timeout"),
        pytest.param("2147484", True, id="above-maximum-timeout"),
        pytest.param("10", False, id="no-node-modules"),
    ],
)
def test_invalid_arguments_are_usage_errors(
    timeout: str, node_modules: bool, tmp_path: Path
) -> None:
    result, pdf = print_pdf(tmp_path, timeout, node_modules=node_modules)
    assert result.returncode == 2, result.stderr
    assert "print-pdf:" in result.stderr
    assert not pdf.exists()
