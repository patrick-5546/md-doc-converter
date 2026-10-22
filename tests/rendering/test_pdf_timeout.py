from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from md_doc_converter.rendering.pipeline import PDF_PRINTER
from md_doc_converter.toolchain.environment import render_environment, toolchain

PAGE = (
    "<!doctype html><html><head><title>Timeout</title>{script}</head>"
    "<body><h1>Timeout</h1></body></html>"
)
# Paged.js awaits PagedConfig.before() inside one browser call; never resolving it
# stalls layout deterministically.
STALL = "<script>window.PagedConfig = {before: () => new Promise(() => {})};</script>"


class PdfTimeoutTest(unittest.TestCase):
    def print_pdf(
        self, timeout: str, script: str = "", *, node_modules: bool = True
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        directory = tempfile.TemporaryDirectory(prefix="md-doc-converter-pdf-timeout-")
        self.addCleanup(directory.cleanup)
        html = Path(directory.name) / "page.html"
        html.write_text(PAGE.format(script=script), encoding="utf-8")
        pdf = html.with_suffix(".pdf")
        tools = toolchain()
        command = ["node", str(PDF_PRINTER)]
        if node_modules:
            command.extend(("--node-modules", str(tools.node_modules)))
        command.extend(("--timeout-seconds", timeout, "-o", str(pdf), str(html)))
        result = subprocess.run(
            command,
            cwd=directory.name,
            env=render_environment(tools),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return result, pdf

    def test_zero_timeout_is_accepted(self) -> None:
        result, pdf = self.print_pdf("0")
        assert result.returncode == 0, result.stderr
        assert pdf.read_bytes().startswith(b"%PDF-")

    def test_stalled_layout_names_the_setting_to_raise(self) -> None:
        result, pdf = self.print_pdf("1", STALL)
        assert result.returncode == 1, result.stderr
        assert "exceeded 1 seconds; raise --pdf-timeout" in result.stderr
        assert not pdf.exists()

    def test_invalid_arguments_are_usage_errors(self) -> None:
        cases = [(timeout, True) for timeout in ("", "-1", "1.5", "2147484")]
        cases.append(("10", False))
        for timeout, node_modules in cases:
            with self.subTest(timeout=timeout, node_modules=node_modules):
                result, pdf = self.print_pdf(timeout, node_modules=node_modules)
                assert result.returncode == 2, result.stderr
                assert "pagedjs-print:" in result.stderr
                assert not pdf.exists()


if __name__ == "__main__":
    unittest.main()
