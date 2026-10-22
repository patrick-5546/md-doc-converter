from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

from md_doc_converter.rendering.header import css_string
from md_doc_converter.toolchain.environment import PANDOC_VERSION
from md_doc_converter.toolchain.install import PANDOC
from tests.support.fake_tools import (
    KATEX_VERSION,
    install_fake_toolchain,
    read_log,
    write_fake_tools,
)


class CommandLineTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "tools.jsonl"
        self.bin = write_fake_tools(self.root / "fake-bin", self.log)
        self.tools = install_fake_toolchain(self.root / "home", self.log)
        self.workspace = self.root / "workspace"
        self.docs = self.workspace / "docs"
        self.docs.mkdir(parents=True)
        self.source = self.docs / "input.md"
        self.source.write_text("# Example\n")

    def run_cli(
        self,
        *arguments: str,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        selected = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MD_DOC_CONVERTER_", "FAKE_TOOL_"))
        }
        selected["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        selected["MD_DOC_CONVERTER_HOME"] = str(self.tools.root)
        selected.update(environment or {})
        result = subprocess.run(
            [sys.executable, "-m", "md_doc_converter", *arguments],
            cwd=cwd or self.workspace,
            env=selected,
            capture_output=True,
            text=True,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def calls(self, tool: str) -> list[dict[str, object]]:
        return [call for call in read_log(self.log) if call["tool"] == tool]

    def test_version_and_help(self) -> None:
        assert re.search(r"^md-doc-converter \d+\.\d+\.\d+", self.run_cli("--version").stdout)
        assert "md-to-pdf" in self.run_cli().stdout
        result = self.run_cli("not-a-command", check=False)
        assert result.returncode == 2
        assert "invalid choice" in result.stderr

    def test_render_defaults_next_to_files_and_inside_folders(self) -> None:
        result = self.run_cli("md-to-html", "docs/input.md")
        html = self.docs / "input.html"
        assert f"-> {html}" in result.stdout
        assert "# Example" in html.read_text()
        self.run_cli("md-to-pdf", "docs")
        assert (self.docs / "docs.pdf").read_bytes() == b"%PDF-fake"
        (self.docs / "docs.pdf").unlink()
        self.run_cli("md-to-pdf", ".", cwd=self.docs)
        assert (self.docs / "docs.pdf").read_bytes() == b"%PDF-fake"
        assert sorted(path.name for path in self.docs.iterdir()) == [
            "docs.pdf",
            "input.html",
            "input.md",
        ]
        assert sorted(path.name for path in self.workspace.iterdir()) == ["docs"]
        for call in self.calls("pandoc") + self.calls("node"):
            scratch = Path(str(call["cwd"]))
            assert not scratch.exists()
            assert not scratch.is_relative_to(self.workspace)
        printer = self.calls("node")[0]["arguments"]
        assert isinstance(printer, list)
        assert printer[printer.index("--node-modules") + 1] == str(self.tools.node_modules)
        environment = self.calls("pandoc")[0]["environment"]
        assert isinstance(environment, dict)
        assert environment["PUPPETEER_CACHE_DIR"] == str(self.tools.puppeteer_cache)
        assert str(environment["PATH"]).startswith(str(self.tools.node_bin))
        arguments = self.calls("pandoc")[0]["arguments"]
        assert isinstance(arguments, list)
        assert (
            arguments[arguments.index("--css") + 1]
            == f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css"
        )
        assert environment["MD_DOC_CONVERTER_NODE_MODULES"] == str(self.tools.node_modules)
        # The fake PATH also has a pandoc; the toolchain's pinned one comes first.
        assert {call["path"] for call in self.calls("pandoc")} == {str(self.tools.pandoc)}

    def test_paths_preserve_spaces_quotes_and_apostrophes(self) -> None:
        source = self.docs / 'input file\'s "review".md'
        source.write_text("# Example\n")
        relative = Path('review output\'s "final"') / 'chosen "name".pdf'
        absolute = self.root / "absolute output" / "chosen.pdf"
        for output, expected in (
            (str(relative), self.workspace / relative),
            (str(absolute), absolute),
        ):
            with self.subTest(output=output):
                self.run_cli("md-to-pdf", str(source.relative_to(self.workspace)), "-o", output)
                assert expected.read_bytes() == b"%PDF-fake"
        self.run_cli("md-to-html", str(source))
        assert source.with_suffix(".html").is_file()

    def test_wrong_output_suffix_is_rejected_before_rendering(self) -> None:
        result = self.run_cli("md-to-pdf", "docs/input.md", "-o", "out.html", check=False)
        assert result.returncode == 1
        assert "Output must end in .pdf" in result.stderr
        assert read_log(self.log) == []
        assert not (self.workspace / "out.html").exists()

    def test_header_and_footer_flags_override_the_environment(self) -> None:
        environment = {
            "MD_DOC_CONVERTER_HEADER": "Environment header",
            "MD_DOC_CONVERTER_FOOTER": "Environment footer",
        }
        self.run_cli("md-to-html", "docs/input.md", environment=environment)
        header = 'Review "A" & $value </style>'
        self.run_cli(
            "md-to-html",
            "docs/input.md",
            "--header",
            header,
            "--footer",
            "",
            environment=environment,
        )
        first, second = (str(call["styles"]) for call in self.calls("pandoc"))
        assert 'content: "Environment header"' in first
        assert 'content: "Environment footer"' in first
        assert f"content: {css_string(header)}" in second
        assert "Environment" not in second
        assert second.count("</style>") == 1
        assert "Review" in (self.docs / "input.html").read_text()

    def test_pdf_timeout_flag_environment_and_default(self) -> None:
        def timeout(*arguments: str, environment: dict[str, str] | None = None) -> str:
            self.run_cli("md-to-pdf", "docs/input.md", *arguments, environment=environment)
            command = self.calls("node")[-1]["arguments"]
            assert isinstance(command, list)
            return str(command[command.index("--timeout-seconds") + 1])

        assert timeout() == "600"
        assert timeout(environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "0"}) == "0"
        assert (
            timeout("--pdf-timeout", "30", environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "0"})
            == "30"
        )
        result = self.run_cli(
            "md-to-pdf",
            "docs/input.md",
            environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "soon"},
            check=False,
        )
        assert result.returncode == 1
        assert "--pdf-timeout (MD_DOC_CONVERTER_PDF_TIMEOUT) must be whole seconds" in result.stderr

    def test_missing_toolchain_fails_without_rendering(self) -> None:
        result = self.run_cli(
            "md-to-pdf",
            "docs/input.md",
            environment={"MD_DOC_CONVERTER_HOME": str(self.root / "not installed")},
            check=False,
        )
        assert result.returncode == 1
        assert "rendering prerequisites are missing" in result.stderr
        assert "md-doc-converter setup" in result.stderr
        assert "md-doc-converter doctor" in result.stderr
        assert read_log(self.log) == []
        assert not (self.docs / "input.pdf").exists()

    def test_failed_render_keeps_the_previous_file_and_requested_intermediates(self) -> None:
        previous = self.docs / "input.pdf"
        previous.write_bytes(b"previous")
        kept = self.root / "kept"
        result = self.run_cli(
            "md-to-pdf",
            "docs/input.md",
            "--keep-intermediates",
            str(kept),
            environment={"FAKE_TOOL_FAIL": "node"},
            check=False,
        )
        assert result.returncode == 1
        assert "node failed with exit status 3" in result.stderr
        assert previous.read_bytes() == b"previous"
        assert sorted(path.name for path in self.docs.iterdir()) == ["input.md", "input.pdf"]
        assert (kept / "markdown" / "input.md").read_text() == "# Example\n"
        assert (kept / "page-styles.html").is_file()
        assert (kept / "render" / "input.html").is_file()

    def test_gitignore_filtering_is_automatic_inside_git_work_trees(self) -> None:
        (self.docs / "ignored.md").write_text("# Ignored\n")
        (self.workspace / ".gitignore").write_text("ignored.md\n")
        subprocess.run(["git", "init", "--quiet"], cwd=self.workspace, check=True)
        self.run_cli("md-to-html", "docs")
        assert "Ignored" not in str(self.calls("pandoc")[-1]["input"])
        self.run_cli("md-to-html", "docs", "--no-respect-gitignore")
        assert "Ignored" in str(self.calls("pandoc")[-1]["input"])

        outside = self.root / "outside"
        outside.mkdir()
        (outside / "page.md").write_text("# Page\n")
        quiet = self.run_cli("md-to-html", str(outside))
        assert "WARNING" not in quiet.stderr
        explicit = self.run_cli("md-to-html", str(outside), "--respect-gitignore")
        assert "--respect-gitignore had no effect" in explicit.stderr

    def test_csv_to_md_resolves_output_paths_from_the_working_directory(self) -> None:
        source = self.docs / 'signal table\'s "review".csv'
        source.write_text("Name,Value\nsignal,one\n")
        absolute = self.root / "absolute output"
        for output, expected_directory in (
            (None, source.parent),
            ("converted", self.workspace / "converted"),
            ('converted output\'s "review"', self.workspace / 'converted output\'s "review"'),
            (str(absolute), absolute),
        ):
            with self.subTest(output=output):
                arguments = ["csv-to-md", str(source.relative_to(self.workspace))]
                if output is not None:
                    arguments += ["-o", output]
                self.run_cli(*arguments)
                converted = expected_directory / f"{source.stem}.md"
                assert "| signal | one |" in converted.read_text()
                assert "md-doc-converter (csv-to-md)" in converted.read_text()
                converted.unlink()

    def test_doctor_and_setup_report_through_the_command_line(self) -> None:
        ready = self.run_cli("doctor")
        assert "Ready:" in ready.stdout
        assert f"{PANDOC_VERSION} ({self.tools.pandoc})" in ready.stdout
        empty = {"MD_DOC_CONVERTER_HOME": str(self.root / "empty")}
        missing = self.run_cli("doctor", environment=empty, check=False)
        assert missing.returncode == 1
        assert "-> Run `md-doc-converter setup`" in missing.stdout

        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / PANDOC.file).write_bytes(b"not the pinned archive")
        result = self.run_cli(
            "setup", "--downloads", str(downloads), environment=empty, check=False
        )
        assert result.returncode == 1
        assert f"{PANDOC.file} has SHA-256" in result.stderr
        assert f"pandoc: using {downloads / PANDOC.file}" in result.stdout
        assert self.calls("npm") == []
        assert list((self.root / "empty" / "bin").iterdir()) == []


if __name__ == "__main__":
    unittest.main()
