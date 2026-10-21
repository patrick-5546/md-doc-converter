"""
Convert CSV files to Markdown pages, each with a heading and one table.

Each CSV produces a ``.md`` file with:
  - An H1 heading derived from the filename (hyphens become spaces).
  - A Markdown table whose first row is the header.

The input may be a single ``.csv`` file or a directory that is searched
recursively. Output files are written next to the source CSVs. When
``--output-dir`` is given for a directory, source subdirectories are preserved
under the selected output directory.

Usage:
    md-doc-converter csv-to-md data.csv
    md-doc-converter csv-to-md data.csv -o out/
    md-doc-converter csv-to-md path/to/csvs/ -o out/ --dry-run
"""

import argparse
import csv
import subprocess
import sys
from pathlib import Path

from md_doc_converter.markdown.tables import literal_cell as _cell
from md_doc_converter.markdown.tables import table_row as _row


def _title_from_stem(stem: str) -> str:
    """Derive a human-readable title from a filename stem."""
    return stem.replace("-", " ")


def csv_to_md(csv_path: Path) -> str:
    """Return the Markdown string for a single CSV file."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return ""

    title = _title_from_stem(csv_path.stem)
    header = rows[0]
    data = rows[1:]

    lines: list[str] = [
        f"# {title}",
        "",
        (
            f"> Generated from {csv_path.name} using md-doc-converter (csv-to-md) "
            "— do not manually modify"
        ),
        "",
        _row([_cell(h) for h in header]),
        _row(["---" for _ in header]),
    ]

    for number, row in enumerate(data, 2):
        if len(row) > len(header):
            raise ValueError(
                f"{csv_path}: row {number} has {len(row)} cells but the header has "
                f"{len(header)}; refusing to discard data"
            )
        cells = row + [""] * (len(header) - len(row))
        lines.append(_row([_cell(cell) for cell in cells]))

    lines.append("")  # trailing newline
    return "\n".join(lines)


def convert(
    input_path: str,
    output_dir: str | None = None,
    dry_run: bool = False,
) -> None:
    src = Path(input_path)
    if not src.exists():
        sys.exit(f"Not found: {input_path}")

    if src.is_dir():
        # Use git ls-files to respect .gitignore
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.csv"],
                cwd=src,
                capture_output=True,
                text=True,
                check=True,
            )
            csv_files = sorted(src / line for line in result.stdout.splitlines() if line)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fall back to rglob if git is unavailable
            csv_files = sorted(src.rglob("*.csv"))
    else:
        csv_files = [src]

    if not csv_files:
        print("No CSV files found.")
        return

    for csv_path in csv_files:
        md_text = csv_to_md(csv_path)
        if not md_text:
            print(f"Skipped (empty): {csv_path}")
            continue

        out = Path(output_dir) if output_dir else csv_path.parent
        if output_dir and src.is_dir():
            out /= csv_path.parent.relative_to(src)
        md_path = out / f"{csv_path.stem}.md"

        if dry_run:
            print(f"Would write: {md_path}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_text, encoding="utf-8")
            print(f"Written: {md_path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter csv-to-md",
        description="Convert CSV files to Markdown tables",
    )
    parser.add_argument(
        "input",
        help="Path to a .csv file or a directory to search recursively",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory (default: same as each input file)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without creating files",
    )
    args = parser.parse_args(argv)

    try:
        convert(args.input, args.output_dir, args.dry_run)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        sys.exit(f"csv-to-md: error: {error}")


if __name__ == "__main__":
    main()
