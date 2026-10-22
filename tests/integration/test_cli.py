from __future__ import annotations

import os
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
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self, tool: str) -> list[dict[str, object]]:
        return [call for call in read_log(self.log) if call["tool"] == tool]

    def test_version_and_help(self) -> None:
        self.assertRegex(self.run_cli("--version").stdout, r"^md-doc-converter \d+\.\d+\.\d+")
        self.assertIn("md-to-pdf", self.run_cli().stdout)
        result = self.run_cli("not-a-command", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_render_defaults_next_to_files_and_inside_folders(self) -> None:
        result = self.run_cli("md-to-html", "docs/input.md")
        html = self.docs / "input.html"
        self.assertIn(f"-> {html}", result.stdout)
        self.assertIn("# Example", html.read_text())
        self.run_cli("md-to-pdf", "docs")
        self.assertEqual((self.docs / "docs.pdf").read_bytes(), b"%PDF-fake")
        (self.docs / "docs.pdf").unlink()
        self.run_cli("md-to-pdf", ".", cwd=self.docs)
        self.assertEqual((self.docs / "docs.pdf").read_bytes(), b"%PDF-fake")
        self.assertEqual(
            sorted(path.name for path in self.docs.iterdir()),
            ["docs.pdf", "input.html", "input.md"],
        )
        self.assertEqual(sorted(path.name for path in self.workspace.iterdir()), ["docs"])
        for call in self.calls("pandoc") + self.calls("node"):
            scratch = Path(str(call["cwd"]))
            self.assertFalse(scratch.exists())
            self.assertFalse(scratch.is_relative_to(self.workspace))
        printer = self.calls("node")[0]["arguments"]
        assert isinstance(printer, list)
        self.assertEqual(printer[printer.index("--node-modules") + 1], str(self.tools.node_modules))
        environment = self.calls("pandoc")[0]["environment"]
        assert isinstance(environment, dict)
        self.assertEqual(environment["PUPPETEER_CACHE_DIR"], str(self.tools.puppeteer_cache))
        self.assertTrue(str(environment["PATH"]).startswith(str(self.tools.node_bin)))
        arguments = self.calls("pandoc")[0]["arguments"]
        assert isinstance(arguments, list)
        self.assertEqual(
            arguments[arguments.index("--css") + 1],
            f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css",
        )
        self.assertEqual(environment["MD_DOC_CONVERTER_NODE_MODULES"], str(self.tools.node_modules))
        # The fake PATH also has a pandoc; the toolchain's pinned one comes first.
        self.assertEqual({call["path"] for call in self.calls("pandoc")}, {str(self.tools.pandoc)})

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
                self.assertEqual(expected.read_bytes(), b"%PDF-fake")
        self.run_cli("md-to-html", str(source))
        self.assertTrue(source.with_suffix(".html").is_file())

    def test_wrong_output_suffix_is_rejected_before_rendering(self) -> None:
        result = self.run_cli("md-to-pdf", "docs/input.md", "-o", "out.html", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Output must end in .pdf", result.stderr)
        self.assertEqual(read_log(self.log), [])
        self.assertFalse((self.workspace / "out.html").exists())

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
        self.assertIn('content: "Environment header"', first)
        self.assertIn('content: "Environment footer"', first)
        self.assertIn(f"content: {css_string(header)}", second)
        self.assertNotIn("Environment", second)
        self.assertEqual(second.count("</style>"), 1)
        self.assertIn("Review", (self.docs / "input.html").read_text())

    def test_pdf_timeout_flag_environment_and_default(self) -> None:
        def timeout(*arguments: str, environment: dict[str, str] | None = None) -> str:
            self.run_cli("md-to-pdf", "docs/input.md", *arguments, environment=environment)
            command = self.calls("node")[-1]["arguments"]
            assert isinstance(command, list)
            return str(command[command.index("--timeout-seconds") + 1])

        self.assertEqual(timeout(), "600")
        self.assertEqual(timeout(environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "0"}), "0")
        self.assertEqual(
            timeout("--pdf-timeout", "30", environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "0"}),
            "30",
        )
        result = self.run_cli(
            "md-to-pdf",
            "docs/input.md",
            environment={"MD_DOC_CONVERTER_PDF_TIMEOUT": "soon"},
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "--pdf-timeout (MD_DOC_CONVERTER_PDF_TIMEOUT) must be whole seconds", result.stderr
        )

    def test_missing_toolchain_fails_without_rendering(self) -> None:
        result = self.run_cli(
            "md-to-pdf",
            "docs/input.md",
            environment={"MD_DOC_CONVERTER_HOME": str(self.root / "not installed")},
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("rendering prerequisites are missing", result.stderr)
        self.assertIn("md-doc-converter setup", result.stderr)
        self.assertIn("md-doc-converter doctor", result.stderr)
        self.assertEqual(read_log(self.log), [])
        self.assertFalse((self.docs / "input.pdf").exists())

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
        self.assertEqual(result.returncode, 1)
        self.assertIn("node failed with exit status 3", result.stderr)
        self.assertEqual(previous.read_bytes(), b"previous")
        self.assertEqual(
            sorted(path.name for path in self.docs.iterdir()), ["input.md", "input.pdf"]
        )
        self.assertEqual((kept / "markdown" / "input.md").read_text(), "# Example\n")
        self.assertTrue((kept / "page-styles.html").is_file())
        self.assertTrue((kept / "render" / "input.html").is_file())

    def test_gitignore_filtering_is_automatic_inside_git_work_trees(self) -> None:
        (self.docs / "ignored.md").write_text("# Ignored\n")
        (self.workspace / ".gitignore").write_text("ignored.md\n")
        subprocess.run(["git", "init", "--quiet"], cwd=self.workspace, check=True)
        self.run_cli("md-to-html", "docs")
        self.assertNotIn("Ignored", str(self.calls("pandoc")[-1]["input"]))
        self.run_cli("md-to-html", "docs", "--no-respect-gitignore")
        self.assertIn("Ignored", str(self.calls("pandoc")[-1]["input"]))

        outside = self.root / "outside"
        outside.mkdir()
        (outside / "page.md").write_text("# Page\n")
        quiet = self.run_cli("md-to-html", str(outside))
        self.assertNotIn("WARNING", quiet.stderr)
        explicit = self.run_cli("md-to-html", str(outside), "--respect-gitignore")
        self.assertIn("--respect-gitignore had no effect", explicit.stderr)

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
                self.assertIn("| signal | one |", converted.read_text())
                self.assertIn("md-doc-converter (csv-to-md)", converted.read_text())
                converted.unlink()

    def test_doctor_and_setup_report_through_the_command_line(self) -> None:
        ready = self.run_cli("doctor")
        self.assertIn("Ready:", ready.stdout)
        self.assertIn(f"{PANDOC_VERSION} ({self.tools.pandoc})", ready.stdout)
        empty = {"MD_DOC_CONVERTER_HOME": str(self.root / "empty")}
        missing = self.run_cli("doctor", environment=empty, check=False)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("-> Run `md-doc-converter setup`", missing.stdout)

        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / PANDOC.file).write_bytes(b"not the pinned archive")
        result = self.run_cli(
            "setup", "--downloads", str(downloads), environment=empty, check=False
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{PANDOC.file} has SHA-256", result.stderr)
        self.assertIn(f"pandoc: using {downloads / PANDOC.file}", result.stdout)
        self.assertEqual(self.calls("npm"), [])
        self.assertEqual(list((self.root / "empty" / "bin").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
