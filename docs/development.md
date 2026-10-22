# Development

## Package layout

```text
src/md_doc_converter/
  __main__.py               # command-line entry point with lazy command dispatch
  resources.py, pandoc.py   # packaged file paths, version, typed Pandoc parsing
  markdown/                 # preprocessing, headings, code regions, literal cells
  converters/
    excel.py, csv.py
    docx/                   # responsibility-based Word conversion stages
  rendering/                # HTML/PDF orchestration, page text CSS, PDF printer
  toolchain/                # setup, doctor, toolchain locations and environment
    package.json, package-lock.json   # pinned npm packages installed by setup
  assets/                   # stylesheets, HTML fragments, Pandoc/Mermaid config, Lua filters
tests/
  support/                  # synthetic document builders and fake native tools
  unit/                     # mirrors runtime responsibilities
  integration/              # command-line, conversion and publication workflows
  rendering/                # real browser-backed HTML/PDF and LibreOffice workflows
```

The DOCX package separates typed models, OOXML/source preparation, reference
HTML, tables, recovery, normalization, media, navigation, validation,
publication, and diagnostics. `pipeline.py` coordinates those stages and
`cli.py` handles arguments. Shared Markdown/Pandoc modules do not import
converters, rendering or the toolchain, and the toolchain does not import
converters, rendering or the Markdown layer; `tests/unit/test_package.py`
enforces these layers and an acyclic import graph.

Parsed Markdown retains its input text and reader. DOCX heading discovery and
page-link rewriting reuse an unchanged page parse; rewriting copies the AST and
rejects mismatched content or readers. There is no global parse cache, and
semantic checks after text transformations still parse the changed text
independently.

## Environment

Development needs Linux x86_64, [uv](https://docs.astral.sh/uv/) and Git, plus
Node.js 22.13 or newer for the render suite, and LibreOffice and Poppler
(`pdftocairo`) for its EMF tests. uv installs Python (version from
`.python-version`) and the locked dependencies in `uv.lock`, including
[Poe the Poet](https://poethepoet.natn.io/), which runs the tasks defined in
`pyproject.toml`. The tasks set `MD_DOC_CONVERTER_HOME` to `.toolchain/` in the
checkout unless it is already set, so the toolchain that the tests use stays out
of your home directory.

```sh
uv run poe lint    # Ruff format check, Ruff, ty and Pyrefly
uv run poe test    # core suite; installs the pinned Pandoc first
uv run poe         # lists the tasks
```

| Task | Purpose |
| --- | --- |
| `setup` | Install the toolchain into `.toolchain/`; needs Node.js |
| `setup-pandoc` | Install only the pinned Pandoc into `.toolchain/` |
| `doctor` | Check the prerequisites |
| `format` | Format Python with Ruff |
| `lint` | Ruff format check, Ruff, ty and Pyrefly |
| `test` | Core suite: unit and integration tests; runs `setup-pandoc` first |
| `test-render` | Real rendering suite; runs `setup` first |
| `test-all` | Both suites; runs `setup` first |
| `build` | Build the wheel and sdist into `dist/` |
| `lock-npm` | Refresh `package-lock.json` after editing the bundled `package.json` |

`uv run` creates or updates `.venv` from `uv.lock` before running a task.

## Test suites

Both suites are discovered explicitly through `python -m tests core|render|all`.
Missing prerequisites, empty discovery and skipped tests fail rather than
reducing coverage. Like the tasks, the test runner uses `.toolchain/` unless
`MD_DOC_CONVERTER_HOME` is set, and puts its `bin/` first on `PATH`, as the
command does.

The core suite needs Git and the pinned Pandoc 3.12.1, because some converter
tests compare exact Pandoc output; the runner stops with a hint to run
`uv run poe setup-pandoc` when that Pandoc is missing. The core suite does not
need Node.js, the rest of the toolchain, browsers or LibreOffice: command-line
and pipeline tests use disposable fake `pandoc`, `node` and `npm` executables
and a fake toolchain, and EMF conversion logic is covered with mocked native
tools. Converter fixtures are synthetic documents built by `tests/support`.

The render suite exercises real EMF-to-SVG conversion (one LibreOffice run for
a document's drawings, and the PDF fallback), a synthetic
Word-to-Markdown-to-HTML/PDF workflow, a reference-linked folder with images,
Mermaid and math, and the PDF time limit. It needs the installed toolchain,
Node.js, `pdftocairo` and LibreOffice (`libreoffice` or
`soffice`) on `PATH`.

The converter writes code fences itself, because Pandoc's GFM writer drops
labels, such as `text`, that are not among its highlighting languages (since
Pandoc 3.10.1). `.python-version` selects the newest supported Python, 3.14;
to test the oldest, 3.11:

```sh
UV_PYTHON=3.11 UV_PROJECT_ENVIRONMENT=.venv-3.11 uv run poe test
```

## Updating dependencies

- **Python:** edit `pyproject.toml`, then run `uv lock`. MarkItDown stays pinned
  to one version, and mammoth, its Word reader, to one release series, because
  Word alignment depends on their exact output.
- **npm packages:** edit `src/md_doc_converter/toolchain/package.json`, then run
  `uv run poe lock-npm`. The next `setup` reinstalls the packages because the
  manifests' digest changes. A test checks that every locked package resolves
  from `registry.npmjs.org`. Rendered HTML links the stylesheet of the installed
  KaTeX version.
- **Pandoc:** update `PANDOC_VERSION` in `toolchain/environment.py` and the
  `PANDOC` checksum in `toolchain/install.py` (the `sha256sum` of the release's
  `linux-amd64` archive), then update tests that compare exact Pandoc output.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes and pull requests:

- `lint-and-core`: lint, then the core suite on Python 3.11 and 3.14, with the
  pinned Pandoc cached by `toolchain/install.py`;
- `render`: installs Node.js 24, LibreOffice and Poppler, runs `setup` (cached
  by the npm manifests and the toolchain code) and the render suite;
- `package`: builds the wheel and sdist, checks the wheel's contents, installs
  it with `uv tool install` and runs the installed command.

## Building

`uv run poe build` (or `uv build`) writes the wheel and sdist to `dist/`. The
wheel contains only the `md_doc_converter` package with its assets, PDF printer
and npm manifests. Check an installed build outside the checkout:

```sh
uv tool install dist/md_doc_converter-*.whl
md-doc-converter --version
```
