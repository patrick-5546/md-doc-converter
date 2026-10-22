"""pytest hooks for the test suites' policies; see tests/support/suites.py."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from tests.support import suites


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
