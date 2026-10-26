"""Rendering Markdown to one HTML or PDF file: names, publication, styles and options.

Most tests render with `fake`, which stands in for preparing the Markdown and for running git,
pandoc and the PDF printer, and with `tools`, a toolchain with only the npm packages' files.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import READER, MarkdownError
from md_doc_converter.markdown.file_paths import url_path
from md_doc_converter.markdown.headings import HeadingError
from md_doc_converter.pandoc import JSONValue
from md_doc_converter.rendering import pipeline as render
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.environment import Toolchain
from tests.support.fake_tools import write_packages
from tests.support.links import FOLDER_LINKS, link_folder
from tests.support.web_server import WebServer

FORMAT = render.OutputFormat
Options = render.RenderOptions


def document(*blocks: JSONValue) -> dict[str, JSONValue]:
    """A prepared document, as Pandoc JSON, of *blocks*."""
    return {"pandoc-api-version": [1, 23, 1, 2], "meta": {}, "blocks": list(blocks)}


@dataclass
class FakeRenderer:
    """Prepares Markdown, and finds and runs git, pandoc and the PDF printer, recording what
    they get.

    Each program is found at /found/<name>. git reports whether the input is in a work tree.
    pandoc writes *html*, or fails with status 7; the PDF printer records the HTML it gets and
    writes a placeholder PDF. Any other command fails the test.
    """

    git_work_tree: bool = False
    pandoc_fails: bool = False
    preparation: preprocess.PreparedDocument = field(
        default_factory=lambda: preprocess.PreparedDocument(
            document(
                {"t": "Header", "c": [1, ["prepared", [], []], [{"t": "Str", "c": "Prepared"}]]}
            )
        )
    )
    html: str = "<html>rendered</html>"
    prepare_calls: list[tuple[Path, bool, bool]] = field(default_factory=list)
    prepared: list[Path] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    katex_stylesheets: list[str] = field(default_factory=list)
    lookups: list[tuple[str, str | None]] = field(default_factory=list)
    pandoc_calls: list[tuple[list[str], dict[str, object]]] = field(default_factory=list)
    printer_commands: list[list[str]] = field(default_factory=list)
    printed: list[str] = field(default_factory=list)

    def prepare(
        self, source: Path, *, respect_gitignore: bool, download_images: bool
    ) -> preprocess.PreparedDocument:
        self.prepare_calls.append((source, respect_gitignore, download_images))
        return self.preparation

    def resolve(self, name: str, path: str | None = None) -> str:
        self.lookups.append((name, path))
        return found(name)

    def run(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        program = Path(command[0]).stem
        if program == "git":
            stdout = "true\n" if self.git_work_tree else ""
            return subprocess.CompletedProcess(command, 0 if stdout else 128, stdout, "")
        if command[0] == found("pandoc"):
            self.prepared.append(Path(command[-1]))
            if self.pandoc_fails:
                raise subprocess.CalledProcessError(7, command)
            prepared = json.loads(self.prepared[-1].read_text(encoding="utf-8"))
            assert prepared == self.preparation.document
            styles = Path(command[command.index("--include-in-header") + 1])
            self.styles.append(styles.read_text(encoding="utf-8"))
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            katex = Path(environment[render.KATEX_STYLESHEET_VARIABLE])
            self.katex_stylesheets.append(katex.read_text(encoding="utf-8"))
            self.pandoc_calls.append((command, kwargs))
            # Exactly as given: in text mode, Windows would write each \n as \r\n.
            Path(command[command.index("-o") + 1]).write_bytes(self.html.encode("utf-8"))
        elif command[:2] == [found("node"), str(render.PDF_PRINTER)]:
            self.printer_commands.append(command)
            self.printed.append(Path(command[-1]).read_bytes().decode("utf-8"))
            Path(command[command.index("-o") + 1]).write_bytes(b"PDF")
        else:
            pytest.fail(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def found(name: str) -> str:
    """Where FakeRenderer finds the program *name*."""
    return str(Path(os.sep, "found", name))


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
    """No missing prerequisites, and a FakeRenderer for preparation, for finding the programs
    that rendering runs, and for subprocess.run."""
    renderer = FakeRenderer()

    def no_problems(tools: Toolchain, *, pdf: bool) -> list[str]:
        return []

    monkeypatch.setattr(render, "render_problems", no_problems)
    monkeypatch.setattr(render, "_prepare_markdown", renderer.prepare)
    monkeypatch.setattr(render.executables, "resolve", renderer.resolve)
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
        assert environment["PATH"].startswith(str(tools.bin_dir) + os.pathsep)
        assert environment["PUPPETEER_CACHE_DIR"] == str(tools.puppeteer_cache)
        # Pandoc reads no file or URL that the document names.
        assert "--sandbox" in command
        # No stylesheet is linked: katex.lua adds KaTeX's as a data URI, the others are inlined.
        assert "--css" not in command
        katex = Path(environment[render.KATEX_STYLESHEET_VARIABLE])
        assert katex.parent == scratch
    # A content security policy that allows no script, then the GitHub Markdown stylesheet,
    # so the shared styles override it.
    assert fake.styles[0].startswith(
        render.CONTENT_SECURITY_POLICY + "\n<style>\n.markdown-body { color: #1f2328; }\n</style>"
    )
    assert "script-src" not in render.CONTENT_SECURITY_POLICY
    assert "default-src 'none'" in render.CONTENT_SECURITY_POLICY
    assert fake.styles[0].index("#1f2328") < fake.styles[0].index(".table-cols table")
    # KaTeX's fonts are embedded in its stylesheet: "wOF2" is the fake font's content.
    assert fake.katex_stylesheets[0] == (
        "@font-face{font-family:KaTeX_Main;"
        'src:url(data:font/woff2;base64,d09GMg==) format("woff2")}'
        ".katex{font:normal 1.21em KaTeX_Main}"
    )


def test_render_runs_the_programs_that_its_path_holds(
    fake: FakeRenderer, source: Path, tools: Toolchain
) -> None:
    render.render_markdown(source, FORMAT.PDF, tools=tools)
    [(command, options)] = fake.pandoc_calls
    environment = options["env"]
    assert isinstance(environment, dict)
    # Each by its path on the render PATH, which starts with the toolchain's folders, so no
    # program in the current folder runs, as Windows would run one started by name.
    assert fake.lookups == [("pandoc", environment["PATH"]), ("node", environment["PATH"])]
    assert command[0] == found("pandoc")
    assert [printer[0] for printer in fake.printer_commands] == [found("node")]


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
    message = (
        "--pdf-timeout (MD_DOC_CONVERTER_PDF_TIMEOUT) must be whole seconds from 0 (no limit) "
        f"to {render.PDF_TIMEOUT_MAX}: {value!r}"
    )
    with pytest.raises(render.RenderError, match=f"^{re.escape(message)}$"):
        render.pdf_timeout_seconds(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, render.LANG_DEFAULT, id="unset"),
        pytest.param("", render.LANG_DEFAULT, id="empty"),
        pytest.param("  ", render.LANG_DEFAULT, id="blank"),
        pytest.param("de", "de", id="language"),
        pytest.param(" en-GB ", "en-GB", id="region"),
        pytest.param("zh-Hant-TW", "zh-Hant-TW", id="script"),
        pytest.param("es-419", "es-419", id="numeric-region"),
    ],
)
def test_language_tag_accepts_tags_and_defaults_when_blank(
    value: str | None, expected: str
) -> None:
    assert render.language_tag(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("en_US", id="underscore"),
        pytest.param("english", id="word"),
        pytest.param("true", id="boolean"),
        pytest.param("e", id="short"),
        pytest.param("en-", id="empty-subtag"),
        pytest.param("en GB", id="space"),
        pytest.param("de-verylongsubtag", id="long-subtag"),
        pytest.param("\u00e9n", id="not-ascii"),
    ],
)
def test_language_tag_rejects_anything_but_a_tag(value: str) -> None:
    message = (
        "--lang (MD_DOC_CONVERTER_LANG) must be a language tag such as en, en-GB or fr-CA: "
        f"{value!r}"
    )
    with pytest.raises(render.RenderError, match=f"^{re.escape(message)}$"):
        render.language_tag(value)


def languages(fake: FakeRenderer) -> list[str]:
    """The document language that Pandoc was given for each render."""
    return [
        argument.removeprefix("--metadata=lang=")
        for command, _ in fake.pandoc_calls
        for argument in command
        if argument.startswith("--metadata=lang=")
    ]


def test_pandoc_gets_the_document_language(
    fake: FakeRenderer, source: Path, tools: Toolchain
) -> None:
    render.render_markdown(source, FORMAT.HTML, tools=tools)
    render.render_markdown(source, FORMAT.PDF, options=Options(lang="fr-CA"), tools=tools)
    assert languages(fake) == [render.LANG_DEFAULT, "fr-CA"]


def test_an_invalid_language_fails_before_rendering(
    fake: FakeRenderer, run: mock.Mock, source: Path, tools: Toolchain
) -> None:
    for output_format in FORMAT:
        with pytest.raises(render.RenderError, match=r"MD_DOC_CONVERTER_LANG"):
            render.render_markdown(
                source, output_format, options=Options(lang="en_US"), tools=tools
            )
    run.assert_not_called()
    assert not fake.prepare_calls


def test_embedded_image_data_goes_into_the_html_after_pandoc(
    fake: FakeRenderer, source: Path, tools: Toolchain
) -> None:
    # Pandoc reads a short placeholder for each image's data; the HTML, and so the PDF, get it,
    # and nothing else in the HTML changes.
    placeholder = "MdcImage0123456789abcdef00000000"
    image: JSONValue = [
        ["", [], []],
        [{"t": "Str", "c": "chart"}],
        [f"data:image/png;base64,{placeholder}#top", ""],
    ]
    fake.preparation = preprocess.PreparedDocument(
        document({"t": "Para", "c": [{"t": "Image", "c": image}]}),
        images={placeholder: "Y2hhcnQ="},
    )
    fake.html = f'<p>\r\n<img src="data:image/png;base64,{placeholder}#top"></p>'
    embedded = '<p>\r\n<img src="data:image/png;base64,Y2hhcnQ=#top"></p>'

    html = render.render_markdown(source, FORMAT.HTML, tools=tools)
    render.render_markdown(source, FORMAT.PDF, tools=tools)

    assert html.read_bytes().decode("utf-8") == embedded
    assert fake.printed == [embedded]


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
    monkeypatch.setenv("MD_DOC_CONVERTER_LANG", "de")
    monkeypatch.setattr(render, "toolchain", installed)
    path = str(source)
    assert render.main([path], action="pdf") == 0
    assert (
        render.main(
            [
                path,
                *("--header", "Flag header", "--footer", ""),
                *("--pdf-timeout", "30", "--lang", "fr-CA"),
            ],
            action="pdf",
        )
        == 0
    )
    assert render.main([path, "--pdf-timeout", "-5"], action="pdf") == 1
    timeouts = [
        command[command.index("--timeout-seconds") + 1] for command in fake.printer_commands
    ]
    assert timeouts == ["45", "30"]
    assert languages(fake) == ["de", "fr-CA"]
    assert 'content: "Header from environment"' in fake.styles[0]
    assert 'content: "Footer from environment"' in fake.styles[0]
    assert 'content: "Flag header"' in fake.styles[1]
    assert "Footer from environment" not in fake.styles[1]
    messages, errors = capsys.readouterr()
    assert f"-> {tmp_path / 'cache.v2 spec.pdf'}" in messages
    assert (
        "md-to-pdf: error: --pdf-timeout (MD_DOC_CONVERTER_PDF_TIMEOUT) must be whole seconds "
        f"from 0 (no limit) to {render.PDF_TIMEOUT_MAX}: '-5'\n"
    ) in errors


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


def test_files_that_the_extra_stylesheet_names_are_embedded(
    tmp_path: Path, web: WebServer, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "brand.woff2").write_bytes(b"font")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"logo")
    web.files["/mark.svg"] = (b"<svg/>", "image/svg+xml")
    css = (
        "@font-face { src: url(fonts/brand.woff2) format('woff2'); }\n"
        f'.logo {{ background: url("{url_path(str(logo))}"), url({web.url}/mark.svg#top); }}\n'
        ".mask { mask: url(#shape), url(data:image/png;base64,AA==), url(missing.png), "
        f"url({web.url}/gone.svg); }}\n"
    )

    embedded = render.embed_stylesheet_files(css, tmp_path)

    assert embedded == (
        "@font-face { src: url(\"data:font/woff2;base64,Zm9udA==\") format('woff2'); }\n"
        '.logo { background: url("data:image/png;base64,bG9nbw=="), '
        'url("data:image/svg+xml;base64,PHN2Zy8+#top"); }\n'
        ".mask { mask: url(#shape), url(data:image/png;base64,AA==), url(missing.png), "
        f"url({web.url}/gone.svg); }}\n"
    )
    assert [path for path, _ in web.requests] == ["/mark.svg", "/gone.svg"]
    errors = capsys.readouterr().err.splitlines()
    assert errors[0].startswith(
        "md-doc-converter: warning: extra stylesheet: missing.png is not embedded: "
        "it could not be read ([Errno 2]"
    )
    assert errors[1:] == [
        (
            f"md-doc-converter: warning: extra stylesheet: {web.url}/gone.svg is not embedded: "
            "the server answered 404 Not Found"
        )
    ]


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
    # Windows keeps only whether a file is read-only.
    mode = 0o444 if sys.platform == "win32" else 0o640
    staged = tmp_path / "staged.pdf"
    staged.write_text("new", encoding="utf-8")
    staged.chmod(mode)
    destination = tmp_path / "new folder" / "doc.pdf"
    render.publish(staged, destination)
    assert destination.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(destination.stat().st_mode) == mode


@pytest.mark.parametrize("windows", [True, False], ids=["windows", "linux"])
def test_an_output_that_a_program_holds_open_is_named_to_close(
    windows: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def held_open(self: Path, target: Path) -> Path:
        raise PermissionError(13, "The file is being used by another process", str(target))

    staged = tmp_path / "staged.pdf"
    staged.write_text("new", encoding="utf-8")
    destination = tmp_path / "doc.pdf"
    destination.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(render, "WINDOWS", windows)
    monkeypatch.setattr(Path, "replace", held_open)
    with pytest.raises(render.RenderError, match="any previous file is unchanged") as raised:
        render.publish(staged, destination)
    # Windows keeps a file that a program holds open, as PDF viewers do.
    assert ("close any program that has it open" in str(raised.value)) is windows
    assert destination.read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["doc.pdf", "staged.pdf"]


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
    kept_files = (path.relative_to(kept) for path in kept.rglob("*") if path.is_file())
    assert sorted(path.as_posix() for path in kept_files) == [
        "document/cache.v2 spec.json",
        "katex.css",
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
    assert (failed / "document/cache.v2 spec.json").is_file()
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


@pytest.mark.parametrize("kind", FOLDER_LINKS)
def test_intermediates_are_never_kept_through_a_link(
    kind: str, fake: FakeRenderer, tmp_path: Path, source: Path, tools: Toolchain
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "debug"
    link_folder(kind, link, elsewhere)
    with pytest.raises(render.RenderError, match="must not be a symlink"):
        render.render_markdown(
            source, FORMAT.HTML, options=Options(keep_intermediates=link), tools=tools
        )
    assert not any(elsewhere.iterdir())
    assert not fake.prepare_calls


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
    assert fake.prepare_calls[-1] == (selected, expected, False)


def test_images_are_downloaded_only_with_the_flag(
    fake: FakeRenderer, source: Path, tools: Toolchain, monkeypatch: pytest.MonkeyPatch
) -> None:
    def installed() -> Toolchain:
        return tools

    monkeypatch.setattr(render, "toolchain", installed)
    assert render.main([str(source)], action="html") == 0
    assert render.main([str(source), "--download-images"], action="pdf") == 0
    assert [call[2] for call in fake.prepare_calls] == [False, True]


@pytest.mark.parametrize(
    "error",
    [MarkdownError("malformed Link"), HeadingError("malformed Header block"), ValueError("bad")],
    ids=["markdown", "heading", "value"],
)
def test_preprocessing_errors_become_render_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail(source: Path, *, respect_gitignore: bool, download_images: bool) -> str:
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
    assert f"md-to-html: error: cannot prepare {source}: 'utf-8' codec can't decode" in error
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


def pandoc_defaults() -> tuple[str, str]:
    """The renderer's Pandoc defaults file, and the Markdown reader that prepared the document
    that the file has Pandoc read."""
    defaults = asset_path("pandoc-defaults.yaml").read_text(encoding="utf-8")
    assert re.search(r"^from: json$", defaults, re.MULTILINE)
    assert READER in defaults
    return defaults, READER


def test_an_escaped_pipe_in_table_code_renders_as_gfm_shows_it(pandoc: str) -> None:
    defaults, reader = pandoc_defaults()
    assert "table-code-pipes.lua" in defaults
    result = subprocess.run(
        [
            pandoc,
            f"--from={reader}",
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


def elements(node: object, tag: str) -> list[object]:
    """The contents of each *tag* element in Pandoc JSON *node*, in document order."""
    if isinstance(node, list):
        return [found for item in node for found in elements(item, tag)]
    if not isinstance(node, dict):
        return []
    found: list[object] = [node["c"]] if node.get("t") == tag else []
    return found + [item for value in node.values() for item in elements(value, tag)]


GITHUB_MATH = (
    "Inline $`a^2`$, $``c`d``$, $`` e ``$, $`f``$, $`ab$, $b$, `$g$`, $\\\\{h\\\\}$ "
    "and $`\\\\{i\\\\}`$.\n\n"
    "```math\n\\sum_{i=1}^n i\n```\n\n"
    "~~~ math\nx\n~~~\n\n"
    "- ```math\n  y\n  ```\n\n"
    "$$`z`$$\n\n"
    "```{.math #eq}\nkept\n```\n\n"
    "```{.math .numbered}\nkept\n```\n\n"
    "```{.math label=sum}\nkept\n```\n"
)


def test_githubs_math_syntax_reads_as_pandocs_gfm_reader_reads_it(pandoc: str) -> None:
    defaults, reader = pandoc_defaults()
    assert defaults.index("gfm-math.lua") < defaults.index("katex.lua")

    def read(*options: str) -> object:
        result = subprocess.run(
            [pandoc, *options, "--to=json"],
            input=GITHUB_MATH,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    ours = read(f"--from={reader}", f"--lua-filter={asset_path('gfm-math.lua')}")
    github = read("--from=gfm")
    assert elements(ours, "Math") == elements(github, "Math")
    # A math block with other attributes is not GitHub's, and stays code.
    assert elements(ours, "CodeBlock") == [
        [["eq", ["math"], []], "kept"],
        [["", ["math", "numbered"], []], "kept"],
        [["", ["math"], [["label", "sum"]]], "kept"],
    ]


def test_pandoc_does_not_repeat_the_warnings_for_images_left_as_references() -> None:
    reported = frozenset({"/docs/outside%20folder/a.png", "https://example.test/b.png"})
    stderr = (
        "[WARNING] Could not fetch resource /docs/outside folder/a.png\n"
        "[WARNING] Could not fetch resource https://example.test/b.png\n"
        "[WARNING] Could not fetch resource clip.mp4\n"
        "[WARNING] Duplicate identifier 'a' at line 3\n"
    )
    assert render.pandoc_messages(stderr, reported) == [
        "Could not fetch resource clip.mp4",
        "Duplicate identifier 'a' at line 3",
    ]
