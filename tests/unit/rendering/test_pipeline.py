from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typing_extensions import override

from md_doc_converter.rendering import pipeline as render
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.environment import Toolchain

FORMAT = render.OutputFormat
Options = render.RenderOptions
PREPROCESS = [sys.executable, "-m", "md_doc_converter.markdown.preprocess"]


class RenderTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "cache.v2 spec.md"
        self.source.write_text("# Canonical Markdown\n")
        self.tools = Toolchain(self.root / "toolchain")
        self.prepared: list[Path] = []
        self.preprocess_commands: list[list[str]] = []
        self.pandoc_calls: list[tuple[list[str], dict[str, object]]] = []
        self.styles: list[str] = []
        self.printer_commands: list[list[str]] = []
        self.git_work_tree = False
        problems = mock.patch.object(render, "render_problems", return_value=[])
        problems.start()
        self.addCleanup(problems.stop)

    def fake_run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:3] == PREPROCESS:
            self.preprocess_commands.append(command)
            output = Path(command[command.index("--output") + 1])
            self.prepared.append(output)
            output.write_text("# Prepared Markdown\n")
        elif command[0] == "git":
            stdout = "true\n" if self.git_work_tree else ""
            return subprocess.CompletedProcess(command, 0 if stdout else 128, stdout, "")
        elif command[0] == "pandoc":
            self.assertTrue(Path(command[-1]).is_file())
            styles = Path(command[command.index("--include-in-header") + 1])
            self.styles.append(styles.read_text(encoding="utf-8"))
            self.pandoc_calls.append((command, kwargs))
            Path(command[command.index("-o") + 1]).write_text("<html>rendered</html>")
        elif command[:2] == ["node", str(render.PDF_PRINTER)]:
            self.printer_commands.append(command)
            self.assertTrue(Path(command[-1]).is_file())
            Path(command[command.index("-o") + 1]).write_bytes(b"PDF")
        else:
            self.fail(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def render_document(
        self,
        output_format: render.OutputFormat = FORMAT.PDF,
        output: Path | None = None,
        source: Path | None = None,
        options: render.RenderOptions | None = None,
    ) -> Path:
        return render.render_markdown(
            source or self.source, output_format, output, options, tools=self.tools
        )

    def test_input_name_preserves_dots_spaces_and_unicode(self) -> None:
        source = self.root / "cache.v2 \u03bc.md"
        source.touch()
        self.assertEqual(
            render.output_path(source, FORMAT.HTML), self.root / "cache.v2 \u03bc.html"
        )
        self.assertEqual(render.output_path(source, FORMAT.PDF), self.root / "cache.v2 \u03bc.pdf")

    def test_folder_output_goes_inside_the_folder_and_keeps_its_suffix(self) -> None:
        source = self.root / "cache.v2"
        source.mkdir()
        self.assertEqual(render.output_path(source, FORMAT.PDF), source / "cache.v2.pdf")
        self.assertEqual(
            render.output_path(self.root / ".", FORMAT.HTML),
            self.root / f"{self.root.name}.html",
        )

    def test_explicit_output_selects_directory_and_name(self) -> None:
        pdf = self.root / "reviews/selected.PDF"
        self.assertEqual(render.output_path(self.source, FORMAT.PDF, pdf), pdf)
        html = self.root / "reviews/selected.htm"
        self.assertEqual(render.output_path(self.source, FORMAT.HTML, html), html)

    def test_wrong_output_format_is_rejected(self) -> None:
        for output_format, output in ((FORMAT.PDF, "x.html"), (FORMAT.HTML, "x.pdf")):
            with self.subTest(output=output), self.assertRaises(render.RenderError):
                render.output_path(self.source, output_format, self.root / output)

    def test_output_cannot_alias_a_source_file(self) -> None:
        alias = self.root / "alias.html"
        alias.hardlink_to(self.source)
        with self.assertRaises(render.RenderError):
            render.validate_destinations((alias,), (self.source,))
        self.assertEqual(self.source.read_text(), "# Canonical Markdown\n")

    def test_output_symlinks_and_directories_are_rejected(self) -> None:
        alias = self.root / "alias.html"
        alias.symlink_to(self.source)
        for destination in (alias, self.root):
            with self.subTest(destination=destination), self.assertRaises(render.RenderError):
                render.validate_destinations((destination,), (self.source,))

    def test_render_publishes_only_the_deliverable_and_keeps_scratch_private(self) -> None:
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            pdf = self.render_document(FORMAT.PDF)
            html = self.render_document(FORMAT.HTML)
        self.assertEqual(pdf, self.root / "cache.v2 spec.pdf")
        self.assertEqual(pdf.read_bytes(), b"PDF")
        self.assertEqual(html, self.root / "cache.v2 spec.html")
        self.assertEqual(html.read_text(), "<html>rendered</html>")
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ["cache.v2 spec.html", "cache.v2 spec.md", "cache.v2 spec.pdf"],
        )
        self.assertEqual(self.source.read_text(), "# Canonical Markdown\n")
        self.assertTrue(self.prepared)
        self.assertTrue(all(not path.exists() for path in self.prepared))
        for command, options in self.pandoc_calls:
            scratch = Path(str(options["cwd"]))
            self.assertFalse(scratch.exists())
            self.assertTrue(Path(command[-1]).is_relative_to(scratch))
            environment = options["env"]
            assert isinstance(environment, dict)
            self.assertTrue(environment["PATH"].startswith(str(self.tools.node_bin) + os.pathsep))
            self.assertEqual(environment["PUPPETEER_CACHE_DIR"], str(self.tools.puppeteer_cache))
            self.assertNotIn("--css", command)

    def test_pdf_timeout_accepts_whole_seconds_and_defaults_when_blank(self) -> None:
        for value, expected in (
            (None, render.PDF_TIMEOUT_DEFAULT),
            ("", render.PDF_TIMEOUT_DEFAULT),
            ("  ", render.PDF_TIMEOUT_DEFAULT),
            ("0", 0),
            (" 1200 ", 1200),
            (str(render.PDF_TIMEOUT_MAX), render.PDF_TIMEOUT_MAX),
        ):
            with self.subTest(value=value):
                self.assertEqual(render.pdf_timeout_seconds(value), expected)
        for value in ("-1", "1.5", "10s", "\u00b2", str(render.PDF_TIMEOUT_MAX + 1)):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(render.RenderError, "--pdf-timeout"),
            ):
                render.pdf_timeout_seconds(value)

    def test_pdf_printer_receives_node_modules_and_timeouts(self) -> None:
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            self.render_document(FORMAT.PDF)
            self.render_document(FORMAT.PDF, options=Options(pdf_timeout="0"))
        self.assertEqual(
            [command[command.index("--timeout-seconds") + 1] for command in self.printer_commands],
            [str(render.PDF_TIMEOUT_DEFAULT), "0"],
        )
        for command in self.printer_commands:
            self.assertEqual(
                command[command.index("--node-modules") + 1], str(self.tools.node_modules)
            )

    def test_invalid_pdf_timeout_fails_before_rendering_and_html_ignores_it(self) -> None:
        run = mock.Mock(side_effect=self.fake_run)
        with mock.patch("subprocess.run", run):
            with self.assertRaisesRegex(render.RenderError, "MD_DOC_CONVERTER_PDF_TIMEOUT"):
                self.render_document(FORMAT.PDF, options=Options(pdf_timeout="ten"))
            run.assert_not_called()
            html = self.render_document(FORMAT.HTML, options=Options(pdf_timeout="ten"))
        self.assertTrue(html.is_file())
        self.assertFalse(self.printer_commands)

    def test_command_line_flags_override_environment_defaults(self) -> None:
        source = str(self.source)
        environment = {
            "MD_DOC_CONVERTER_HEADER": "Header from environment",
            "MD_DOC_CONVERTER_FOOTER": "Footer from environment",
            "MD_DOC_CONVERTER_PDF_TIMEOUT": "45",
        }
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(render, "toolchain", return_value=self.tools),
            mock.patch("subprocess.run", side_effect=self.fake_run),
            contextlib.redirect_stdout(io.StringIO()) as messages,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(render.main([source], action="pdf"), 0)
            self.assertEqual(
                render.main(
                    [source, "--header", "Flag header", "--footer", "", "--pdf-timeout", "30"],
                    action="pdf",
                ),
                0,
            )
            self.assertEqual(render.main([source, "--pdf-timeout", "-5"], action="pdf"), 1)
        timeouts = [
            command[command.index("--timeout-seconds") + 1] for command in self.printer_commands
        ]
        self.assertEqual(timeouts, ["45", "30"])
        self.assertIn('content: "Header from environment"', self.styles[0])
        self.assertIn('content: "Footer from environment"', self.styles[0])
        self.assertIn('content: "Flag header"', self.styles[1])
        self.assertNotIn("Footer from environment", self.styles[1])
        self.assertIn(f"-> {self.root / 'cache.v2 spec.pdf'}", messages.getvalue())
        self.assertIn("--pdf-timeout", errors.getvalue())

    def test_extra_stylesheet_is_inlined_last_and_protected(self) -> None:
        extra = self.root / "extra.html"
        extra.write_text(".markdown-body { color: red; }\n")
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            self.render_document(FORMAT.HTML, options=Options(extra_css=extra, header="Header"))
            with self.assertRaisesRegex(render.RenderError, "would replace an input"):
                self.render_document(FORMAT.HTML, output=extra, options=Options(extra_css=extra))
        self.assertGreater(self.styles[0].index("color: red"), self.styles[0].index('"Header"'))
        self.assertEqual(extra.read_text(), ".markdown-body { color: red; }\n")
        extra.write_text("</STYLE><script>alert(1)</script>")
        with self.assertRaisesRegex(render.RenderError, "must not contain"):
            self.render_document(FORMAT.HTML, options=Options(extra_css=extra))

    def test_conversion_failure_leaves_previous_deliverable_and_cleans_scratch(self) -> None:
        destination = self.root / "cache.v2 spec.pdf"
        destination.write_text("previous")

        def fail_pandoc(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "pandoc":
                raise subprocess.CalledProcessError(7, command)
            return self.fake_run(command, **kwargs)

        with (
            mock.patch("subprocess.run", side_effect=fail_pandoc),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.render_document(FORMAT.PDF)
        self.assertEqual(destination.read_text(), "previous")
        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            ["cache.v2 spec.md", "cache.v2 spec.pdf"],
        )
        self.assertTrue(all(not path.exists() for path in self.prepared))

    def test_publication_failure_leaves_the_previous_file(self) -> None:
        destination = self.root / "doc.pdf"
        destination.write_text("previous")
        staged = self.root / "staged.pdf"
        staged.write_text("new")
        original_replace = Path.replace

        def fail_partial(source: Path, target: Path) -> Path:
            if source.name.endswith(".partial"):
                raise OSError("injected publication failure")
            return original_replace(source, target)

        with (
            mock.patch.object(Path, "replace", autospec=True, side_effect=fail_partial),
            self.assertRaisesRegex(render.RenderError, "any previous file is unchanged"),
        ):
            render.publish(staged, destination)
        self.assertEqual(destination.read_text(), "previous")
        self.assertEqual(
            {path.name for path in self.root.iterdir()},
            {"cache.v2 spec.md", "doc.pdf", "staged.pdf"},
        )

    def test_publication_keeps_the_rendered_file_mode(self) -> None:
        staged = self.root / "staged.pdf"
        staged.write_text("new")
        staged.chmod(0o640)
        destination = self.root / "new folder" / "doc.pdf"
        render.publish(staged, destination)
        self.assertEqual(destination.read_text(), "new")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o640)

    def test_intermediates_are_kept_on_request_even_when_rendering_fails(self) -> None:
        kept = self.root / "debug"
        with (
            mock.patch("subprocess.run", side_effect=self.fake_run),
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            self.render_document(FORMAT.PDF, options=Options(keep_intermediates=kept))
        self.assertEqual(
            sorted(str(path.relative_to(kept)) for path in kept.rglob("*") if path.is_file()),
            [
                "markdown/cache.v2 spec.md",
                "page-styles.html",
                "render/cache.v2 spec.html",
                "render/cache.v2 spec.pdf",
            ],
        )
        self.assertIn(str(kept), messages.getvalue())

        failed = self.root / "failed"

        def fail_pandoc(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "pandoc":
                raise subprocess.CalledProcessError(7, command)
            return self.fake_run(command, **kwargs)

        with (
            mock.patch("subprocess.run", side_effect=fail_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.render_document(FORMAT.HTML, options=Options(keep_intermediates=failed))
        self.assertTrue((failed / "markdown/cache.v2 spec.md").is_file())
        self.assertTrue((failed / "page-styles.html").is_file())

    def test_intermediate_directory_must_be_new_or_empty_and_separate(self) -> None:
        nonempty = self.root / "nonempty"
        nonempty.mkdir()
        (nonempty / "keep.txt").write_text("keep")
        folder = self.root / "docs"
        folder.mkdir()
        (folder / "page.md").write_text("# Page\n")
        cases = (
            (self.source, nonempty, "new or empty"),
            (self.source, self.source, "new or empty"),
            (folder, folder / "debug", "overlaps"),
            (self.source, self.root, "new or empty"),
        )
        for source, kept, message in cases:
            with (
                self.subTest(kept=kept),
                mock.patch("subprocess.run", side_effect=self.fake_run),
                self.assertRaisesRegex(render.RenderError, message),
            ):
                self.render_document(
                    FORMAT.HTML, source=source, options=Options(keep_intermediates=kept)
                )
        self.assertEqual((nonempty / "keep.txt").read_text(), "keep")
        self.assertFalse((folder / "debug").exists())

    def test_missing_prerequisites_fail_before_any_rendering(self) -> None:
        run = mock.Mock(side_effect=self.fake_run)
        problem = "pandoc: not found on PATH. Install Pandoc"
        with (
            mock.patch.object(render, "render_problems", return_value=[problem]),
            mock.patch("subprocess.run", run),
            self.assertRaisesRegex(render.RenderError, "(?s)prerequisites are missing.*doctor"),
        ):
            self.render_document(FORMAT.PDF)
        run.assert_not_called()
        self.assertFalse((self.root / "cache.v2 spec.pdf").exists())

    def test_gitignore_filtering_defaults_to_folders_in_git_work_trees(self) -> None:
        folder = self.root / "docs"
        folder.mkdir()
        (folder / "page.md").write_text("# Page\n")
        cases = (
            (folder, None, True, True),
            (folder, None, False, False),
            (folder, False, True, False),
            (folder, True, False, True),
            (self.source, None, True, False),
        )
        for source, choice, work_tree, expected in cases:
            self.git_work_tree = work_tree
            with (
                self.subTest(source=source.name, choice=choice, work_tree=work_tree),
                mock.patch("subprocess.run", side_effect=self.fake_run),
            ):
                self.render_document(
                    FORMAT.HTML, source=source, options=Options(respect_gitignore=choice)
                )
                self.assertEqual("--respect-gitignore" in self.preprocess_commands[-1], expected)


class TableCodePipeFilterTest(unittest.TestCase):
    def test_an_escaped_pipe_in_table_code_renders_as_gfm_shows_it(self) -> None:
        defaults = asset_path("pandoc-defaults.yaml").read_text(encoding="utf-8")
        reader = re.search(r"^from: (\S+)$", defaults, re.MULTILINE)
        assert reader is not None
        self.assertIn("table-code-pipes.lua", defaults)
        result = subprocess.run(
            [
                "pandoc",
                f"--from={reader.group(1)}",
                "--to=html",
                f"--lua-filter={asset_path('table-code-pipes.lua')}",
            ],
            input="| A | B |\n| --- | --- |\n| `a\\|b` | x |\n\nOutside `c\\|d`.\n",
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("<code>a|b</code>", result.stdout)
        self.assertIn("<code>c\\|d</code>", result.stdout)


if __name__ == "__main__":
    unittest.main()
