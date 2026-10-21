"""Convert Word, Excel and CSV documents to Markdown, and render Markdown to HTML and PDF."""

from __future__ import annotations

import argparse
import sys

from md_doc_converter.resources import package_version

COMMANDS = {
    "excel-to-csv": "write each worksheet of an Excel workbook as a CSV file",
    "csv-to-md": "convert CSV files to Markdown tables",
    "docx-to-md": "convert a Word document to Markdown, with its images",
    "md-to-html": "render Markdown to a standalone HTML file",
    "md-to-pdf": "render Markdown to a paged PDF",
    "setup": "install the renderer toolchain (Mermaid, Paged.js, pandoc-katex)",
    "doctor": "check the prerequisites for conversion and rendering",
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    width = max(len(name) for name in COMMANDS)
    parser = argparse.ArgumentParser(
        prog="md-doc-converter",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="commands:\n"
        + "\n".join(f"  {name:<{width}}  {summary}" for name, summary in COMMANDS.items())
        + "\n\nRun `md-doc-converter COMMAND --help` for a command's options.",
    )
    parser.add_argument(
        "--version", action="version", version=f"md-doc-converter {package_version()}"
    )
    parser.add_argument(
        "command", choices=COMMANDS, metavar="COMMAND", help="one of the commands listed below"
    )
    if not arguments:
        parser.print_help()
        return 0
    command = parser.parse_args(arguments[:1]).command
    remaining = arguments[1:]
    if command == "excel-to-csv":
        from md_doc_converter.converters.excel import main as excel_to_csv

        excel_to_csv(remaining)
        return 0
    if command == "csv-to-md":
        from md_doc_converter.converters.csv import main as csv_to_md

        csv_to_md(remaining)
        return 0
    if command == "docx-to-md":
        from md_doc_converter.converters.docx.cli import main as docx_to_md

        return docx_to_md(remaining)
    if command in {"md-to-html", "md-to-pdf"}:
        from md_doc_converter.rendering.pipeline import main as render

        return render(remaining, action="html" if command == "md-to-html" else "pdf")
    if command == "setup":
        from md_doc_converter.toolchain.install import main as setup

        return setup(remaining)
    from md_doc_converter.toolchain.checks import main as doctor

    return doctor(remaining)


if __name__ == "__main__":
    sys.exit(main())
