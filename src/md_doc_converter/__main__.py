"""Convert Word, Excel and CSV documents to Markdown, and render Markdown to HTML and PDF."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from md_doc_converter.resources import package_version
from md_doc_converter.toolchain.environment import prefer_installed_tools

COMMANDS = {
    "excel-to-md": "convert each worksheet of an Excel workbook to a Markdown page",
    "excel-to-csv": "write each worksheet of an Excel workbook as a CSV file",
    "csv-to-md": "convert CSV files to Markdown tables",
    "docx-to-md": "convert a Word document to Markdown, with its images",
    "md-to-html": "render Markdown to a standalone HTML file",
    "md-to-pdf": "render Markdown to a paged PDF",
    "setup": "install the toolchain (Pandoc; for rendering Mermaid, KaTeX, Puppeteer)",
    "doctor": "check the prerequisites for conversion and rendering",
}


class _VersionAction(argparse.Action):
    """Print the version and exit, as argparse's version action does.

    Unlike that action, it looks the version up only then: every command would otherwise wait
    for the lookup, which is slow on Windows.
    """

    def __init__(self, option_strings: Sequence[str], dest: str) -> None:
        super().__init__(
            option_strings,
            dest,
            nargs=0,
            default=argparse.SUPPRESS,
            help="show program's version number and exit",
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        print(f"md-doc-converter {package_version()}")
        parser.exit()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Conversion and rendering then use the Pandoc that `setup` pinned, when it is installed.
    prefer_installed_tools()
    width = max(len(name) for name in COMMANDS)
    parser = argparse.ArgumentParser(
        prog="md-doc-converter",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="commands:\n"
        + "\n".join(f"  {name:<{width}}  {summary}" for name, summary in COMMANDS.items())
        + "\n\nRun `md-doc-converter COMMAND --help` for a command's options.",
    )
    parser.add_argument("--version", action=_VersionAction)
    parser.add_argument(
        "command", choices=COMMANDS, metavar="COMMAND", help="one of the commands listed below"
    )
    if not arguments:
        parser.print_help()
        return 0
    command = parser.parse_args(arguments[:1]).command
    remaining = arguments[1:]
    if command in {"excel-to-md", "excel-to-csv"}:
        from md_doc_converter.converters import excel

        (excel.markdown_main if command == "excel-to-md" else excel.csv_main)(remaining)
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
