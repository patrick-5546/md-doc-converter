"""Render Markdown to one named HTML or PDF file without public intermediates."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from md_doc_converter.rendering.header import page_css
from md_doc_converter.resources import asset_path
from md_doc_converter.toolchain.checks import render_problems
from md_doc_converter.toolchain.environment import Toolchain, render_environment, toolchain

PDF_PRINTER = Path(__file__).with_name("pagedjs-print.mjs")
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
    return Path(os.path.abspath(path.expanduser()))


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
    if selected.is_symlink():
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
    return text


def page_styles(header: str = "", footer: str = "", extra_css: str = "") -> str:
    """Inline the shared overrides, page text and extra CSS after the linked stylesheets."""
    try:
        text_css = page_css(header, footer)
    except ValueError as error:
        raise RenderError(f"Invalid header or footer text: {error}") from error
    parts = [asset_path("style-overrides.css").read_text(encoding="utf-8"), text_css, extra_css]
    body = "\n".join(part.strip("\n") for part in parts if part.strip())
    return f"<style>\n{body}\n</style>\n"


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

    return in_git_work_tree(str(source))


def _keep(work: Path, directory: Path) -> None:
    try:
        shutil.copytree(work, directory, dirs_exist_ok=True)
    except OSError as error:
        print(
            f"md-doc-converter: cannot keep intermediates in {directory}: {error}", file=sys.stderr
        )
    else:
        print(f"md-doc-converter: intermediates kept in {directory}", file=sys.stderr)


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
            prepared = work / "markdown" / f"{name}.md"
            prepared.parent.mkdir()
            command = [
                sys.executable,
                "-m",
                "md_doc_converter.markdown.preprocess",
                "--input",
                str(source),
                "--output",
                str(prepared),
            ]
            if filter_gitignored:
                command.append("--respect-gitignore")
            subprocess.run(command, env=environment, check=True)

            styles_file = work / "page-styles.html"
            styles_file.write_text(styles, encoding="utf-8")
            staged_dir = work / "render"
            staged_dir.mkdir()
            html = staged_dir / f"{name}.html"
            subprocess.run(
                [
                    "pandoc",
                    "--defaults",
                    str(asset_path("pandoc-defaults.yaml")),
                    "--standalone",
                    "--include-in-header",
                    str(styles_file),
                    "-o",
                    str(html),
                    str(prepared),
                ],
                cwd=work,
                env=environment,
                check=True,
            )
            staged = html
            if pdf:
                staged = staged_dir / f"{name}.pdf"
                subprocess.run(
                    [
                        "node",
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
    command = [str(part) for part in error.cmd] if isinstance(error.cmd, list) else [str(error.cmd)]
    step = command[2] if command[1:2] == ["-m"] else Path(command[0]).name
    return f"{step} failed with exit status {error.returncode}"


def main(argv: list[str] | None = None, *, action: Literal["html", "pdf"]) -> int:
    output_format = OutputFormat(action)
    parser = argparse.ArgumentParser(
        prog=f"md-doc-converter md-to-{action}",
        description=(
            f"Render a Markdown file, or a folder of Markdown files combined in order, to "
            f"{action.upper()}. Prepared Markdown and other intermediates stay private."
        ),
    )
    parser.add_argument("input", type=Path, help="Markdown file or folder")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            f"Output file (default: <input name>.{action} next to a file input, "
            "or inside a folder input)"
        ),
    )
    parser.add_argument(
        "--header", help=f"Page header text, top right (default: ${HEADER_VARIABLE}, or none)"
    )
    parser.add_argument(
        "--footer", help=f"Page footer text, bottom left (default: ${FOOTER_VARIABLE}, or none)"
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
    if output_format is OutputFormat.PDF:
        parser.add_argument(
            "--pdf-timeout",
            metavar="SECONDS",
            help=(
                f"Limit for each browser step (default: ${PDF_TIMEOUT_VARIABLE}, or "
                f"{PDF_TIMEOUT_DEFAULT}; 0 = no limit)"
            ),
        )
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
    )
    try:
        destination = render_markdown(arguments.input, output_format, arguments.output, options)
    except subprocess.CalledProcessError as error:
        print(f"md-doc-converter: {_failed_step(error)}", file=sys.stderr)
        return 1
    except (RenderError, OSError) as error:
        print(f"md-doc-converter: {error}", file=sys.stderr)
        return 1
    print(f"Rendered {arguments.input} -> {destination}")
    return 0
