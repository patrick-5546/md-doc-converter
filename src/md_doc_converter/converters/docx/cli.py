"""Command-line options and error reporting for DOCX conversion."""

from __future__ import annotations

import argparse
from pathlib import Path

from md_doc_converter.console import Console, activated, add_verbosity_options, plural, shown
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
    parser.add_argument(
        "input", type=Path, metavar="DOCX", help="Word document (.docx), which is not changed"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="FILE.md",
        help=(
            "Markdown file to write, with the images in .images/<name>/ beside it (default: the "
            "input's name with .md, beside it)"
        ),
    )
    parser.add_argument(
        "--split-depth",
        type=int,
        choices=range(7),
        default=0,
        metavar="0..6",
        help=(
            "Write a page for each heading of levels 1 to this, in a folder named like the "
            "output, which lists them (default: 0, one file)"
        ),
    )
    parser.add_argument(
        "--qualify-duplicate-headings",
        action="store_true",
        help=(
            "Distinguish repeated headings by the headings of the sections that hold them, "
            "keeping their levels"
        ),
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
            "those, such as Azure DevOps Wiki (default: github)"
        ),
    )
    parser.add_argument(
        "--keep-intermediates",
        type=Path,
        metavar="DIR",
        help=(
            "Keep the intermediate files, such as the copy that Pandoc reads, and a "
            "diagnostics.json report in a new or empty directory apart from the input and "
            "outputs, also with --dry-run"
        ),
    )
    add_verbosity_options(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from md_doc_converter.converters.docx import pipeline as _pipeline
    from md_doc_converter.converters.docx.diagnostics import TransformReport

    console = Console("docx-to-md", args.verbosity)
    report = TransformReport(console=console)
    console.note(f"converting {shown(args.input)}")
    with activated(console):
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
            console.finish()
            console.error(str(error))
            return 1
    if console.verbosity == "verbose":
        console.block(report.summary())
    console.block(report.issues())
    console.finish(counted=True)
    output = _publication.requested_output(args.input, args.output)
    counts = (
        plural(stats["pages"], "page"),
        plural(stats["tables"], "table"),
        plural(stats["image_occurrences"], "image reference"),
        plural(stats["bookmarks"], "Word bookmark"),
    )
    console.result(
        f"{'validated' if args.dry_run else 'converted'} {shown(args.input)} -> "
        f"{shown(output)}: {', '.join(counts)}"
    )
    return 0
