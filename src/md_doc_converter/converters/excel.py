"""
Convert each sheet in an Excel workbook to a separate CSV file.

For each sheet in the workbook, a CSV file is written as
``<sheet_name>.csv``.  Formulas are resolved to their
cached values (``data_only=True``).

Usage:
    md-doc-converter excel-to-csv workbook.xlsx
    md-doc-converter excel-to-csv workbook.xlsx -o out_dir/
"""

import argparse
import csv
import sys
from pathlib import Path


def excel_to_csv(excel_path: str, output_dir: str | None = None) -> None:
    import openpyxl

    path = Path(excel_path)
    if not path.exists():
        sys.exit(f"File not found: {excel_path}")

    out = Path(output_dir) if output_dir else path.parent
    out.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(path, data_only=True)

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        safe_name = sheet_name.replace("/", "_").replace("\\", "_")
        csv_path = out / f"{safe_name}.csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(row)

        print(f"Written: {csv_path}")

    wb.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter excel-to-csv", description="Convert Excel sheets to CSV files"
    )
    parser.add_argument("excel_file", help="Path to the Excel workbook (.xlsx)")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory (default: same as input file)",
    )
    args = parser.parse_args(argv)

    excel_to_csv(args.excel_file, args.output_dir)


if __name__ == "__main__":
    main()
