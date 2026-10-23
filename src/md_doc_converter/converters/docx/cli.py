"""Command-line options and error reporting for DOCX conversion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx.models import ConversionError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter docx-to-md",
        description=(
            "Convert a Word document to Markdown, with its images, and report what could not "
            "be converted as Word shows it"
        ),
    )
    parser.add_argument("input", type=Path, help="Word document (.docx), which is not changed")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Markdown file to write (default: the input's name with .md, beside it)",
    )
    parser.add_argument(
        "--split-depth",
        type=int,
        choices=range(7),
        default=0,
        metavar="0..6",
        help=(
            "Write a page for each heading of levels 1 to this, in a folder named like the "
            "output (default: 0, one file)"
        ),
    )
    parser.add_argument(
        "--qualify-duplicate-headings",
        action="store_true",
        help="Distinguish repeated headings by their context, keeping their levels",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert and check without writing the Markdown, images or pages",
    )
    parser.add_argument(
        "--replace-generated",
        action="store_true",
        help=(
            "Allow replacing an existing Markdown file at the default output, and nonempty "
            "images and pages folders"
        ),
    )
    parser.add_argument(
        "--math",
        choices=("github", "dollars"),
        default="github",
        help=(
            "How to write equations: github, as $`x`$ and ```math blocks, which GitHub, GitLab "
            "and VS Code show; or dollars, as $x$ and $$x$$, for renderers that read only "
            "those (default: github)"
        ),
    )
    parser.add_argument(
        "--keep-intermediates",
        type=Path,
        metavar="DIR",
        help=(
            "Keep the intermediate files and a diagnostic report in a new or empty directory "
            "apart from the input and outputs, also with --dry-run"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from md_doc_converter.converters.docx import pipeline as _pipeline
    from md_doc_converter.converters.docx.diagnostics import TransformReport

    report = TransformReport()
    try:
        stats = _pipeline.convert_docx(
            args.input,
            args.output,
            split_depth=args.split_depth,
            qualify_duplicate_headings=args.qualify_duplicate_headings,
            dry_run=args.dry_run,
            replace_generated=args.replace_generated,
            keep_intermediates=args.keep_intermediates,
            report=report,
            math=args.math,
        )
    except (ConversionError, OSError) as error:
        print(f"docx-to-md: error: {error}", file=sys.stderr)
        return 1
    for line in report.summary():
        print(line, file=sys.stderr)
    print(
        f"{'validated' if args.dry_run else 'converted'} {args.input} -> "
        f"{_publication.requested_output(args.input, args.output)}: {stats['pages']} page(s), "
        f"{stats['tables']} table(s), {stats['image_occurrences']} image reference(s), "
        f"{stats['bookmarks']} Word bookmark(s)"
    )
    return 0
