# md-doc-converter

Convert Word, Excel and CSV documents to Markdown, and render Markdown to HTML
and PDF.

- **Word to Markdown:** converts with [Pandoc](https://pandoc.org/), keeping
  images with their captions as alt text, tables, lists, links, bookmarks,
  footnotes, equations and heading levels as Markdown can show them.
  Optionally splits the document into pages.
- **Excel and CSV to Markdown:** a table page per worksheet or CSV file, with
  cell contents kept literal.
- **Markdown to HTML and PDF:** one file or a whole folder, with Mermaid
  diagrams, KaTeX math, GitHub-style tables and code, heading bookmarks, page
  numbers and optional header and footer text. The HTML is self-contained.

## Requirements

- Linux x86_64.
- Python 3.11 to 3.15. uv installs one if needed.
- [Node.js](https://nodejs.org/) 22.13 or newer, with npm, for rendering.
- Optional: LibreOffice (`libreoffice` or `soffice`) and Poppler
  (`pdftocairo`) to convert EMF and WMF drawings in Word documents to SVG, and
  Git to skip Git-ignored files when converting or rendering a folder.

`md-doc-converter setup` installs the rest, including Pandoc for Word
conversion and rendering; see [Toolchain](#toolchain). Excel and CSV conversion
need neither Pandoc nor Node.js.

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

For Word conversion only, `setup --no-render` installs just Pandoc and needs no
Node.js.

## Quick start

```sh
md-doc-converter docx-to-md docs/spec.docx      # docs/spec.md and docs/.images/spec/
md-doc-converter md-to-html docs/spec.md        # docs/spec.html
md-doc-converter md-to-pdf docs/spec.md --header "Design review"   # docs/spec.pdf
md-doc-converter md-to-pdf docs                 # docs/docs.pdf, from every page in docs/
md-doc-converter excel-to-md data/pins.xlsx     # one Markdown page per worksheet, beside it
md-doc-converter csv-to-md data                 # one Markdown page beside each CSV
```

Every command writes next to its input unless `-o` selects another location,
and never changes its input. `md-doc-converter COMMAND --help` lists a
command's options.

| Command | Purpose |
| --- | --- |
| `docx-to-md DOCX [-o FILE.md]` | Convert a Word document to Markdown with its images; see [Word conversion](docs/word-conversion.md) |
| `excel-to-md WORKBOOK [-o DIR] [--dry-run]` | Convert each worksheet to a Markdown page, `<sheet name>.md`; see [Excel and CSV conversion](docs/excel-and-csv.md) |
| `excel-to-csv WORKBOOK [-o DIR]` | Write each worksheet as `<sheet name>.csv` |
| `csv-to-md PATH [-o DIR] [--dry-run] [--delimiter NAME]` | Convert a CSV file, or every CSV in a folder, to Markdown |
| `md-to-html INPUT [-o FILE.html]` | Render a Markdown file or folder to standalone HTML; see [Rendering](docs/rendering.md) |
| `md-to-pdf INPUT [-o FILE.pdf]` | Render a Markdown file or folder to a paged PDF |
| `setup [--no-render] [--downloads DIR] [--force]` | Install the pinned toolchain; see [Toolchain](#toolchain) |
| `doctor` | Check the prerequisites and show how to fix what is missing |

## Guides

- [Word conversion](docs/word-conversion.md): what the Markdown holds, the
  report, the options, and converting again.
- [Excel and CSV conversion](docs/excel-and-csv.md): how worksheets and CSV
  files become pages, and what makes clean output.
- [Rendering](docs/rendering.md): output names, folder input, how Markdown is
  read, images from elsewhere, Mermaid diagrams, math, the page header and
  footer, extra stylesheets, the PDF time limit and table styling.

## Messages

`docx-to-md`, `md-to-html` and `md-to-pdf` warn about what they cannot carry
over or show as written, naming the file and, when it is known, the line:

```text
md-to-html: warning: docs/setup.md: broken anchor '#instal' (no matching heading)
```

They show the first three warnings of each kind and count the rest. `-v`,
`--verbose` shows every warning and the details of each step; `-q`, `--quiet`
shows only errors. The result, the last line, goes to standard output and the
rest to standard error, so `--verbose 2> conversion.log` keeps the whole
report. On a terminal, a status line names the running step, and warnings and
errors are colored unless `NO_COLOR` is set. The Excel and CSV commands list
each file they write.

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
  from its GitHub release, verified against a pinned SHA-256 checksum;
- `node/`, for rendering: the Mermaid, KaTeX and Puppeteer npm packages from
  the lockfile bundled with md-doc-converter, installed without their install
  scripts or optional packages, which none needs;
- `puppeteer/`, for rendering: chrome-headless-shell, the build of headless
  Chrome that the locked Puppeteer pins, which Mermaid and the PDF printer
  share.

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
Testing downloads. Conversion and rendering run locally and upload nothing.
Rendering downloads only the files that your `--extra-css` stylesheet names by
URL and, with `--download-images`, the images that a document shows by URL. It
is safe for documents from elsewhere; see
[Images and documents from elsewhere](docs/rendering.md#images-and-documents-from-elsewhere).
Rendered HTML embeds its images, stylesheets and fonts, so opening it, or
printing it to PDF, needs no network.

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
