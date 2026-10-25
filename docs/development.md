# Development

## Package layout

```text
src/md_doc_converter/
  __main__.py               # command-line entry point with lazy command dispatch
  console.py                # what commands print: warnings by kind, errors, status
  resources.py, pandoc.py   # packaged file paths, version, typed Pandoc parsing
  executables.py            # the programs that it runs, found on PATH and never elsewhere
  media_types.py            # media types of the images and fonts that rendering embeds
  download.py               # downloads of images and stylesheet files, with limits
  markdown/                 # preprocessing, headings, code regions, literal cells,
                            # file paths as the URL paths of targets
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
- **Programs:** run each program, such as Pandoc, Node.js or Git, by the path
  that `executables.py` finds on `PATH`, never by its bare name, which Windows
  would look for in the current folder first. Lua filters get the paths they
  need from `render_environment`, such as `MD_DOC_CONVERTER_NODE`.

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

Development needs Linux x86_64 or Windows x86_64,
[uv](https://docs.astral.sh/uv/), GNU make 4 or newer and Git, plus Node.js
22.13 or newer for the render suite, and LibreOffice and Poppler (`pdftocairo`)
for its EMF tests. uv installs Python (version from `.python-version`) and the
locked dependencies in `uv.lock`. The `Makefile` runs each task through uv and
sets `MD_DOC_CONVERTER_HOME` to `.toolchain/` in the checkout unless it is
already set, so the toolchain that the tests use stays out of your home
directory. `.gitattributes` gives every file LF line endings, on Windows too.

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

On Windows, install make with `winget install ezwinports.make`. There make runs
each task's commands with cmd.exe, whichever shell starts it, so the tasks run
alike from PowerShell, cmd and Git Bash. `make -n TASK` prints a task's commands
without running them.

### Package mirrors

uv installs the files that `uv.lock` names from the URLs it records, which are
PyPI's. Given a mirror's index, with `--default-index`, `UV_DEFAULT_INDEX` or a
`uv.toml`, uv's project commands, such as `uv run` and `uv lock`, lock again
through the mirror instead and write the mirror's URLs into `uv.lock`, which
other machines and the CI cannot use. Name a mirror only on the command line, as
below, never machine-wide, and do not commit a lock that names one. uv has no
setting that downloads through a mirror while it keeps PyPI's URLs in the lock;
[astral-sh/uv#6349](https://github.com/astral-sh/uv/issues/6349) asks for one.

On a network that allows only a package mirror, `uv run`, and every task that
uses it, therefore fails as it syncs `.venv`. Install the locked packages
through the mirror instead, with the lock's hashes; `uv run` then finds `.venv`
in sync. Run these again whenever `uv.lock` changes:

```sh
mirror=https://mirror.example/pypi/simple/
uv export --frozen --all-groups --no-emit-project |
  uv pip sync --index-url "$mirror" --exclude-newer false -
uv pip install --index-url "$mirror" --exclude-newer false --no-deps -e .
```

In PowerShell:

```powershell
$mirror = "https://mirror.example/pypi/simple/"
uv export --frozen --all-groups --no-emit-project |
  uv pip sync --index-url $mirror --exclude-newer false -
uv pip install --index-url $mirror --exclude-newer false --no-deps -e .
```

`--exclude-newer false` turns off the week-long limit that `pyproject.toml`
sets, which these commands need not apply again: the locked versions are a week
old already, and a mirror may report when it took in a file rather than when it
was published. npm needs only its registry set, as the README's
[Package mirrors](../README.md#package-mirrors) shows.

## Test suites

The tests run with [pytest](https://docs.pytest.org/): the core suite in
`tests/unit` and `tests/integration`, which plain `uv run pytest` runs, and the
render suite in `tests/rendering`. `make test` runs the core suite in parallel
with pytest-xdist; `uv run pytest -k NAME` runs matching tests on their own.

`tests/conftest.py` applies the suites' policies, defined in
`tests/support/suites.py`. Tests use `.toolchain/` unless `MD_DOC_CONVERTER_HOME`
is set, with its `bin/` first on `PATH`, as the command does. A run stops before
collecting when the selected suites miss a prerequisite, and a skipped test
fails, so a missing tool never quietly reduces coverage. Where Linux and Windows
differ, a test expects what each does; a test of what only one has, such as
Linux's `/dev/shm`, is defined there alone. pytest runs in strict mode, and a
warning fails the test that raises it.

The core suite needs Git and the pinned Pandoc, which `make test` installs
first, because some converter tests compare exact Pandoc output. On Windows, it
also needs permission to create symbolic links, which administrators have, as do
other users in Developer Mode. It needs nothing else: command-line and pipeline
tests use fake `pandoc`, `node` and `npm` executables, fake scripts for the
`mmdc` and `puppeteer` commands that the fake `node` runs, and a fake toolchain,
and EMF conversion is tested with LibreOffice and Poppler mocked. The render
suite runs the real tools: the installed toolchain, Node.js, LibreOffice
(`libreoffice` or `soffice`) and Poppler's `pdftocairo`, `pdfinfo`,
`pdftotext`, `pdftohtml` and `pdffonts`.

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
come separately. Most compromised releases are found and pulled within days, so
the lockfiles hold only releases at least a week old too, whoever updates them:
`exclude-newer` in `pyproject.toml` sets this for uv, and `min-release-age` in
the `.npmrc` beside the npm manifests sets it for npm 11.10 or newer. A minimum
version in `pyproject.toml` or `package.json` must be a week old too, or locking
fails. A [package mirror](#package-mirrors) that holds new releases for a week
then has every locked release. To update by hand:

- **Python:** edit `pyproject.toml`, then run `uv lock`. `uv.lock` records the
  week as a span, not a date, and each new resolution, such as
  `uv lock --upgrade`, counts it back from its own time.
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
  checksums in `PANDOC_ARCHIVES` in `toolchain/install.py`, one for each
  platform's release archive, which
  `gh api repos/jgm/pandoc/releases/tags/VERSION --jq '.assets[] | [.name, .digest] | @tsv'`
  lists; then update tests that compare exact Pandoc output.

The week-long limits apply to a security fix too, for which Dependabot skips its
cooldown. To take a release less than a week old, lift the limit for it alone:

- **Python:** add the package to `pyproject.toml`'s
  `[tool.uv.exclude-newer-package]` table with the value `false`, then run
  `uv lock`. `uv.lock` records the exception, so once the release is a week
  old, remove the entry and run `uv lock` again.
- **npm packages:** run `npm_config_min_release_age=0 make lock-npm`.

A mirror that holds new releases serves the release only once it lets it
through.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests; a
newer push to a pull request cancels its run in progress. Actions are pinned by
commit SHA, checkouts keep no credentials, and each job has a time limit. The
jobs:

- `lint-and-core`: lint, then the core suite, with the pinned Pandoc cached;
- `core-windows`: the core suite on Windows, run with uv, as make runs it;
- `render`: installs Node.js, LibreOffice and Poppler, runs `setup` (cached)
  and the render suite;
- `render-windows`: the same on Windows, with LibreOffice and Poppler from
  Chocolatey;
- `package`: on Linux and Windows, builds the wheel and sdist, checks the
  wheel's contents, installs it with `uv tool install` and runs the installed
  command.

## Building

`make build` (or `uv build`) writes the wheel and sdist to `dist/`. The
wheel contains only the `md_doc_converter` package with its assets, PDF printer
and npm manifests. Check an installed build outside the checkout:

```sh
uv tool install dist/md_doc_converter-*.whl
md-doc-converter --version
```
