# Development tasks for md-doc-converter; `make help` lists them.
# Each task runs through uv, which first creates or updates .venv from uv.lock.

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

format: ## Format Python with Ruff
	$(UV_RUN) ruff format src tests

lint: ## Ruff format check, Ruff, ty and Pyrefly
	$(UV_RUN) ruff format --check src tests
	$(UV_RUN) ruff check src tests
	$(UV_RUN) ty check src tests
	$(UV_RUN) pyrefly check

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
