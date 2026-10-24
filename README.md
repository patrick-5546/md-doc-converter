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
- Python 3.15. uv installs it if needed; on a host that cannot reach GitHub,
  where uv downloads Python from, install Python 3.15 another way.
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
# or: uv tool install md_doc_converter-*.whl
```

On a network that allows only a package mirror, see
[Package mirrors](#package-mirrors).

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
and never changes its input.

## Commands

`md-doc-converter --help` lists the commands, and
`md-doc-converter COMMAND --help` a command's options. What each command does
is described in:

- [Word conversion](docs/word-conversion.md), for `docx-to-md`: what the
  Markdown holds, the report, pages, and converting again;
- [Excel and CSV conversion](docs/excel-and-csv.md), for `excel-to-md`,
  `excel-to-csv` and `csv-to-md`: how worksheets and CSV files become pages,
  and what makes clean output;
- [Rendering](docs/rendering.md), for `md-to-html` and `md-to-pdf`: output
  names, folder input, how Markdown is read, images from elsewhere, Mermaid
  diagrams, math, the page header and footer, extra stylesheets, the PDF time
  limit and table styling;
- [Installation](#installation) and [Toolchain](#toolchain), for `setup` and
  `doctor`.

## Messages

`docx-to-md`, `md-to-html` and `md-to-pdf` warn about what they cannot carry
over or show as written, naming the file and, when it is known, the line:

```text
md-to-html: warning: docs/setup.md: broken anchor '#instal' (no matching heading)
```

They show the first three warnings of each kind and count the rest. `-v`,
`--verbose` shows every warning and the details of each step; `-q`, `--quiet`
shows only errors. The result, the last line, goes to standard output and the
rest to standard error, so `--verbose 2> conversion.log` keeps the whole report.
On a terminal, a status line names the running step, and warnings and errors are
colored unless `NO_COLOR` is set.

## Settings

Command-line options override these environment variables.

| Variable | Purpose |
| --- | --- |
| `MD_DOC_CONVERTER_HOME` | Toolchain directory (default `~/.local/share/md-doc-converter`, or `%LOCALAPPDATA%\md-doc-converter` on Windows) |
| `MD_DOC_CONVERTER_HEADER` | Default `--header` text |
| `MD_DOC_CONVERTER_FOOTER` | Default `--footer` text |
| `MD_DOC_CONVERTER_PDF_TIMEOUT` | Default `--pdf-timeout`, in seconds (600; `0` = no limit) |

## Toolchain

`setup` installs into `$MD_DOC_CONVERTER_HOME`, by default
`$XDG_DATA_HOME/md-doc-converter` or `~/.local/share/md-doc-converter`, and on
Windows `%LOCALAPPDATA%\md-doc-converter`:

- `bin/pandoc` (`bin\pandoc.exe` on Windows): Pandoc 3.12.1, the version
  md-doc-converter is tested with, from its GitHub release, verified against a
  pinned SHA-256 checksum;
- `node/`, for rendering: the Mermaid, KaTeX and Puppeteer npm packages from
  the lockfile bundled with md-doc-converter, which holds only releases at
  least a week old, installed without their install scripts or optional
  packages, which none needs;
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

npm uses your npm configuration, including proxies and registry mirrors; see
[Package mirrors](#package-mirrors). On a
host that cannot reach GitHub, download the archive for your platform,
`pandoc-3.12.1-linux-amd64.tar.gz` or `pandoc-3.12.1-windows-x86_64.zip`, from
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

## Package mirrors

On a network that allows only a package mirror, such as an organization's proxy
of PyPI and npm, set up each package manager for it:

- uv, which installs md-doc-converter, downloads from PyPI. It does not read
  pip's settings, such as `PIP_INDEX_URL` or pip's configuration file, so name
  the mirror's PyPI index in `UV_DEFAULT_INDEX` as you install:

  ```sh
  UV_DEFAULT_INDEX=https://mirror.example/pypi/simple/ uv tool install ./md-doc-converter
  ```

- npm, which `setup` runs, downloads from its configured registry, which may be
  the mirror already. It fetches the locked packages from there, though the
  bundled lockfile names `registry.npmjs.org`:

  ```sh
  npm config get registry
  npm config set registry https://mirror.example/npm/
  ```

Some mirrors hold a new release for a week while it is checked. The bundled npm
lockfile holds only releases at least a week old, so such a mirror has every
package it names, and `uv tool install` takes the newest release of openpyxl,
md-doc-converter's one Python dependency, that the mirror has. For a checkout,
see [Package mirrors](docs/development.md#package-mirrors) in the development
guide.

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
