# Development

## Package layout

```text
src/md_doc_converter/
  __main__.py               # command-line entry point with lazy command dispatch
  console.py                # what commands print: warnings by kind, errors, status
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

`converters/docx/pipeline.py` runs Word conversion's stages: `package.py`
reads the Word package, `prepare.py` writes the copy that Pandoc reads,
`tree.py` reads it into Pandoc's document tree and writes GFM, `transforms.py`,
`bookmarks.py`, `tables.py` and `media.py` rewrite the tree and publish the
images, `navigation.py` settles heading IDs, links and pages, `validation.py`
checks the result, and `publication.py` and `diagnostics.py` write the outputs
and the report. `rendering/pipeline.py` renders: `markdown/preprocess.py`
prepares the Markdown, Pandoc writes the HTML with `assets/pandoc-defaults.yaml`
and its filters, and `rendering/print-pdf.mjs` prints a PDF. The docstrings
tell how each part works.

## Conventions

- **Layers:** `console.py`, `markdown/`, `pandoc.py` and `resources.py`, which
  every command shares, import no converter, rendering or toolchain module, and
  the toolchain imports no converter, rendering or Markdown module.
  `tests/unit/test_package.py` checks these layers, and that the imports form no
  cycle.
- **Messages:** commands print through `console.py`, which groups warnings by
  kind. Shared code reports a warning with
  `console.active().warning(message, kind=..., where=...)`. Word conversion
  reports with `report.warn` or `report.skipped`, giving the warning's kind and
  its section: `word` or `markdown`, the summary's two parts, or `detail`, which
  only `--verbose` shows.
- **Packaged files:** adding, renaming or removing a file in `assets/` also
  changes `ASSETS` in `tests/unit/test_package.py` and the wheel check in
  `.github/workflows/ci.yml`.

## Documentation

- The README covers what md-doc-converter does, installing it, its commands,
  settings and messages. Each guide in `docs/` covers its commands for their
  users: what they produce, and the options and messages that need more than a
  line. `--help` is the reference for every option: a guide names an option only
  where it explains it, and lists none. This file covers the layout, the
  conventions and the workflows for changing the code.
- Change the README or a guide when what users see or do changes. How a module
  works belongs in its docstrings, and why it changed in the commit message.
- Say each thing in one place, and link to it from the others. Name a version
  only where readers need it; `tests/unit/test_docs.py` checks the versions,
  limits, commands and options that the docs name against the code.

## Environment

Development needs Linux x86_64, [uv](https://docs.astral.sh/uv/), make and Git,
plus Node.js 22.13 or newer for the render suite, and LibreOffice and Poppler
(`pdftocairo`) for its EMF tests. uv installs Python (version from
`.python-version`) and the locked dependencies in `uv.lock`. The `Makefile` runs
each task through uv and sets `MD_DOC_CONVERTER_HOME` to `.toolchain/` in the
checkout unless it is already set, so the toolchain that the tests use stays out
of your home directory.

```sh
make              # lists every task
make format       # format Python, and fix what rumdl can in Markdown
make lint         # Ruff, ty, Pyrefly, rumdl, typos, actionlint and zizmor
make test         # core suite; installs the pinned Pandoc first
make test-render  # render suite; installs the toolchain first
```

`uv run`, which each task uses, creates or updates `.venv` from `uv.lock` first,
so every checker is a locked development dependency. Their settings are in
`pyproject.toml`; `.editorconfig` gives editors the indentation and line
endings.

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

The core suite needs Git and the pinned Pandoc, which `make test` installs
first, because some converter tests compare exact Pandoc output. It needs
nothing else: command-line and pipeline tests use fake `pandoc`, `node`, `npm`
and `mmdc` executables and a fake toolchain, and EMF conversion is tested with
LibreOffice and Poppler mocked. The render suite runs the real tools: the
installed toolchain, Node.js, LibreOffice (`libreoffice` or `soffice`) and
Poppler's `pdftocairo`, `pdfinfo`, `pdftotext`, `pdftohtml` and `pdffonts`.

`.python-version` selects the newest supported Python; to test the oldest:

```sh
UV_PYTHON=3.11 UV_PROJECT_ENVIRONMENT=.venv-3.11 make test
```

Tests are plain pytest functions. A test asks for what it needs as fixtures:
pytest's own, such as `tmp_path`, `monkeypatch` and `capsys`; the shared ones in
`tests/conftest.py`, `pandoc` (the pinned Pandoc) and `fake_tools` (writes fake
executables, which `tests/support/fake_tool.py` implements, installs a fake
toolchain and reads the calls they recorded); and its module's own. Converter
fixtures are synthetic documents built by `tests/support`. Independent cases are
`pytest.mark.parametrize` cases with readable ids.

pytest-randomly runs the tests in a random order, printed as `--randomly-seed`
in the header, so no test depends on another; `--randomly-seed=N` repeats an
order, and `-p no:randomly` turns it off. Properties of the escaping that keeps
text literal, such as table cells, CSV rows, link titles and page header text,
are [Hypothesis](https://hypothesis.readthedocs.io/) tests, which read the
generated text back through Pandoc's GFM reader and the renderer's. They run 50
examples each; `HYPOTHESIS_PROFILE=thorough` runs 1,000. Every Markdown page
that the Word conversion tests publish is also checked with rumdl, against the
rules in `tests/support/converted.rumdl.toml`.

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
  `make lock-npm`; the next `setup` reinstalls the packages. Keep the override
  that gives every package the math filter's KaTeX, so that Mermaid draws math
  as KaTeX's stylesheet expects, and keep a package that rendering needs but
  that is optional elsewhere, such as Mermaid's tidy-tree layout, a direct
  dependency, since `setup` installs no optional packages; tests check both, and
  that every package resolves from `registry.npmjs.org`. When a package needs a
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

- `lint-and-core`: lint, then the core suite on the oldest and newest supported
  Python, with the pinned Pandoc cached;
- `render`: installs Node.js, LibreOffice and Poppler, runs `setup` (cached)
  and the render suite;
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
