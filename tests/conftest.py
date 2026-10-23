"""pytest hooks for the test suites' policies (see tests/support/suites.py), and shared fixtures."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from hypothesis import settings

from tests.support import suites
from tests.support.fake_tools import FakeTools

# Properties that run Pandoc take tens of milliseconds an example, so they have no deadline
# and fewer examples than Hypothesis's default; HYPOTHESIS_PROFILE=thorough runs many more.
settings.register_profile("md-doc-converter", deadline=None, max_examples=50)
settings.register_profile("thorough", deadline=None, max_examples=1000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "md-doc-converter"))


def pytest_configure(config: pytest.Config) -> None:
    del config
    suites.use_project_toolchain()


def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    # Check once, in the main process, and only when tests are going to run.
    if hasattr(config, "workerinput") or config.option.collectonly:
        return
    render = suites.selects_rendering(config.args, config.invocation_params.dir)
    missing = suites.missing_prerequisites(render=render)
    if missing:
        pytest.exit("prerequisites missing: " + ", ".join(missing))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport() -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr
        report.outcome = "failed"
        report.longrepr = f"tests must not skip: {reason}"
    return report


@pytest.fixture(scope="session")
def pandoc() -> str:
    """The pinned Pandoc, which every suite requires before it runs."""
    return suites.pinned_pandoc()


@pytest.fixture
def fake_tools(tmp_path: Path) -> FakeTools:
    """Fake pandoc, node, npm, mmdc and Puppeteer, written on request with `write`."""
    return FakeTools(tmp_path / "fake-bin", tmp_path / "tools.jsonl")
