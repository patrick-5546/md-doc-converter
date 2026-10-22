from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlsplit

import pytest
from typing_extensions import override

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import MarkdownError
from md_doc_converter.markdown.headings import HeadingError
from md_doc_converter.rendering import pipeline as render
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.environment import Toolchain
from tests.support.fake_tools import write_packages

FORMAT = render.OutputFormat
Options = render.RenderOptions


class RenderTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "cache.v2 spec.md"
        self.source.write_text("# Canonical Markdown\n")
        # Outside root, which the tests list; only the npm packages' stylesheets are read.
        toolchain = tempfile.TemporaryDirectory()
        self.addCleanup(toolchain.cleanup)
        self.tools = Toolchain(Path(toolchain.name))
        write_packages(self.tools)
        self.prepared: list[Path] = []
        self.katex_stylesheets: list[str] = []
        self.prepare_calls: list[tuple[Path, bool]] = []
        self.pandoc_calls: list[tuple[list[str], dict[str, object]]] = []
        self.styles: list[str] = []
        self.printer_commands: list[list[str]] = []
        self.git_work_tree = False
        problems = mock.patch.object(render, "render_problems", return_value=[])
        problems.start()
        self.addCleanup(problems.stop)
        prepare = mock.patch.object(render, "_prepare_markdown", side_effect=self.fake_prepare)
        prepare.start()
        self.addCleanup(prepare.stop)

    def fake_prepare(self, source: Path, *, respect_gitignore: bool) -> str:
        self.prepare_calls.append((source, respect_gitignore))
        return "# Prepared Markdown\n"

    def fake_run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            stdout = "true\n" if self.git_work_tree else ""
            return subprocess.CompletedProcess(command, 0 if stdout else 128, stdout, "")
        elif command[0] == "pandoc":
            self.prepared.append(Path(command[-1]))
            assert self.prepared[-1].read_text(encoding="utf-8") == "# Prepared Markdown\n"
            styles = Path(command[command.index("--include-in-header") + 1])
            self.styles.append(styles.read_text(encoding="utf-8"))
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            katex = Path(unquote(urlsplit(environment[render.KATEX_STYLESHEET_VARIABLE]).path))
            self.katex_stylesheets.append(katex.read_text(encoding="utf-8"))
            self.pandoc_calls.append((command, kwargs))
            Path(command[command.index("-o") + 1]).write_text(
                "<html>rendered</html>", encoding="utf-8"
            )
        elif command[:2] == ["node", str(render.PDF_PRINTER)]:
            self.printer_commands.append(command)
            assert Path(command[-1]).is_file()
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
        assert render.output_path(source, FORMAT.HTML) == self.root / "cache.v2 μ.html"
        assert render.output_path(source, FORMAT.PDF) == self.root / "cache.v2 μ.pdf"

    def test_folder_output_goes_inside_the_folder_and_keeps_its_suffix(self) -> None:
        source = self.root / "cache.v2"
        source.mkdir()
        assert render.output_path(source, FORMAT.PDF) == source / "cache.v2.pdf"
        assert (
            render.output_path(self.root / ".", FORMAT.HTML) == self.root / f"{self.root.name}.html"
        )

    def test_explicit_output_selects_directory_and_name(self) -> None:
        pdf = self.root / "reviews/selected.PDF"
        assert render.output_path(self.source, FORMAT.PDF, pdf) == pdf
        html = self.root / "reviews/selected.htm"
        assert render.output_path(self.source, FORMAT.HTML, html) == html

    def test_wrong_output_format_is_rejected(self) -> None:
        for output_format, output in ((FORMAT.PDF, "x.html"), (FORMAT.HTML, "x.pdf")):
            with self.subTest(output=output), pytest.raises(render.RenderError):
                render.output_path(self.source, output_format, self.root / output)

    def test_output_cannot_alias_a_source_file(self) -> None:
        alias = self.root / "alias.html"
        alias.hardlink_to(self.source)
        with pytest.raises(render.RenderError):
            render.validate_destinations((alias,), (self.source,))
        assert self.source.read_text() == "# Canonical Markdown\n"

    def test_output_symlinks_and_directories_are_rejected(self) -> None:
        alias = self.root / "alias.html"
        alias.symlink_to(self.source)
        for destination in (alias, self.root):
            with self.subTest(destination=destination), pytest.raises(render.RenderError):
                render.validate_destinations((destination,), (self.source,))

    def test_render_publishes_only_the_deliverable_and_keeps_scratch_private(self) -> None:
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            pdf = self.render_document(FORMAT.PDF)
            html = self.render_document(FORMAT.HTML)
        assert pdf == self.root / "cache.v2 spec.pdf"
        assert pdf.read_bytes() == b"PDF"
        assert html == self.root / "cache.v2 spec.html"
        assert html.read_text() == "<html>rendered</html>"
        assert sorted(path.name for path in self.root.iterdir()) == [
            "cache.v2 spec.html",
            "cache.v2 spec.md",
            "cache.v2 spec.pdf",
        ]
        assert self.source.read_text() == "# Canonical Markdown\n"
        assert self.prepared
        assert all(not path.exists() for path in self.prepared)
        for command, options in self.pandoc_calls:
            scratch = Path(str(options["cwd"]))
            assert not scratch.exists()
            assert Path(command[-1]).is_relative_to(scratch)
            environment = options["env"]
            assert isinstance(environment, dict)
            assert environment["PATH"].startswith(str(self.tools.node_bin) + os.pathsep)
            assert environment["PUPPETEER_CACHE_DIR"] == str(self.tools.puppeteer_cache)
            # No stylesheet is linked: Pandoc embeds KaTeX's, and the others are inlined.
            assert "--css" not in command
            katex = Path(unquote(urlsplit(environment[render.KATEX_STYLESHEET_VARIABLE]).path))
            assert katex.parent == scratch
        # The GitHub Markdown stylesheet comes first, so the shared styles override it.
        assert self.styles[0].startswith("<style>\n.markdown-body { color: #1f2328; }\n</style>")
        assert self.styles[0].index("#1f2328") < self.styles[0].index(".table-cols table")
        fonts = self.tools.node_modules / "katex/dist/fonts"
        assert self.katex_stylesheets[0] == (
            "@font-face{font-family:KaTeX_Main;"
            f'src:url({(fonts / "KaTeX_Main-Regular.woff2").as_uri()}) format("woff2")}}'
            ".katex{font:normal 1.21em KaTeX_Main}"
        )

    def test_missing_stylesheets_and_fonts_fail_before_rendering(self) -> None:
        for name in (
            "github-markdown-css/github-markdown-light.css",
            "katex/dist/katex.min.css",
            "katex/dist/fonts/KaTeX_Main-Regular.woff2",
        ):
            write_packages(self.tools)
            (self.tools.node_modules / name).unlink()
            with (
                self.subTest(name=name),
                mock.patch("subprocess.run", side_effect=self.fake_run) as run,
                pytest.raises(render.RenderError, match=r"setup --force"),
            ):
                self.render_document(FORMAT.HTML)
            run.assert_not_called()
            assert not self.prepare_calls

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
                assert render.pdf_timeout_seconds(value) == expected
        for value in ("-1", "1.5", "10s", "\u00b2", str(render.PDF_TIMEOUT_MAX + 1)):
            with (
                self.subTest(value=value),
                pytest.raises(render.RenderError, match="--pdf-timeout"),
            ):
                render.pdf_timeout_seconds(value)

    def test_pdf_printer_receives_node_modules_and_timeouts(self) -> None:
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            self.render_document(FORMAT.PDF)
            self.render_document(FORMAT.PDF, options=Options(pdf_timeout="0"))
        assert [
            command[command.index("--timeout-seconds") + 1] for command in self.printer_commands
        ] == [str(render.PDF_TIMEOUT_DEFAULT), "0"]
        for command in self.printer_commands:
            assert command[command.index("--node-modules") + 1] == str(self.tools.node_modules)

    def test_invalid_pdf_timeout_fails_before_rendering_and_html_ignores_it(self) -> None:
        run = mock.Mock(side_effect=self.fake_run)
        with mock.patch("subprocess.run", run):
            with pytest.raises(render.RenderError, match="MD_DOC_CONVERTER_PDF_TIMEOUT"):
                self.render_document(FORMAT.PDF, options=Options(pdf_timeout="ten"))
            run.assert_not_called()
            assert not self.prepare_calls
            html = self.render_document(FORMAT.HTML, options=Options(pdf_timeout="ten"))
        assert html.is_file()
        assert not self.printer_commands

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
            assert render.main([source], action="pdf") == 0
            assert (
                render.main(
                    [source, "--header", "Flag header", "--footer", "", "--pdf-timeout", "30"],
                    action="pdf",
                )
                == 0
            )
            assert render.main([source, "--pdf-timeout", "-5"], action="pdf") == 1
        timeouts = [
            command[command.index("--timeout-seconds") + 1] for command in self.printer_commands
        ]
        assert timeouts == ["45", "30"]
        assert 'content: "Header from environment"' in self.styles[0]
        assert 'content: "Footer from environment"' in self.styles[0]
        assert 'content: "Flag header"' in self.styles[1]
        assert "Footer from environment" not in self.styles[1]
        assert f"-> {self.root / 'cache.v2 spec.pdf'}" in messages.getvalue()
        assert "--pdf-timeout" in errors.getvalue()

    def test_extra_stylesheet_is_inlined_last_and_protected(self) -> None:
        extra = self.root / "extra.html"
        extra.write_text(".markdown-body { color: red; }\n")
        with mock.patch("subprocess.run", side_effect=self.fake_run):
            self.render_document(FORMAT.HTML, options=Options(extra_css=extra, header="Header"))
            with pytest.raises(render.RenderError, match="would replace an input"):
                self.render_document(FORMAT.HTML, output=extra, options=Options(extra_css=extra))
        assert self.styles[0].index("color: red") > self.styles[0].index('"Header"')
        assert extra.read_text() == ".markdown-body { color: red; }\n"
        extra.write_text("</STYLE><script>alert(1)</script>")
        with pytest.raises(render.RenderError, match="must not contain"):
            self.render_document(FORMAT.HTML, options=Options(extra_css=extra))

    def test_conversion_failure_leaves_previous_deliverable_and_cleans_scratch(self) -> None:
        destination = self.root / "cache.v2 spec.pdf"
        destination.write_text("previous")

        def fail_pandoc(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "pandoc":
                self.prepared.append(Path(command[-1]))
                raise subprocess.CalledProcessError(7, command)
            return self.fake_run(command, **kwargs)

        with (
            mock.patch("subprocess.run", side_effect=fail_pandoc),
            pytest.raises(subprocess.CalledProcessError),
        ):
            self.render_document(FORMAT.PDF)
        assert destination.read_text() == "previous"
        assert sorted(path.name for path in self.root.iterdir()) == [
            "cache.v2 spec.md",
            "cache.v2 spec.pdf",
        ]
        assert all(not path.exists() for path in self.prepared)

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
            pytest.raises(render.RenderError, match="any previous file is unchanged"),
        ):
            render.publish(staged, destination)
        assert destination.read_text() == "previous"
        assert {path.name for path in self.root.iterdir()} == {
            "cache.v2 spec.md",
            "doc.pdf",
            "staged.pdf",
        }

    def test_publication_keeps_the_rendered_file_mode(self) -> None:
        staged = self.root / "staged.pdf"
        staged.write_text("new")
        staged.chmod(0o640)
        destination = self.root / "new folder" / "doc.pdf"
        render.publish(staged, destination)
        assert destination.read_text() == "new"
        assert destination.stat().st_mode & 511 == 416

    def test_intermediates_are_kept_on_request_even_when_rendering_fails(self) -> None:
        kept = self.root / "debug"
        with (
            mock.patch("subprocess.run", side_effect=self.fake_run),
            contextlib.redirect_stderr(io.StringIO()) as messages,
        ):
            self.render_document(FORMAT.PDF, options=Options(keep_intermediates=kept))
        assert sorted(
            str(path.relative_to(kept)) for path in kept.rglob("*") if path.is_file()
        ) == [
            "katex.css",
            "markdown/cache.v2 spec.md",
            "page-styles.html",
            "render/cache.v2 spec.html",
            "render/cache.v2 spec.pdf",
        ]
        assert str(kept) in messages.getvalue()

        failed = self.root / "failed"

        def fail_pandoc(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "pandoc":
                raise subprocess.CalledProcessError(7, command)
            return self.fake_run(command, **kwargs)

        with (
            mock.patch("subprocess.run", side_effect=fail_pandoc),
            contextlib.redirect_stderr(io.StringIO()),
            pytest.raises(subprocess.CalledProcessError),
        ):
            self.render_document(FORMAT.HTML, options=Options(keep_intermediates=failed))
        assert (failed / "markdown/cache.v2 spec.md").is_file()
        assert (failed / "page-styles.html").is_file()

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
                pytest.raises(render.RenderError, match=message),
            ):
                self.render_document(
                    FORMAT.HTML, source=source, options=Options(keep_intermediates=kept)
                )
        assert (nonempty / "keep.txt").read_text() == "keep"
        assert not (folder / "debug").exists()

    def test_missing_prerequisites_fail_before_any_rendering(self) -> None:
        run = mock.Mock(side_effect=self.fake_run)
        problem = "pandoc: not found on PATH. Install Pandoc"
        with (
            mock.patch.object(render, "render_problems", return_value=[problem]),
            mock.patch("subprocess.run", run),
            pytest.raises(render.RenderError, match=r"(?s)prerequisites are missing.*doctor"),
        ):
            self.render_document(FORMAT.PDF)
        run.assert_not_called()
        assert not self.prepare_calls
        assert not (self.root / "cache.v2 spec.pdf").exists()

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
                assert self.prepare_calls[-1] == (source, expected)


@pytest.mark.parametrize(
    "error",
    [MarkdownError("malformed Link"), HeadingError("malformed Header block"), ValueError("bad")],
    ids=["markdown", "heading", "value"],
)
def test_preprocessing_errors_become_render_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(source: Path, *, respect_gitignore: bool) -> str:
        raise error

    monkeypatch.setattr(preprocess, "prepare_markdown", fail)
    with pytest.raises(render.RenderError, match=re.escape(f"cannot prepare {tmp_path}: {error}")):
        render._prepare_markdown(tmp_path, respect_gitignore=False)


def test_markdown_that_is_not_utf8_names_the_file(tmp_path: Path) -> None:
    source = tmp_path / "latin-1.md"
    source.write_bytes(b"# Caf\xe9\n")
    with pytest.raises(render.RenderError, match=r"latin-1\.md: 'utf-8' codec can't decode"):
        render._prepare_markdown(source, respect_gitignore=False)


def test_render_command_reports_preprocessing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_problems(tools: Toolchain, *, pdf: bool) -> list[str]:
        return []

    def no_stylesheet(tools: Toolchain) -> str:
        return ""

    monkeypatch.setattr(render, "render_problems", no_problems)
    monkeypatch.setattr(render, "github_stylesheet", no_stylesheet)
    monkeypatch.setattr(render, "katex_stylesheet", no_stylesheet)
    source = tmp_path / "latin-1.md"
    source.write_bytes(b"# Caf\xe9\n")
    assert render.main([str(source)], action="html") == 1
    error = capsys.readouterr().err
    assert f"md-doc-converter: cannot prepare {source}: 'utf-8' codec can't decode" in error
    assert not (tmp_path / "latin-1.html").exists()


def test_katex_stylesheet_without_woff2_fonts_is_rejected(tmp_path: Path) -> None:
    tools = Toolchain(tmp_path)
    write_packages(tools)
    stylesheet = tools.node_modules / render.KATEX_DIST / "katex.min.css"
    stylesheet.write_text(
        "@font-face{font-family:KaTeX_Main;"
        'src:url(fonts/KaTeX_Main-Regular.ttf) format("truetype")}',
        encoding="utf-8",
    )
    with pytest.raises(render.RenderError, match=r"no fonts in its stylesheet.*setup --force"):
        render.katex_stylesheet(tools)


class TableCodePipeFilterTest(unittest.TestCase):
    def test_an_escaped_pipe_in_table_code_renders_as_gfm_shows_it(self) -> None:
        defaults = asset_path("pandoc-defaults.yaml").read_text(encoding="utf-8")
        reader = re.search(r"^from: (\S+)$", defaults, re.MULTILINE)
        assert reader is not None
        assert "table-code-pipes.lua" in defaults
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
        assert "<code>a|b</code>" in result.stdout
        assert "<code>c\\|d</code>" in result.stdout


if __name__ == "__main__":
    unittest.main()
