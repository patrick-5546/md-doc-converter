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
  toolchain/                # setup, doctor, renderer locations and environment
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

Development needs Linux x86_64, [pixi](https://pixi.sh) and Git. pixi provides
Pandoc 3.10, Node.js 24, Poppler (`pdftocairo`) and uv from conda-forge; uv
installs Python (version from `.python-version`) and the locked dependencies in
`uv.lock`. Inside pixi, `MD_DOC_CONVERTER_HOME` points at `.toolchain/` in the
checkout, so the renderer toolchain stays out of your home directory.

```sh
pixi run sync      # uv sync --locked
pixi run lint      # Ruff format check, Ruff, ty and Pyrefly
pixi run test      # core suite
```

| Task | Purpose |
| --- | --- |
| `sync` | Create or update `.venv` from `uv.lock` |
| `setup` | Install the renderer toolchain into `.toolchain/` |
| `doctor` | Check the prerequisites |
| `format` | Format Python with Ruff |
| `lint` | `lint-ruff`, `lint-ty` and `lint-pyrefly` |
| `test` | Core suite: unit and integration tests |
| `test-render` | Real rendering suite; runs `setup` first |
| `test-all` | Both suites |
| `build` | Build the wheel and sdist into `dist/` |
| `lock-npm` | Refresh `package-lock.json` after editing the bundled `package.json` |

Without pixi, put Pandoc 3.10 on `PATH` and use uv directly, for example
`uv run python -m tests core`.

## Test suites

Both suites are discovered explicitly through `python -m tests core|render|all`.
Missing prerequisites, empty discovery and skipped tests fail rather than
reducing coverage.

The core suite needs Git and Pandoc. It does not need Node.js, the renderer
toolchain, browsers or LibreOffice: command-line and pipeline tests use
disposable fake `pandoc`, `node` and `npm` executables and a fake toolchain, and
EMF conversion logic is covered with mocked native tools. Converter fixtures are
synthetic documents built by `tests/support`.

The render suite exercises real EMF-to-SVG conversion (including its PDF
fallback), a synthetic Word-to-Markdown-to-HTML/PDF workflow, a reference-linked
folder with images, Mermaid and math, and the PDF time limit. It needs the
installed toolchain, `pdftocairo` and LibreOffice (`libreoffice` or `soffice`)
on `PATH`.

Some converter tests compare exact Pandoc output and expect the Pandoc 3.10 that
pixi pins; Pandoc 3.10.2, for example, writes fenced code without its `text`
label. Python 3.10 is the oldest supported version:

```sh
UV_PYTHON=3.10 UV_PROJECT_ENVIRONMENT=.venv-3.10 pixi run test
```

## Updating dependencies

- **Python:** edit `pyproject.toml`, then run `uv lock`. MarkItDown stays pinned
  to one version because Word alignment depends on its exact output.
- **npm packages:** edit `src/md_doc_converter/toolchain/package.json`, then run
  `pixi run lock-npm`. The next `setup` reinstalls the packages because the
  manifests' digest changes. A test checks that every locked package resolves
  from `registry.npmjs.org`.
- **pandoc-katex:** update `PANDOC_KATEX_VERSION` in
  `toolchain/environment.py`, `PANDOC_KATEX_SHA256` in `toolchain/install.py`
  (the `sha256sum` of the release's `x86_64-unknown-linux-musl` archive), and the
  KaTeX stylesheet version in `assets/pandoc-defaults.yaml`
  (`pandoc-katex --katex-version`).
- **Pandoc:** change the pin in `pixi.toml` and update tests that compare exact
  Pandoc output.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes and pull requests:

- `lint-and-core`: lint, then the core suite on Python 3.10 and 3.13;
- `render`: installs LibreOffice, runs `setup` (cached by the npm lockfile and
  the pandoc-katex pin) and the render suite;
- `package`: builds the wheel and sdist, checks the wheel's contents, installs
  it with `uv tool install` and runs the installed command.

## Building

`pixi run build` (or `uv build`) writes the wheel and sdist to `dist/`. The
wheel contains only the `md_doc_converter` package with its assets, PDF printer
and npm manifests. Check an installed build outside the checkout:

```sh
uv tool install dist/md_doc_converter-*.whl
md-doc-converter --version
```
