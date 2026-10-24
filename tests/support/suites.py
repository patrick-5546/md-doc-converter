"""The test suites' policies, which tests/conftest.py applies.

- Tests use the checkout's toolchain, `.toolchain/`, unless MD_DOC_CONVERTER_HOME is set, with
  its pinned Pandoc first on PATH, as the command does.
- A run stops before collecting when the selected suites miss a prerequisite: Git and the pinned
  Pandoc for every suite, and Node.js, Poppler, LibreOffice and the installed toolchain for the
  rendering suite in tests/rendering.
- A skipped test fails the run, so a missing tool cannot quietly reduce coverage.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping, Sequence
from pathlib import Path

from md_doc_converter import executables
from md_doc_converter.toolchain.checks import check_browser, check_packages, check_pandoc
from md_doc_converter.toolchain.environment import (
    PANDOC_VERSION,
    prefer_installed_tools,
    toolchain,
)
from tests.support.project import PROJECT_ROOT

RENDERING = PROJECT_ROOT / "tests" / "rendering"
SETUP_PANDOC = "run `make setup-pandoc`"
# The Poppler utilities that the render suite runs: pdftocairo converts EMF drawings, and
# the others read the PDFs it prints.
POPPLER = ("pdftocairo", "pdfinfo", "pdftotext", "pdftohtml", "pdffonts")


def use_project_toolchain(environment: MutableMapping[str, str] | None = None) -> None:
    """Use the checkout's toolchain, as make does, with its pinned Pandoc first on PATH."""
    environment = os.environ if environment is None else environment
    environment.setdefault("MD_DOC_CONVERTER_HOME", str(PROJECT_ROOT / ".toolchain"))
    prefer_installed_tools(environment)


def selects_rendering(arguments: Sequence[str], base: Path) -> bool:
    """Whether the paths or node IDs given to pytest include any rendering test."""
    for argument in arguments:
        path = (base / argument.split("::", 1)[0]).resolve()
        if path == RENDERING or RENDERING in path.parents or path in RENDERING.parents:
            return True
    return False


def pinned_pandoc() -> str:
    """The pinned Pandoc, which every suite requires before it runs."""
    pandoc = executables.which("pandoc")
    assert pandoc is not None, "the suites' prerequisites guarantee Pandoc"
    return pandoc


def missing_prerequisites(*, render: bool) -> list[str]:
    missing = [] if executables.which("git") else ["git"]
    # Converter tests compare exact Pandoc output, so they need the pinned version.
    pandoc = check_pandoc()
    if pandoc.status != "ok":
        missing.append(f"pandoc {PANDOC_VERSION}: {pandoc.detail} ({SETUP_PANDOC})")
    if render:
        missing.extend(tool for tool in ("node", *POPPLER) if executables.which(tool) is None)
        if executables.libreoffice() is None:
            missing.append("libreoffice/soffice (EMF regression coverage)")
        tools = toolchain()
        missing.extend(
            f"{check.name}: {check.detail} ({check.fix})"
            for check in (check_packages(tools), check_browser(tools))
            if check.status != "ok"
        )
    return missing
