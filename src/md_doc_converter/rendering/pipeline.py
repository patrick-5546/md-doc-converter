"""Render Markdown to one named HTML or PDF file without public intermediates."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote, urlsplit

from md_doc_converter import executables
from md_doc_converter import pandoc as _pandoc
from md_doc_converter.console import Console, activated, active, add_verbosity_options, shown
from md_doc_converter.download import DownloadError, download
from md_doc_converter.media_types import FONT_TYPES, IMAGE_TYPES
from md_doc_converter.rendering.header import page_css
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.checks import render_problems
from md_doc_converter.toolchain.environment import Toolchain, render_environment, toolchain

if TYPE_CHECKING:
    from md_doc_converter.markdown.preprocess import PreparedDocument

PDF_PRINTER = Path(__file__).with_name("print-pdf.mjs")
# Stylesheets in the installed npm packages.
GITHUB_STYLESHEET = Path("github-markdown-css", "github-markdown-light.css")
KATEX_DIST = Path("katex", "dist")
# The file of the stylesheet that katex.lua adds to a document with math.
KATEX_STYLESHEET_VARIABLE = "MD_DOC_CONVERTER_KATEX_CSS"
# Each KaTeX font lists its WOFF2 file first, then WOFF and TrueType ones.
KATEX_FONT_SOURCES = re.compile(
    r'src:url\(fonts/([^()]+\.woff2)\) format\("woff2"\)(?:,url\([^()]*\) format\("[^"]*"\))*'
)
# Rendered pages run no script and load nothing but the images and media they show; the
# rest is embedded. The PDF printer also runs without JavaScript and blocks other requests.
CONTENT_SECURITY_POLICY = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'none'; img-src * data:; media-src * data:; style-src 'unsafe-inline' data:; "
    "font-src data:; base-uri 'none'; form-action 'none'\">"
)
# A url() in a stylesheet, its address quoted or not.
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s"']*))\s*\)""")
PDF_TIMEOUT_DEFAULT = 600
# Node timers overflow above 2**31 - 1 milliseconds.
PDF_TIMEOUT_MAX = 2_147_483
HEADER_VARIABLE = "MD_DOC_CONVERTER_HEADER"
FOOTER_VARIABLE = "MD_DOC_CONVERTER_FOOTER"
PDF_TIMEOUT_VARIABLE = "MD_DOC_CONVERTER_PDF_TIMEOUT"


class RenderError(Exception):
    """A rendering request could not be completed safely."""


class OutputFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"

    @property
    def suffixes(self) -> frozenset[str]:
        return frozenset({".html", ".htm"}) if self is OutputFormat.HTML else frozenset({".pdf"})


@dataclass(frozen=True)
class RenderOptions:
    header: str = ""
    footer: str = ""
    extra_css: Path | None = None
    # None skips Git-ignored files only when a folder input is inside a Git work tree.
    respect_gitignore: bool | None = None
    pdf_timeout: str | None = None
    keep_intermediates: Path | None = None
    download_images: bool = False


def pdf_timeout_seconds(value: str | None) -> int:
    """Parse a PDF time limit: blank selects the default and 0 disables the limit."""
    text = (value or "").strip()
    if not text:
        return PDF_TIMEOUT_DEFAULT
    if not (text.isascii() and text.isdigit()) or int(text) > PDF_TIMEOUT_MAX:
        raise RenderError(
            f"--pdf-timeout ({PDF_TIMEOUT_VARIABLE}) must be whole seconds from 0 (no limit) "
            f"to {PDF_TIMEOUT_MAX}: {value!r}"
        )
    return int(text)


def absolute_path(path: Path) -> Path:
    """Normalize spelling without changing a supplied symlink's document name."""
    # Unlike Path.resolve, abspath collapses ".." without following symlinks.
    return Path(os.path.abspath(path.expanduser()))  # noqa: PTH100


def output_path(source: Path, output_format: OutputFormat, output: Path | None = None) -> Path:
    """Use an explicit output, or the input's name next to a file or inside a folder."""
    if output is not None:
        selected = absolute_path(output)
        if selected.suffix.lower() not in output_format.suffixes:
            allowed = ", ".join(sorted(output_format.suffixes))
            raise RenderError(f"Output must end in {allowed}: {selected}")
        return selected
    selected = absolute_path(source)
    if not selected.exists():
        raise RenderError(f"Input does not exist: {selected}")
    name, directory = (
        (selected.name, selected) if selected.is_dir() else (selected.stem, selected.parent)
    )
    if name in {"", ".", ".."} or any(character in name for character in ("/", "\\", "\n", "\r")):
        raise RenderError(f"Cannot derive a safe output filename from {selected}")
    return directory / f"{name}.{output_format.value}"


def validate_destinations(destinations: tuple[Path, ...], protected: tuple[Path, ...]) -> None:
    for destination in destinations:
        if destination.is_symlink():
            raise RenderError(f"Output must not be a symlink: {destination}")
        if destination.exists() and not destination.is_file():
            raise RenderError(f"Output is not a regular file: {destination}")
        for source in protected:
            if destination.resolve() == source.resolve() or (
                destination.exists() and source.exists() and destination.samefile(source)
            ):
                raise RenderError(f"Output would replace an input file: {destination}")


def _overlaps(first: Path, second: Path) -> bool:
    first, second = first.resolve(), second.resolve()
    return first == second or first in second.parents or second in first.parents


def prepare_intermediates(directory: Path, source: Path, destination: Path) -> Path:
    selected = absolute_path(directory)
    # A Windows junction links a folder, as a symlink does.
    if selected.is_symlink() or selected.is_junction():
        raise RenderError(f"--keep-intermediates must not be a symlink: {selected}")
    if selected.exists() and (not selected.is_dir() or any(selected.iterdir())):
        raise RenderError(f"--keep-intermediates must be a new or empty directory: {selected}")
    for protected in (source, destination):
        if _overlaps(selected, protected):
            raise RenderError(f"--keep-intermediates overlaps {protected}: {selected}")
    return selected


def _read_extra_css(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RenderError(f"Cannot read extra stylesheet {path}: {error}") from error
    if "</style" in text.lower():
        raise RenderError(f"Extra stylesheet must not contain '</style': {path}")
    return embed_stylesheet_files(text, path.parent)


def embed_stylesheet_files(css: str, folder: Path) -> str:
    """Embed the files that the url()s of the user's stylesheet name, as data URIs.

    Pandoc reads none of them in its sandbox. Local paths are relative to *folder*, and
    http(s) URLs are downloaded; one that cannot be read stays as it is, with a warning.
    """

    def embed(match: re.Match[str]) -> str:
        target = next((group for group in match.groups() if group is not None), "")
        url = urlsplit(target)
        scheme = url.scheme.casefold()
        local = scheme in {"", "file"} and bool(url.path) and not url.netloc
        if not local and scheme not in {"http", "https"}:
            return match[0]
        try:
            if local:
                path = folder / unquote(url.path)
                data = path.read_bytes()
                suffix = path.suffix.casefold()
                kind = (
                    FONT_TYPES.get(suffix)
                    or IMAGE_TYPES.get(suffix)
                    or mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream"
                )
            else:
                data, kind = download(target)
        except DownloadError as error:
            problem = str(error)
        except (OSError, ValueError) as error:
            problem = f"it could not be read ({error})"
        else:
            fragment = f"#{url.fragment}" if url.fragment else ""
            return f'url("data:{kind};base64,{base64.b64encode(data).decode("ascii")}{fragment}")'
        active().warning(
            f"extra stylesheet: {target} is not embedded: {problem}",
            kind="extra stylesheet resource not embedded",
        )
        return match[0]

    return _CSS_URL.sub(embed, css)


def page_styles(header: str = "", footer: str = "", extra_css: str = "") -> str:
    """Inline the shared overrides, page text and extra CSS after the linked stylesheets."""
    try:
        text_css = page_css(header, footer)
    except ValueError as error:
        raise RenderError(f"Invalid header or footer text: {error}") from error
    parts = [asset_path("style-overrides.css").read_text(encoding="utf-8"), text_css, extra_css]
    body = "\n".join(part.strip("\n") for part in parts if part.strip())
    return f"<style>\n{body}\n</style>\n"


def github_stylesheet(tools: Toolchain) -> str:
    try:
        return (tools.node_modules / GITHUB_STYLESHEET).read_text(encoding="utf-8")
    except OSError as error:
        raise RenderError(
            f"cannot read the GitHub Markdown stylesheet ({error}); "
            "run `md-doc-converter setup --force`"
        ) from error


def katex_stylesheet(tools: Toolchain) -> str:
    """KaTeX's stylesheet with only its WOFF2 fonts, which every current browser reads."""
    dist = tools.node_modules / KATEX_DIST
    missing: list[str] = []

    def woff2_only(match: re.Match[str]) -> str:
        font = dist / "fonts" / match[1]
        if not font.is_file():
            missing.append(match[1])
            return match[0]
        data = base64.b64encode(font.read_bytes()).decode("ascii")
        return f'src:url(data:font/woff2;base64,{data}) format("woff2")'

    try:
        css, fonts = KATEX_FONT_SOURCES.subn(
            woff2_only, (dist / "katex.min.css").read_text(encoding="utf-8")
        )
    except OSError as error:
        raise RenderError(
            f"cannot read KaTeX's stylesheet ({error}); run `md-doc-converter setup --force`"
        ) from error
    if not fonts or missing:
        detail = f"missing {', '.join(missing)}" if missing else "no fonts in its stylesheet"
        raise RenderError(
            f"cannot use KaTeX's fonts ({detail}); run `md-doc-converter setup --force`"
        )
    return css


def publish(staged: Path, destination: Path) -> None:
    """Replace the destination atomically; a failure leaves any previous file unchanged."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    os.close(descriptor)
    partial = Path(name)
    try:
        shutil.copyfile(staged, partial)
        shutil.copymode(staged, partial)
        partial.replace(destination)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise RenderError(
            f"Cannot publish {destination}; any previous file is unchanged: {error}"
        ) from error


def _filter_gitignored(choice: bool | None, source: Path) -> bool:
    if choice is not None:
        return choice
    if not source.is_dir():
        return False
    # Imported here so command-line help does not load the Markdown toolchain.
    from md_doc_converter.markdown.preprocess import in_git_work_tree

    return in_git_work_tree(source)


def _prepare_markdown(
    source: Path, *, respect_gitignore: bool, download_images: bool = False
) -> PreparedDocument:
    # Imported here so command-line help does not load the Markdown toolchain.
    from md_doc_converter.markdown.document import MarkdownError
    from md_doc_converter.markdown.headings import HeadingError
    from md_doc_converter.markdown.preprocess import prepare_markdown

    try:
        return prepare_markdown(
            source, respect_gitignore=respect_gitignore, download_images=download_images
        )
    except (MarkdownError, HeadingError, ValueError) as error:
        raise RenderError(f"cannot prepare {source}: {error}") from error


# How Pandoc reports a resource that it cannot embed, such as an image outside its sandbox.
_NOT_FETCHED = "Could not fetch resource "


def pandoc_messages(stderr: str, reported: frozenset[str]) -> list[str]:
    """Pandoc's messages, without its warnings for the images that preparation reported."""
    known = {unquote(target) for target in reported}
    return [
        message
        for message in _pandoc.messages(stderr)
        if not (
            message.startswith(_NOT_FETCHED)
            and unquote(message[len(_NOT_FETCHED) :].strip()) in known
        )
    ]


def _keep(work: Path, directory: Path) -> None:
    try:
        shutil.copytree(work, directory, dirs_exist_ok=True)
    except OSError as error:
        active().warning(f"cannot keep intermediates in {shown(directory)}: {error}")
    else:
        active().note(f"intermediates kept in {shown(directory)}")


def render_markdown(
    source: Path,
    output_format: OutputFormat,
    output: Path | None = None,
    options: RenderOptions | None = None,
    *,
    tools: Toolchain | None = None,
) -> Path:
    options = options or RenderOptions()
    pdf = output_format is OutputFormat.PDF
    timeout = pdf_timeout_seconds(options.pdf_timeout) if pdf else 0
    source = absolute_path(source)
    if not source.is_file() and not source.is_dir():
        raise RenderError(f"Input is not a file or directory: {source}")
    destination = output_path(source, output_format, output)
    protected: tuple[Path, ...] = (source,)
    extra_css = ""
    if options.extra_css is not None:
        stylesheet = absolute_path(options.extra_css)
        if not stylesheet.is_file():
            raise RenderError(f"Extra stylesheet does not exist: {stylesheet}")
        extra_css = _read_extra_css(stylesheet)
        protected += (stylesheet,)
    validate_destinations((destination,), protected)
    styles = page_styles(options.header, options.footer, extra_css)
    keep = (
        prepare_intermediates(options.keep_intermediates, source, destination)
        if options.keep_intermediates is not None
        else None
    )
    tools = tools or toolchain()
    problems = render_problems(tools, pdf=pdf)
    if problems:
        raise RenderError(
            "rendering prerequisites are missing:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
            + "\nRun `md-doc-converter doctor` for details."
        )
    base_css = github_stylesheet(tools)
    math_css = katex_stylesheet(tools)
    environment = render_environment(tools)
    filter_gitignored = _filter_gitignored(options.respect_gitignore, source)

    with tempfile.TemporaryDirectory(prefix="md-doc-converter-render-") as temporary:
        work = Path(temporary)
        if source.is_dir() and work.resolve().is_relative_to(source.resolve()):
            raise RenderError(
                "TMPDIR must be outside the input directory for private preprocessing"
            )
        try:
            name = destination.stem
            # Pandoc reads the document as prepared, already parsed, as Pandoc JSON. Its HTML
            # title, without a heading to take it from, is the name of this file.
            document = work / "document" / f"{name}.json"
            document.parent.mkdir()
            active().status("preparing the Markdown")
            prepared = _prepare_markdown(
                source,
                respect_gitignore=filter_gitignored,
                download_images=options.download_images,
            )
            document.write_text(json.dumps(prepared.document), encoding="utf-8")

            styles_file = work / "page-styles.html"
            styles_file.write_text(
                f"{CONTENT_SECURITY_POLICY}\n<style>\n{base_css.strip()}\n</style>\n{styles}",
                encoding="utf-8",
            )
            katex_file = work / "katex.css"
            katex_file.write_text(math_css, encoding="utf-8")
            staged_dir = work / "render"
            staged_dir.mkdir()
            html = staged_dir / f"{name}.html"
            command = [
                executables.resolve("pandoc", environment.get("PATH")),
                # Read no file and fetch no URL that the document names; the images that it
                # may show are embedded during preparation.
                "--sandbox",
                "--defaults",
                str(asset_path("pandoc-defaults.yaml")),
                "--standalone",
                "--include-in-header",
                str(styles_file),
                "-o",
                str(html),
                str(document),
            ]
            active().status("rendering")
            converted = subprocess.run(
                command,
                cwd=work,
                env={**environment, KATEX_STYLESHEET_VARIABLE: str(katex_file)},
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            # Pandoc read the prepared document, not the Markdown, so its messages name no line.
            for message in pandoc_messages(converted.stderr or "", prepared.unembedded):
                active().warning(message, kind=_pandoc.message_kind(message), where=shown(source))
            if converted.returncode:
                raise subprocess.CalledProcessError(converted.returncode, command)
            if prepared.images:
                active().status("embedding images")
                # Pandoc read a short placeholder for each embedded image's data.
                written = html.read_bytes().decode("utf-8")
                html.write_bytes(prepared.embed_images(written).encode("utf-8"))
            staged = html
            if pdf:
                active().status("printing the PDF")
                staged = staged_dir / f"{name}.pdf"
                subprocess.run(
                    [
                        executables.resolve("node", environment.get("PATH")),
                        str(PDF_PRINTER),
                        "--node-modules",
                        str(tools.node_modules),
                        "--timeout-seconds",
                        str(timeout),
                        "-o",
                        str(staged),
                        str(html),
                    ],
                    cwd=work,
                    env=environment,
                    check=True,
                )
            if not staged.is_file():
                raise RenderError(f"Renderer did not produce {staged.name}")
            validate_destinations((destination,), protected)
            publish(staged, destination)
        finally:
            if keep is not None:
                _keep(work, keep)
    return destination


def _failed_step(error: subprocess.CalledProcessError) -> str:
    command = error.cmd[0] if isinstance(error.cmd, list) and error.cmd else error.cmd
    return (
        f"{Path(str(command)).name.removesuffix('.exe')} failed with exit status {error.returncode}"
    )


def main(argv: list[str] | None = None, *, action: Literal["html", "pdf"]) -> int:
    output_format = OutputFormat(action)
    parser = argparse.ArgumentParser(
        prog=f"md-doc-converter md-to-{action}",
        description=(
            f"Render a Markdown file, or a folder of Markdown files combined in order, to "
            f"{action.upper()}. The prepared document and other intermediates stay private."
        ),
    )
    parser.add_argument("input", type=Path, metavar="INPUT", help="Markdown file or folder")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar=f"FILE.{action}",
        help=(
            f"Output file (default: <input name>.{action} next to a file input, "
            "or inside a folder input)"
        ),
    )
    parser.add_argument(
        "--header",
        metavar="TEXT",
        help=f"Page header text, top right (default: ${HEADER_VARIABLE}, or none)",
    )
    parser.add_argument(
        "--footer",
        metavar="TEXT",
        help=f"Page footer text, bottom left (default: ${FOOTER_VARIABLE}, or none)",
    )
    parser.add_argument(
        "--extra-css",
        type=Path,
        metavar="FILE",
        help="Stylesheet applied after the built-in styles",
    )
    parser.add_argument(
        "--respect-gitignore",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Skip Git-ignored files in a folder input (default: when it is in a Git work tree)",
    )
    parser.add_argument(
        "--keep-intermediates",
        type=Path,
        metavar="DIR",
        help="Keep the private intermediates in a new or empty directory, even on failure",
    )
    parser.add_argument(
        "--download-images",
        action="store_true",
        help=(
            "Download and embed the images that the Markdown shows by http(s) URL; use it only "
            "for documents you trust, since they can make rendering request any URL"
        ),
    )
    if output_format is OutputFormat.PDF:
        parser.add_argument(
            "--pdf-timeout",
            metavar="SECONDS",
            help=(
                f"Limit for each browser step (default: ${PDF_TIMEOUT_VARIABLE}, or "
                f"{PDF_TIMEOUT_DEFAULT}; 0 = no limit)"
            ),
        )
    add_verbosity_options(parser)
    arguments = parser.parse_args(argv)
    pdf_timeout = getattr(arguments, "pdf_timeout", None)
    options = RenderOptions(
        header=os.environ.get(HEADER_VARIABLE, "")
        if arguments.header is None
        else arguments.header,
        footer=os.environ.get(FOOTER_VARIABLE, "")
        if arguments.footer is None
        else arguments.footer,
        extra_css=arguments.extra_css,
        respect_gitignore=arguments.respect_gitignore,
        pdf_timeout=os.environ.get(PDF_TIMEOUT_VARIABLE) if pdf_timeout is None else pdf_timeout,
        keep_intermediates=arguments.keep_intermediates,
        download_images=arguments.download_images,
    )
    console = Console(f"md-to-{action}", arguments.verbosity)
    console.note(f"rendering {shown(arguments.input)}")
    with activated(console):
        try:
            destination = render_markdown(arguments.input, output_format, arguments.output, options)
        except subprocess.CalledProcessError as error:
            console.finish()
            console.error(_failed_step(error))
            return 1
        except (RenderError, OSError) as error:
            console.finish()
            console.error(str(error))
            return 1
    console.finish()
    console.result(f"rendered {shown(arguments.input)} -> {shown(destination)}")
    return 0
