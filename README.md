# md-doc-converter

Convert Word, Excel and CSV documents to Markdown, and render Markdown to HTML
and PDF.

- **Word to Markdown:** starts from a [MarkItDown](https://github.com/microsoft/markitdown)
  conversion and restores what it loses from the Word source, such as images
  with their captions, tables, lists, links, bookmarks and heading levels.
  Optionally splits the document into wiki pages.
- **Excel to CSV and CSV to Markdown:** one CSV per worksheet, one Markdown
  table page per CSV, with cell contents kept literal.
- **Markdown to HTML and PDF:** one file or a whole folder, with Mermaid
  diagrams, KaTeX math, GitHub-style tables and code, heading bookmarks, page
  numbers and optional header and footer text. The HTML is self-contained.

## Requirements

- Linux x86_64.
- Python 3.11 to 3.14. uv installs one if needed.
- [Node.js](https://nodejs.org/) 22.13 or newer, with npm, for rendering.
- Optional: LibreOffice (`libreoffice` or `soffice`) and Poppler
  (`pdftocairo`) to convert EMF and WMF drawings in Word documents to SVG, and
  Git to skip Git-ignored files when converting or rendering a folder.

`md-doc-converter setup` installs the rest, including
[Pandoc](https://pandoc.org/) 3.12.1 for Word conversion and rendering; see
[Toolchain](#toolchain). Excel and CSV conversion need neither Pandoc nor
Node.js.

## Installation

Install the command with [uv](https://docs.astral.sh/uv/) from a checkout or a
built wheel:

```sh
uv tool install ./md-doc-converter
# or: uv tool install md_doc_converter-0.1.0-py3-none-any.whl
```

Install Node.js for rendering, and optionally LibreOffice and Poppler, from your
distribution's packages; [nvm](https://github.com/nvm-sh/nvm) also installs
Node.js without root. Then install the toolchain and check every prerequisite:

```sh
md-doc-converter setup
md-doc-converter doctor
```

`setup` installs Pandoc and, for rendering, the pinned Mermaid, KaTeX and
Puppeteer npm packages with the headless browser they share: about 270 MB of
downloads and 0.8 GB on disk. For Word conversion only, `setup --no-render`
installs just Pandoc, a 35 MB download, and needs no Node.js. See
[Toolchain](#toolchain).

## Quick start

```sh
md-doc-converter docx-to-md docs/spec.docx      # docs/spec.md and docs/.images/spec/
md-doc-converter md-to-html docs/spec.md        # docs/spec.html
md-doc-converter md-to-pdf docs/spec.md --header "Design review"   # docs/spec.pdf
md-doc-converter md-to-pdf docs                 # docs/docs.pdf, from every page in docs/
md-doc-converter excel-to-csv data/pins.xlsx    # one CSV per worksheet, beside the workbook
md-doc-converter csv-to-md data                 # one Markdown page beside each CSV
```

Every command writes next to its input unless `-o` selects another location,
and never changes its input. `md-doc-converter COMMAND --help` lists a
command's options.

| Command | Purpose |
| --- | --- |
| `docx-to-md DOCX [-o FILE.md]` | Convert a Word document to Markdown with its images; see [Word conversion](docs/word-conversion.md) |
| `excel-to-csv WORKBOOK [-o DIR]` | Write each worksheet as `<sheet name>.csv` |
| `csv-to-md PATH [-o DIR] [--dry-run] [--delimiter NAME]` | Convert a CSV file, or every CSV in a folder, to Markdown |
| `md-to-html INPUT [-o FILE.html]` | Render a Markdown file or folder to standalone HTML; see [Rendering](docs/rendering.md) |
| `md-to-pdf INPUT [-o FILE.pdf]` | Render a Markdown file or folder to a paged PDF |
| `setup [--no-render] [--downloads DIR] [--force]` | Install the pinned toolchain; `--no-render` installs only Pandoc |
| `doctor` | Check the prerequisites and show how to fix what is missing |

## Excel and CSV

`excel-to-csv` writes each worksheet as `<sheet name>.csv` beside the workbook,
or in the `-o` folder. Like Word documents, a workbook that is encrypted, in the
Excel 97-2003 format (`.xls`), or whose contents expand to more than 1,024 MiB
is refused. Formulas are written as the values Excel saved with them;
a workbook saved by a program that does not calculate formulas, such as a
script, has none, and those cells come out empty. For clean output, each
worksheet should:

- contain one table beginning in the first row and first column;
- contain no populated cells outside the table;
- use the desired output name, conventionally `##-File-Name`.

`csv-to-md` converts one CSV file, or every CSV in a folder and its subfolders
(skipping Git-ignored files when the folder is in a Git work tree). Each page
has a heading from the file name, with hyphens as spaces, a note that it is
generated, and the table. Pages are written beside each CSV unless `-o` is set;
for a folder, `-o` keeps the relative subfolders. Short rows are padded; rows
wider than the header fail instead of silently dropping cells.

CSV files are read as UTF-8, with or without the byte-order mark that Excel's
"CSV UTF-8" format writes. Each file's delimiter is detected from its header
line: a comma, or a semicolon or tab, as Excel writes in some languages and
regions. `--delimiter comma`, `semicolon` or `tab` selects one for every file.

Cell contents stay literal: backslashes, pipes, HTML-like text, entity names,
code delimiters, and Markdown punctuation remain text, not formatting, and so do
a citation-like `@name`, which Pandoc's Markdown reads as a citation, and an
emoji code such as `:smile:`, which GitHub shows as an emoji. Identifiers such
as `_ctl_s_ready` and e-mail addresses stay readable. Cell newlines become
`<br>`, surrounding whitespace is trimmed, and tabs become spaces.

After conversion, check empty rows and columns and the file names. It is usually
easiest to correct problems in the workbook and convert again. Regenerate the
Markdown whenever a CSV changes; wikis render the Markdown table, not the CSV.

## Word documents

```sh
md-doc-converter docx-to-md docs/spec.docx
md-doc-converter docx-to-md docs/spec.docx --split-depth 1   # one wiki page per section
md-doc-converter docx-to-md docs/spec.docx --dry-run --explain
```

Conversion reports each transform it applied and each item it could not carry
over, without quoting the document in the summary. See
[Word conversion](docs/word-conversion.md) for the transforms, the report,
options, and the replacement rules for existing output.

## Rendering

```sh
md-doc-converter md-to-pdf docs/spec.md -o "reviews/spec review.pdf"
md-doc-converter md-to-pdf docs --header "Design review" --footer "Draft"
md-doc-converter md-to-html docs/spec.md --extra-css branding.css
```

A folder input is combined into one document in name order, or in the order an
Azure DevOps Wiki `.order` file gives. Rendering writes only the requested file
and replaces an existing one only after it succeeds. Prepared Markdown and
other intermediates stay private unless `--keep-intermediates DIR` asks to keep
them. See [Rendering](docs/rendering.md) for output naming, folder input,
Mermaid diagrams, math, the page header and footer, extra stylesheets, the PDF
time limit and table styling.

## Settings

Command-line options override these environment variables.

| Variable | Purpose |
| --- | --- |
| `MD_DOC_CONVERTER_HOME` | Toolchain directory (default `~/.local/share/md-doc-converter`) |
| `MD_DOC_CONVERTER_HEADER` | Default `--header` text |
| `MD_DOC_CONVERTER_FOOTER` | Default `--footer` text |
| `MD_DOC_CONVERTER_PDF_TIMEOUT` | Default `--pdf-timeout`, in seconds (600; `0` = no limit) |

## Toolchain

`setup` installs into `$MD_DOC_CONVERTER_HOME`, by default
`$XDG_DATA_HOME/md-doc-converter` or `~/.local/share/md-doc-converter`:

- `bin/pandoc`: Pandoc 3.12.1, the version md-doc-converter is tested with,
  from its GitHub release, verified against a pinned SHA-256 checksum (a 35 MB
  download, 158 MB on disk);
- `node/`: the npm packages from the lockfile bundled with md-doc-converter,
  installed with `npm ci --ignore-scripts`, since none needs its install
  scripts (about 110 MB of downloads, 400 MB on disk);
- `puppeteer/`: chrome-headless-shell, the build of headless Chrome that the
  locked Puppeteer pins, which Mermaid and the PDF printer share (a 126 MB
  download, 273 MB on disk).

`setup --no-render` installs only Pandoc.

md-doc-converter looks in the toolchain's `bin/` first, so it uses this Pandoc
even when another one is installed. Without it, md-doc-converter uses the
Pandoc on `PATH`, and `doctor` warns when that is another version, which may
format some converted Markdown slightly differently.

`setup` skips what is already current, and reinstalls the npm packages and the
browser when a new md-doc-converter version changes them. `setup --force`
reinstalls everything. To remove the toolchain, delete its directory; to remove
the command, run `uv tool uninstall md-doc-converter`.

npm uses your npm configuration, including proxies and registry mirrors. On a
host that cannot reach GitHub, download `pandoc-3.12.1-linux-amd64.tar.gz` from
the [Pandoc 3.12.1 release](https://github.com/jgm/pandoc/releases/tag/3.12.1)
another way into a folder and pass that folder to `setup`, which still verifies
the archive's checksum:

```sh
md-doc-converter setup --downloads ~/downloads
```

## Network access

Only `setup` downloads software: Pandoc from GitHub, npm packages from your npm
registry, and chrome-headless-shell through Puppeteer from Google's Chrome for
Testing downloads. Conversion and rendering run locally and do not upload
documents. Rendering downloads only the images that a document references by
URL, to embed them. Rendered HTML embeds its images, stylesheets and fonts, so
opening it, or printing it to PDF, needs no network.

## Troubleshooting

- Run `md-doc-converter doctor` first; it names each missing prerequisite and
  how to install it.
- If the browser cannot start, install the shared libraries that Chrome needs
  on your distribution. On hosts that restrict unprivileged user namespaces,
  such as Ubuntu 23.10 and later, Chrome's sandbox needs that restriction
  lifted (`kernel.apparmor_restrict_unprivileged_userns=0`).
- If a large PDF times out, raise `--pdf-timeout`, or set it to `0`.

## Development

See [Development](docs/development.md) for the layout, the uv and make
workflow, the test suites and continuous integration.
