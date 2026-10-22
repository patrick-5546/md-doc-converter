# Development tasks for md-doc-converter; `make help` lists them.
# Each task runs through uv, which first creates or updates .venv from uv.lock.

# Fail a recipe on its first failing command, including inside a pipeline; flag undefined
# variables; no implicit rules, which a task runner never needs.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
MAKEFLAGS += --warn-undefined-variables --no-builtin-rules
.SUFFIXES:

# Keep the toolchain that `setup` installs inside the checkout, unless already set.
export MD_DOC_CONVERTER_HOME ?= $(CURDIR)/.toolchain

UV_RUN := uv run

.DEFAULT_GOAL := help
.PHONY: help setup setup-pandoc doctor format lint test test-render test-all coverage build \
	lock-npm

help: ## List the tasks
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "  %-13s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

setup: ## Install the toolchain into .toolchain/; rendering needs Node.js
	$(UV_RUN) md-doc-converter setup

setup-pandoc: ## Install only the pinned Pandoc into .toolchain/
	$(UV_RUN) md-doc-converter setup --no-render

doctor: ## Check the prerequisites
	$(UV_RUN) md-doc-converter doctor

format: ## Format Python with Ruff, and fix what rumdl can in Markdown
	$(UV_RUN) ruff format src tests
	$(UV_RUN) rumdl fmt .

# actionlint also runs shellcheck on the workflow's scripts. zizmor audits the workflow's
# security, including the online audits when GH_TOKEN is set.
lint: ## Python (Ruff, ty, Pyrefly), Markdown, spelling and the GitHub workflow
	$(UV_RUN) ruff format --check src tests
	$(UV_RUN) ruff check src tests
	$(UV_RUN) ty check src tests
	$(UV_RUN) pyrefly check
	$(UV_RUN) rumdl check .
	$(UV_RUN) typos
	$(UV_RUN) actionlint
	$(UV_RUN) zizmor --quiet $(if $(filter undefined,$(origin GH_TOKEN)),--offline) .github

test: setup-pandoc ## Core suite: unit and integration tests, in parallel
	$(UV_RUN) pytest -n auto

test-render: setup ## Real rendering suite; also needs LibreOffice and Poppler
	$(UV_RUN) pytest tests/rendering

test-all: test test-render ## Both suites

coverage: setup-pandoc ## Core suite's line and branch coverage, with the lines it misses
	$(UV_RUN) pytest -n auto --cov --cov-report=term-missing:skip-covered

build: ## Build the wheel and sdist into dist/
	uv build

lock-npm: ## Refresh package-lock.json after editing the bundled package.json
	cd src/md_doc_converter/toolchain && npm install --package-lock-only --ignore-scripts \
		--no-audit --no-fund --registry=https://registry.npmjs.org/
