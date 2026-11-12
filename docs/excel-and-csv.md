# Excel and CSV conversion

```sh
md-doc-converter excel-to-md data/pins.xlsx    # data/<sheet name>.md for each worksheet
md-doc-converter excel-to-csv data/pins.xlsx   # data/<sheet name>.csv for each worksheet
md-doc-converter csv-to-md data                # a page beside each CSV in data/ and its subfolders
```

Each worksheet or CSV file becomes a Markdown page that holds its table, written
beside the input unless `-o` names a folder. Each command lists the files it
writes; with `--dry-run`, `excel-to-md` and `csv-to-md` list them without
writing. `--help` lists each command's options. Excel and CSV conversion need
neither Pandoc nor Node.js.

## Workbooks

`excel-to-md` writes each worksheet as a page, `<sheet name>.md`.
`excel-to-csv` writes `<sheet name>.csv` instead, from which `csv-to-md` writes
the same page. The empty rows and columns around the cells that hold text are
left out, so the first row with text is the table's header, and empty
worksheets and chart sheets are skipped. Formulas are written as the values
Excel saved with them; a workbook saved by a program that does not calculate
formulas, such as a script, has none, and those cells come out empty.

Each cell is written as Excel shows it in a column wide enough for it, as
Excel's own CSV export writes it: `TRUE` and `FALSE`, and a number in its number
format. The General format shows at most 11 characters besides a minus sign, so
`0.1+0.2` shows as `0.3`, `31/3` as `10.33333333`, and `123456789012` as
`1.23457E+11`. Digits with decimals, a thousands separator, and a percent sign
or an exponent, as in `#,##0.00`, `0%` and `0.00E+00`, show as in Excel; other
number formats, such as ones with text or a section for negative numbers, show
their numbers as General does. Dates and times are written as ISO 8601 writes
them, such as `2026-11-10 09:30`, whatever their format.

A workbook that is encrypted, in the Excel 97-2003 format (`.xls`), or whose
contents expand to more than 1,024 MiB is refused. On Windows, a workbook with
a worksheet that cannot name a file there, such as `a<b` or `CON`, is refused
before any file is written.

For clean output, each worksheet should:

- contain one table, whose first row is its header;
- contain no populated cells outside the table;
- be named for the page it becomes: a worksheet `Pin-Assignments` becomes
  `Pin-Assignments.md`, titled "Pin Assignments".

## CSV files

`csv-to-md` converts one CSV file, or every file in a folder and its subfolders
whose name ends in `.csv`, in any case; for a folder, `-o` keeps the relative
subfolders. As in a [folder that is rendered](rendering.md#folder-input),
entries whose names begin with `.` are skipped, and so are Git-ignored files,
unless Git ignores the folder itself. Each page has a heading from the file
name, with hyphens as spaces, a note that it is generated, and the table,
without the empty rows and columns around its text. Short rows are padded; rows
wider than the header fail instead of silently dropping cells.

CSV files are read as UTF-8, with or without the byte-order mark that Excel's
"CSV UTF-8" format writes. Each file's delimiter is detected from its header
line: a comma, or a semicolon or tab, as Excel writes in some languages and
regions. `--delimiter` selects one for every file.

## Cell contents

Cell contents and page titles stay literal: backslashes, pipes, HTML-like text,
entity names, code delimiters, and Markdown punctuation remain text, not
formatting, and so do a citation-like `@name`, which Pandoc's Markdown reads as
a citation, and an emoji code such as `:smile:`, which GitHub shows as an emoji.
Identifiers such as `_ctl_s_ready` and e-mail addresses stay readable. Cell
newlines become `<br>`, surrounding whitespace is trimmed, and tabs become
spaces.

## After converting

Check empty rows and columns and the file names. It is usually easiest to
correct problems in the workbook and convert again. Regenerate the Markdown
whenever the workbook or a CSV changes, since readers see the Markdown, not the
workbook.
