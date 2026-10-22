"""Rendering Markdown to one HTML or PDF file: names, publication, styles and options.

Most tests render with `fake`, which stands in for preparing the Markdown and for running git,
pandoc and the PDF printer, and with `tools`, a toolchain with only the npm packages' files.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlsplit

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import MarkdownError
from md_doc_converter.markdown.headings import HeadingError
from md_doc_converter.rendering import pipeline as render
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.environment import Toolchain
from tests.support.fake_tools import write_packages

FORMAT = render.OutputFormat
Options = render.RenderOptions


@dataclass
class FakeRenderer:
    """Prepares Markdown, and runs git, pandoc and the PDF printer, recording what they get.

    git reports whether the input is in a work tree. pandoc writes placeholder HTML, or fails with
    status 7; the PDF printer writes a placeholder PDF. Any other command fails the test.
    """

    git_work_tree: bool = False
    pandoc_fails: bool = False
    prepare_calls: list[tuple[Path, bool]] = field(default_factory=list)
    prepared: list[Path] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    katex_stylesheets: list[str] = field(default_factory=list)
    pandoc_calls: list[tuple[list[str], dict[str, object]]] = field(default_factory=list)
    printer_commands: list[list[str]] = field(default_factory=list)

    def prepare(self, source: Path, *, respect_gitignore: bool) -> str:
        self.prepare_calls.append((source, respect_gitignore))
        return "# Prepared Markdown\n"

    def run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            stdout = "true\n" if self.git_work_tree else ""
            return subprocess.CompletedProcess(command, 0 if stdout else 128, stdout, "")
        elif command[0] == "pandoc":
            self.prepared.append(Path(command[-1]))
            if self.pandoc_fails:
                raise subprocess.CalledProcessError(7, command)
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
            pytest.fail(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A Markdown file, alone in tmp_path, which some tests list after rendering."""
    path = tmp_path / "cache.v2 spec.md"
    path.write_text("# Canonical Markdown\n", encoding="utf-8")
    return path


@pytest.fixture
def tools(tmp_path_factory: pytest.TempPathFactory) -> Toolchain:
    """A toolchain outside tmp_path, with only the npm packages' files that rendering reads."""
    toolchain = Toolchain(tmp_path_factory.mktemp("toolchain"))
    write_packages(toolchain)
    return toolchain


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRenderer:
    """No missing prerequisites, and a FakeRenderer for preparation and for subprocess.run."""
    renderer = FakeRenderer()

    def no_problems(tools: Toolchain, *, pdf: bool) -> list[str]:
        return []

    monkeypatch.setattr(render, "render_problems", no_problems)
    monkeypatch.setattr(render, "_prepare_markdown", renderer.prepare)
    monkeypatch.setattr(subprocess, "run", renderer.run)
    return renderer


@pytest.fixture
def run(fake: FakeRenderer, monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """The fake's subprocess.run as a Mock, for tests that check that nothing ran."""
    recorder = mock.Mock(side_effect=fake.run)
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


def test_input_name_preserves_dots_spaces_and_unicode(tmp_path: Path) -> None:
    source = tmp_path / "cache.v2 \u03bc.md"
    source.touch()
    assert render.output_path(source, FORMAT.HTML) == tmp_path / "cache.v2 μ.html"
    assert render.output_path(source, FORMAT.PDF) == tmp_path / "cache.v2 μ.pdf"


def test_folder_output_goes_inside_the_folder_and_keeps_its_suffix(tmp_path: Path) -> None:
    source = tmp_path / "cache.v2"
    source.mkdir()
    assert render.output_path(source, FORMAT.PDF) == source / "cache.v2.pdf"
    assert render.output_path(tmp_path / ".", FORMAT.HTML) == tmp_path / f"{tmp_path.name}.html"


def test_explicit_output_selects_directory_and_name(tmp_path: Path, source: Path) -> None:
    pdf = tmp_path / "reviews/selected.PDF"
    assert render.output_path(source, FORMAT.PDF, pdf) == pdf
    html = tmp_path / "reviews/selected.htm"
    assert render.output_path(source, FORMAT.HTML, html) == html


@pytest.mark.parametrize(
    ("output_format", "output"),
    [
        pytest.param(FORMAT.PDF, "x.html", id="pdf-named-html"),
        pytest.param(FORMAT.HTML, "x.pdf", id="html-named-pdf"),
    ],
)
def test_wrong_output_format_is_rejected(
    output_format: render.OutputFormat, output: str, tmp_path: Path, source: Path
) -> None:
    with pytest.raises(render.RenderError):
        render.output_path(source, output_format, tmp_path / output)


def test_output_cannot_alias_a_source_file(tmp_path: Path, source: Path) -> None:
    alias = tmp_path / "alias.html"
    alias.hardlink_to(source)
    with pytest.raises(render.RenderError):
        render.validate_destinations((alias,), (source,))
    assert source.read_text(encoding="utf-8") == "# Canonical Markdown\n"


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_output_symlinks_and_directories_are_rejected(
    kind: str, tmp_path: Path, source: Path
) -> None:
    alias = tmp_path / "alias.html"
    alias.symlink_to(source)
    destination = alias if kind == "symlink" else tmp_path
    with pytest.raises(render.RenderError):
        render.validate_destinations((destination,), (source,))


def test_render_publishes_only_the_deliverable_and_keeps_scratch_private(
    fake: FakeRenderer, tmp_path: Path, source: Path, tools: Toolchain
) -> None:
    pdf = render.render_markdown(source, FORMAT.PDF, tools=tools)
    html = render.render_markdown(source, FORMAT.HTML, tools=tools)
    assert pdf == tmp_path / "cache.v2 spec.pdf"
    assert pdf.read_bytes() == b"PDF"
    assert html == tmp_path / "cache.v2 spec.html"
    assert html.read_text(encoding="utf-8") == "<html>rendered</html>"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "cache.v2 spec.html",
        "cache.v2 spec.md",
        "cache.v2 spec.pdf",
    ]
    assert source.read_text(encoding="utf-8") == "# Canonical Markdown\n"
    assert fake.prepared
    assert all(not path.exists() for path in fake.prepared)
    for command, options in fake.pandoc_calls:
        scratch = Path(str(options["cwd"]))
        assert not scratch.exists()
        assert Path(command[-1]).is_relative_to(scratch)
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["PATH"].startswith(str(tools.node_bin) + os.pathsep)
        assert environment["PUPPETEER_CACHE_DIR"] == str(tools.puppeteer_cache)
        # No stylesheet is linked: Pandoc embeds KaTeX's, and the others are inlined.
        assert "--css" not in command
        katex = Path(unquote(urlsplit(environment[render.KATEX_STYLESHEET_VARIABLE]).path))
        assert katex.parent == scratch
    # The GitHub Markdown stylesheet comes first, so the shared styles override it.
    assert fake.styles[0].startswith("<style>\n.markdown-body { color: #1f2328; }\n</style>")
    assert fake.styles[0].index("#1f2328") < fake.styles[0].index(".table-cols table")
    fonts = tools.node_modules / "katex/dist/fonts"
    assert fake.katex_stylesheets[0] == (
        "@font-face{font-family:KaTeX_Main;"
        f'src:url({(fonts / "KaTeX_Main-Regular.woff2").as_uri()}) format("woff2")}}'
        ".katex{font:normal 1.21em KaTeX_Main}"
    )


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("github-markdown-css/github-markdown-light.css", id="github-stylesheet"),
        pytest.param("katex/dist/katex.min.css", id="katex-stylesheet"),
        pytest.param("katex/dist/fonts/KaTeX_Main-Regular.woff2", id="katex-font"),
    ],
)
def test_missing_stylesheets_and_fonts_fail_before_rendering(
    name: str, fake: FakeRenderer, run: mock.Mock, source: Path, tools: Toolchain
) -> None:
    (tools.node_modules / name).unlink()
    with pytest.raises(render.RenderError, match=r"setup --force"):
        render.render_markdown(source, FORMAT.HTML, tools=tools)
    run.assert_not_called()
    assert not fake.prepare_calls


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, render.PDF_TIMEOUT_DEFAULT, id="unset"),
        pytest.param("", render.PDF_TIMEOUT_DEFAULT, id="empty"),
        pytest.param("  ", render.PDF_TIMEOUT_DEFAULT, id="blank"),
        pytest.param("0", 0, id="no-limit"),
        pytest.param(" 1200 ", 1200, id="padded"),
        pytest.param(str(render.PDF_TIMEOUT_MAX), render.PDF_TIMEOUT_MAX, id="maximum"),
    ],
)
def test_pdf_timeout_accepts_whole_seconds_and_defaults_when_blank(
    value: str | None, expected: int
) -> None:
    assert render.pdf_timeout_seconds(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("-1", id="negative"),
        pytest.param("1.5", id="fraction"),
        pytest.param("10s", id="unit"),
        pytest.param("\u00b2", id="superscript-digit"),
        pytest.param(str(render.PDF_TIMEOUT_MAX + 1), id="over-maximum"),
    ],
)
def test_pdf_timeout_rejects_anything_but_whole_seconds_up_to_the_maximum(value: str) -> None:
    with pytest.raises(render.RenderError, match=r"--pdf-timeout"):
        render.pdf_timeout_seconds(value)


def test_pdf_printer_receives_node_modules_and_timeouts(
    fake: FakeRenderer, source: Path, tools: Toolchain
) -> None:
    render.render_markdown(source, FORMAT.PDF, tools=tools)
    render.render_markdown(source, FORMAT.PDF, options=Options(pdf_timeout="0"), tools=tools)
    assert [
        command[command.index("--timeout-seconds") + 1] for command in fake.printer_commands
    ] == [str(render.PDF_TIMEOUT_DEFAULT), "0"]
    for command in fake.printer_commands:
        assert command[command.index("--node-modules") + 1] == str(tools.node_modules)


def test_invalid_pdf_timeout_fails_before_rendering_and_html_ignores_it(
    fake: FakeRenderer, run: mock.Mock, source: Path, tools: Toolchain
) -> None:
    invalid = Options(pdf_timeout="ten")
    with pytest.raises(render.RenderError, match=r"MD_DOC_CONVERTER_PDF_TIMEOUT"):
        render.render_markdown(source, FORMAT.PDF, options=invalid, tools=tools)
    run.assert_not_called()
    assert not fake.prepare_calls
    html = render.render_markdown(source, FORMAT.HTML, options=invalid, tools=tools)
    assert html.is_file()
    assert not fake.printer_commands


def test_command_line_flags_override_environment_defaults(
    fake: FakeRenderer,
    tmp_path: Path,
    source: Path,
    tools: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def installed() -> Toolchain:
        return tools

    monkeypatch.setenv("MD_DOC_CONVERTER_HEADER", "Header from environment")
    monkeypatch.setenv("MD_DOC_CONVERTER_FOOTER", "Footer from environment")
    monkeypatch.setenv("MD_DOC_CONVERTER_PDF_TIMEOUT", "45")
    monkeypatch.setattr(render, "toolchain", installed)
    path = str(source)
    assert render.main([path], action="pdf") == 0
    assert (
        render.main(
            [path, "--header", "Flag header", "--footer", "", "--pdf-timeout", "30"],
            action="pdf",
        )
        == 0
    )
    assert render.main([path, "--pdf-timeout", "-5"], action="pdf") == 1
    timeouts = [
        command[command.index("--timeout-seconds") + 1] for command in fake.printer_commands
    ]
    assert timeouts == ["45", "30"]
    assert 'content: "Header from environment"' in fake.styles[0]
    assert 'content: "Footer from environment"' in fake.styles[0]
    assert 'content: "Flag header"' in fake.styles[1]
    assert "Footer from environment" not in fake.styles[1]
    messages, errors = capsys.readouterr()
    assert f"-> {tmp_path / 'cache.v2 spec.pdf'}" in messages
    assert "--pdf-timeout" in errors


def test_extra_stylesheet_is_inlined_last_and_protected(
    fake: FakeRenderer, tmp_path: Path, source: Path, tools: Toolchain
) -> None:
    extra = tmp_path / "extra.html"
    extra.write_text(".markdown-body { color: red; }\n", encoding="utf-8")
    render.render_markdown(
        source, FORMAT.HTML, options=Options(extra_css=extra, header="Header"), tools=tools
    )
    with pytest.raises(render.RenderError, match=r"would replace an input"):
        render.render_markdown(
            source, FORMAT.HTML, output=extra, options=Options(extra_css=extra), tools=tools
        )
    assert fake.styles[0].index("color: red") > fake.styles[0].index('"Header"')
    assert extra.read_text(encoding="utf-8") == ".markdown-body { color: red; }\n"
    extra.write_text("</STYLE><script>alert(1)</script>", encoding="utf-8")
    with pytest.raises(render.RenderError, match=r"must not contain"):
        render.render_markdown(source, FORMAT.HTML, options=Options(extra_css=extra), tools=tools)


def test_conversion_failure_leaves_previous_deliverable_and_cleans_scratch(
    fake: FakeRenderer, tmp_path: Path, source: Path, tools: Toolchain
) -> None:
    destination = tmp_path / "cache.v2 spec.pdf"
    destination.write_text("previous", encoding="utf-8")
    fake.pandoc_fails = True
    with pytest.raises(subprocess.CalledProcessError):
        render.render_markdown(source, FORMAT.PDF, tools=tools)
    assert destination.read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "cache.v2 spec.md",
        "cache.v2 spec.pdf",
    ]
    assert all(not path.exists() for path in fake.prepared)


@pytest.mark.usefixtures("source")
def test_publication_failure_leaves_the_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "doc.pdf"
    destination.write_text("previous", encoding="utf-8")
    staged = tmp_path / "staged.pdf"
    staged.write_text("new", encoding="utf-8")
    original_replace = Path.replace

    def fail_partial(path: Path, target: Path) -> Path:
        if path.name.endswith(".partial"):
            raise OSError("injected publication failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_partial)
    with pytest.raises(render.RenderError, match=r"any previous file is unchanged"):
        render.publish(staged, destination)
    assert destination.read_text(encoding="utf-8") == "previous"
    assert {path.name for path in tmp_path.iterdir()} == {
        "cache.v2 spec.md",
        "doc.pdf",
        "staged.pdf",
    }


def test_publication_keeps_the_rendered_file_mode(tmp_path: Path) -> None:
    staged = tmp_path / "staged.pdf"
    staged.write_text("new", encoding="utf-8")
    staged.chmod(0o640)
    destination = tmp_path / "new folder" / "doc.pdf"
    render.publish(staged, destination)
    assert destination.read_text(encoding="utf-8") == "new"
    assert destination.stat().st_mode & 511 == 416


def test_intermediates_are_kept_on_request_even_when_rendering_fails(
    fake: FakeRenderer,
    tmp_path: Path,
    source: Path,
    tools: Toolchain,
    capsys: pytest.CaptureFixture[str],
) -> None:
    kept = tmp_path / "debug"
    render.render_markdown(
        source, FORMAT.PDF, options=Options(keep_intermediates=kept), tools=tools
    )
    messages = capsys.readouterr().err
    assert sorted(str(path.relative_to(kept)) for path in kept.rglob("*") if path.is_file()) == [
        "katex.css",
        "markdown/cache.v2 spec.md",
        "page-styles.html",
        "render/cache.v2 spec.html",
        "render/cache.v2 spec.pdf",
    ]
    assert str(kept) in messages

    failed = tmp_path / "failed"
    fake.pandoc_fails = True
    with pytest.raises(subprocess.CalledProcessError):
        render.render_markdown(
            source, FORMAT.HTML, options=Options(keep_intermediates=failed), tools=tools
        )
    assert (failed / "markdown/cache.v2 spec.md").is_file()
    assert (failed / "page-styles.html").is_file()


@pytest.mark.usefixtures("fake")
def test_intermediate_directory_must_be_new_or_empty_and_separate(
    tmp_path: Path, source: Path, tools: Toolchain, subtests: pytest.Subtests
) -> None:
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "page.md").write_text("# Page\n", encoding="utf-8")
    cases = (
        (source, nonempty, "new or empty"),
        (source, source, "new or empty"),
        (folder, folder / "debug", "overlaps"),
        (source, tmp_path, "new or empty"),
    )
    for selected, kept, message in cases:
        with subtests.test(kept=kept), pytest.raises(render.RenderError, match=message):
            render.render_markdown(
                selected, FORMAT.HTML, options=Options(keep_intermediates=kept), tools=tools
            )
    assert (nonempty / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (folder / "debug").exists()


def test_missing_prerequisites_fail_before_any_rendering(
    fake: FakeRenderer,
    run: mock.Mock,
    tmp_path: Path,
    source: Path,
    tools: Toolchain,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(toolchain: Toolchain, *, pdf: bool) -> list[str]:
        return ["pandoc: not found on PATH. Install Pandoc"]

    monkeypatch.setattr(render, "render_problems", missing)
    with pytest.raises(render.RenderError, match=r"(?s)prerequisites are missing.*doctor"):
        render.render_markdown(source, FORMAT.PDF, tools=tools)
    run.assert_not_called()
    assert not fake.prepare_calls
    assert not (tmp_path / "cache.v2 spec.pdf").exists()


@pytest.mark.parametrize(
    ("kind", "choice", "work_tree", "expected"),
    [
        pytest.param("folder", None, True, True, id="folder-in-a-work-tree"),
        pytest.param("folder", None, False, False, id="folder-outside-work-trees"),
        pytest.param("folder", False, True, False, id="folder-opted-out"),
        pytest.param("folder", True, False, True, id="folder-opted-in"),
        pytest.param("file", None, True, False, id="file-in-a-work-tree"),
    ],
)
def test_gitignore_filtering_defaults_to_folders_in_git_work_trees(
    kind: str,
    choice: bool | None,
    work_tree: bool,
    expected: bool,
    fake: FakeRenderer,
    tmp_path: Path,
    source: Path,
    tools: Toolchain,
) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "page.md").write_text("# Page\n", encoding="utf-8")
    selected = folder if kind == "folder" else source
    fake.git_work_tree = work_tree
    render.render_markdown(
        selected, FORMAT.HTML, options=Options(respect_gitignore=choice), tools=tools
    )
    assert fake.prepare_calls[-1] == (selected, expected)


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


def test_an_escaped_pipe_in_table_code_renders_as_gfm_shows_it(pandoc: str) -> None:
    defaults = asset_path("pandoc-defaults.yaml").read_text(encoding="utf-8")
    reader = re.search(r"^from: (\S+)$", defaults, re.MULTILINE)
    assert reader is not None
    assert "table-code-pipes.lua" in defaults
    result = subprocess.run(
        [
            pandoc,
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
