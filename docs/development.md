# Development

## Package layout

```text
src/md_doc_converter/
  __main__.py               # command-line entry point with lazy command dispatch
  resources.py, pandoc.py   # packaged file paths, version, typed Pandoc parsing
  media_types.py            # media types of the images and fonts that rendering embeds
  download.py               # downloads of images and stylesheet files, with limits
  markdown/                 # preprocessing, headings, code regions, literal cells
  converters/
    excel.py, csv.py
    docx/                   # responsibility-based Word conversion stages
  rendering/                # HTML/PDF orchestration, page text CSS, PDF printer
  toolchain/                # setup, doctor, toolchain locations and environment
    package.json, package-lock.json   # pinned npm packages installed by setup
  assets/                   # stylesheets, HTML fragments, Pandoc/Mermaid config, Lua filters
tests/
  conftest.py               # the suites' policies (tests/support/suites.py), shared fixtures
  support/                  # synthetic document builders, fake native tools, suite policies
  unit/                     # mirrors runtime responsibilities
  integration/              # command-line, conversion and publication workflows
  rendering/                # real browser-backed HTML/PDF and LibreOffice workflows
```

Word conversion runs in stages, which `docx/pipeline.py` coordinates and
`cli.py` starts. `package.py` reads the Word package safely, and `prepare.py`
writes a private copy that Pandoc reads as docx-to-md converts it: headings,
code, fields, images and bookmarks in terms that Pandoc's DOCX reader
understands. `tree.py` reads the copy into Pandoc's JSON document tree and
writes GFM; `transforms.py`, `bookmarks.py` and `tables.py` rewrite the tree in
between; `navigation.py` settles heading IDs, links and pages; `validation.py`
checks the result; and `publication.py` and `diagnostics.py` write the outputs
and the report. `media.py` publishes the images: one LibreOffice run, started in
the background as soon as the package is read, converts the EMF and WMF drawings
that the body shows while Pandoc reads the document. Where Pandoc's GFM writer
would write something that reads back differently, a transform writes it
instead: code fences, whose `text` label the writer drops (since Pandoc 3.10.1),
emphasis that Markdown cannot close, and lines of `-` or `=`, which the writer
leaves unescaped.

Rendering prepares the Markdown in `markdown/preprocess.py`: it collects a
folder, adjusts what Pandoc's Markdown reader would read differently from
GitHub, and embeds images, each as a short placeholder for its data. It parses
the Markdown once, with `markdown/document.py`'s reader, and hands Pandoc the
parsed document as Pandoc JSON, not written back to Markdown. A folder's files
are parsed one by one and joined as document trees; one more Pandoc run, with
`assets/heading-ids.lua`, gives their headings the identifiers that Pandoc gives
them read as one document.
`rendering/pipeline.py` then runs Pandoc in its sandbox with
`assets/pandoc-defaults.yaml`, whose filters read GitHub's math, draw Mermaid
diagrams and KaTeX math (each with one Node.js run, only for a document that
has any), and adjust tables; puts the images' data in the HTML,
which Pandoc would read slowly and in gigabytes of memory; and for a PDF, prints
the HTML in headless Chrome with `rendering/print-pdf.mjs`.

Every command prints through `console.py`: warnings, the first three of each
kind unless `--verbose`; errors; a status line on a terminal; and the result, on
standard output. Shared code reports through the running command's console.

Shared console, Markdown and Pandoc modules do not import converters, rendering
or the toolchain, and the toolchain does not import converters, rendering or the
Markdown layer; `tests/unit/test_package.py` enforces these layers and an
acyclic import graph.

Parsed Markdown retains its input text and reader. DOCX heading discovery and
page-link rewriting reuse an unchanged page parse; rewriting copies the AST and
rejects mismatched content or readers. There is no global parse cache, and
semantic checks after text transformations still parse the changed text
independently. Such a check's parses, such as of a page before and after a
rewrite, run at once with `pandoc.run_all`, each in a Pandoc process of its own.

## Environment

Development needs Linux x86_64, [uv](https://docs.astral.sh/uv/), make and Git,
plus Node.js 22.13 or newer for the render suite, and LibreOffice and Poppler
(`pdftocairo`) for its EMF tests. uv installs Python (version from
`.python-version`) and the locked dependencies in `uv.lock`. The `Makefile` runs
each task through uv and sets `MD_DOC_CONVERTER_HOME` to `.toolchain/` in the
checkout unless it is already set, so the toolchain that the tests use stays out
of your home directory.

```sh
make lint    # Ruff, ty, Pyrefly, rumdl, typos, actionlint and zizmor
make test    # core suite; installs the pinned Pandoc first
make         # lists the tasks
```

| Task | Purpose |
| --- | --- |
| `setup` | Install the toolchain into `.toolchain/`; needs Node.js |
| `setup-pandoc` | Install only the pinned Pandoc into `.toolchain/` |
| `doctor` | Check the prerequisites |
| `format` | Format Python with Ruff, and fix what rumdl can in Markdown |
| `lint` | Check Python (Ruff's formatter and linter, ty, Pyrefly), Markdown (rumdl), spelling (typos), and the GitHub workflow (actionlint with shellcheck, zizmor) |
| `test` | Core suite: unit and integration tests, in parallel; runs `setup-pandoc` first |
| `test-render` | Real rendering suite; runs `setup` first |
| `test-all` | Both suites |
| `coverage` | Core suite's line and branch coverage, listing the lines it misses |
| `build` | Build the wheel and sdist into `dist/` |
| `lock-npm` | Refresh `package-lock.json` after editing the bundled `package.json` |

`uv run`, which each task uses, creates or updates `.venv` from `uv.lock` first, so
every checker is a locked development dependency. Their settings are in
`pyproject.toml`; `.editorconfig` gives editors the indentation and line endings.
The `Makefile` runs recipes with bash's `-eu -o pipefail` and warns about undefined
variables.

## Test suites

The tests run with [pytest](https://docs.pytest.org/): the core suite in
`tests/unit` and `tests/integration`, which plain `uv run pytest` runs, and the
render suite in `tests/rendering`. `make test` runs the core suite in parallel
with pytest-xdist; `uv run pytest -k NAME` runs matching tests on their own.

`tests/conftest.py` applies the suites' policies, defined in
`tests/support/suites.py`. Tests use `.toolchain/` unless `MD_DOC_CONVERTER_HOME`
is set, with its `bin/` first on `PATH`, as the command does. A run stops before
collecting when the selected suites miss a prerequisite, and a skipped test
fails, so a missing tool never quietly reduces coverage. pytest runs in strict
mode, and a warning fails the test that raises it.

The core suite needs Git and the pinned Pandoc 3.12.1, because some converter
tests compare exact Pandoc output; a run stops with a hint to run
`make setup-pandoc` when that Pandoc is missing. The core suite does not
need Node.js, the rest of the toolchain, browsers or LibreOffice: command-line
and pipeline tests use disposable fake `pandoc`, `node`, `npm` and `mmdc`
executables and a fake toolchain, and EMF conversion logic is covered with
mocked native tools. Converter fixtures are synthetic documents built by
`tests/support`.

The render suite exercises real EMF-to-SVG conversion (one LibreOffice run for
a document's drawings, and the PDF fallback), a synthetic
Word-to-Markdown-to-HTML/PDF workflow, a reference-linked folder with images,
Mermaid and math, the printed PDF's pages, header and footer, bookmarks,
tagging and fonts, and the PDF time limit. It needs the installed toolchain,
Node.js, LibreOffice (`libreoffice` or `soffice`) and Poppler's `pdftocairo`,
`pdfinfo`, `pdftotext`, `pdftohtml` and `pdffonts` on `PATH`.

`.python-version` selects the newest supported Python, 3.15; to test the
oldest, 3.11:

```sh
UV_PYTHON=3.11 UV_PROJECT_ENVIRONMENT=.venv-3.11 make test
```

Tests are plain pytest functions. A test asks for what it needs as fixtures:
pytest's own, such as `tmp_path`, `monkeypatch` and `capsys`; the shared ones in
`tests/conftest.py`, `pandoc` (the pinned Pandoc) and `fake_tools` (writes fake
executables, which `tests/support/fake_tool.py` implements, installs a fake
toolchain and reads the calls they recorded); and its module's own. Independent
cases are `pytest.mark.parametrize` cases with readable ids.

pytest-randomly runs the tests in a random order, printed as `--randomly-seed`
in the header, so no test depends on another; `--randomly-seed=N`
repeats an order, and `-p no:randomly` turns it off. Properties of the escaping
that keeps text literal, such as table cells, CSV rows, link titles and page
header text, are [Hypothesis](https://hypothesis.readthedocs.io/) tests, which
read the generated text back through Pandoc's GFM reader and the renderer's.
They run 50 examples each; `HYPOTHESIS_PROFILE=thorough` runs 1,000.

Every Markdown page that the Word conversion tests publish is also checked with
rumdl, against the rules in `tests/support/converted.rumdl.toml`.

`make coverage` measures the core suite's line and branch coverage, including
the command-line processes that tests start, and lists the lines it misses. No
minimum is enforced; use it to find untested code.

## Updating dependencies

Dependabot (`.github/dependabot.yml`) proposes weekly updates for the Python
packages, the bundled npm manifests and the workflow's actions, once a release
is a week old, in one pull request per ecosystem; major Python and npm updates
come separately. To update by hand:

- **Python:** edit `pyproject.toml`, then run `uv lock`.
- **npm packages:** edit `src/md_doc_converter/toolchain/package.json`, then run
  `make lock-npm`. The next `setup` reinstalls the packages because the
  manifests' digest changes. A test checks that every locked package resolves
  from `registry.npmjs.org`. An override gives every package the KaTeX that the
  math filter uses, so one copy is installed and Mermaid's math matches KaTeX's
  stylesheet; a render test draws math inside a diagram. `setup` leaves out
  optional packages, so one that rendering needs, such as Mermaid's tidy-tree
  layout, is a direct dependency, which a render test checks. When a package needs a
  newer Node.js, raise `NODE_MINIMUM` in `toolchain/environment.py`, which
  `setup` and `doctor` check.
- **Pandoc:** update `PANDOC_VERSION` in `toolchain/environment.py` and the
  `PANDOC` checksum in `toolchain/install.py` (the `sha256sum` of the release's
  `linux-amd64` archive), then update tests that compare exact Pandoc output.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests; a
newer push to a pull request cancels its run in progress. Actions are pinned by
commit SHA, checkouts keep no credentials, and each job has a time limit. The
jobs:

- `lint-and-core`: lint, then the core suite on Python 3.11 and 3.15, with the
  pinned Pandoc cached by `toolchain/install.py`;
- `render`: installs Node.js 24, LibreOffice and Poppler, runs `setup` (cached
  by the npm manifests and the toolchain code) and the render suite;
- `package`: builds the wheel and sdist, checks the wheel's contents, installs
  it with `uv tool install` and runs the installed command.

## Building

`make build` (or `uv build`) writes the wheel and sdist to `dist/`. The
wheel contains only the `md_doc_converter` package with its assets, PDF printer
and npm manifests. Check an installed build outside the checkout:

```sh
uv tool install dist/md_doc_converter-*.whl
md-doc-converter --version
```
