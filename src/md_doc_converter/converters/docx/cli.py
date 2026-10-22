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
            "Convert DOCX with MarkItDown, then layer on the Word structure that Pandoc "
            "recovers; anything that cannot be applied is reported and left as MarkItDown made it"
        ),
        epilog="Publication is recoverable per-path replacement, not multi-artifact atomicity.",
    )
    parser.add_argument("input", type=Path, help="Source DOCX file (never modified)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Markdown output (default: <provided-input-parent>/<provided-input-stem>.md)",
    )
    parser.add_argument(
        "--split-depth",
        type=int,
        choices=range(7),
        default=0,
        metavar="0..6",
        help="Split headings through this source depth into Wiki pages (default: 0)",
    )
    parser.add_argument(
        "--qualify-duplicate-headings",
        action="store_true",
        help="Qualify duplicate titles without changing heading levels or punctuation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert and check in scratch; leave all final outputs unchanged",
    )
    parser.add_argument(
        "--replace-generated",
        action="store_true",
        help="Permit replacing nonempty derived directories and existing derived Markdown",
    )
    parser.add_argument(
        "--keep-intermediates",
        type=Path,
        metavar="DIR",
        help="Retain a diagnostic bundle in a new/empty nonoverlapping directory, even in dry-run",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help=(
            "After the transform summary, describe what the items each transform did not apply "
            "have in common, with fixed labels and counts only (no document text)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from md_doc_converter.converters.docx import pipeline as _pipeline
    from md_doc_converter.converters.docx.diagnostics import TransformReport

    report = TransformReport(explaining=args.explain)
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
        )
    except (ConversionError, OSError) as error:
        print(f"docx-to-md: error: {error}", file=sys.stderr)
        return 1
    for line in report.summary():
        print(line, file=sys.stderr)
    for line in report.explanation():
        print(line, file=sys.stderr)
    print(
        f"{'validated' if args.dry_run else 'converted'} {args.input} -> "
        f"{_publication.requested_output(args.input, args.output)}: {stats['pages']} page(s), "
        f"{stats['tables']} table(s), {stats['image_occurrences']} image reference(s), "
        f"{stats['bookmarks']} Word bookmark(s)"
    )
    return 0
