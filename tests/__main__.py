"""Explicit core and native-rendering regression suites."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import unittest

from md_doc_converter.toolchain.checks import (
    check_browser,
    check_packages,
    check_pandoc,
    check_pandoc_katex,
)
from md_doc_converter.toolchain.environment import (
    PANDOC_VERSION,
    prefer_installed_tools,
    toolchain,
)
from tests.support.project import PROJECT_ROOT

SUITES = {
    "core": ("unit", "integration"),
    "render": ("rendering",),
    "all": ("unit", "integration", "rendering"),
}


SETUP_PANDOC = "run `uv run poe setup-pandoc`"


def use_project_toolchain() -> None:
    """Use the checkout's toolchain, as the poe tasks do, with its pinned Pandoc first on PATH."""
    os.environ.setdefault("MD_DOC_CONVERTER_HOME", str(PROJECT_ROOT / ".toolchain"))
    prefer_installed_tools()


def missing_dependencies(selection: str) -> list[str]:
    missing = [] if shutil.which("git") else ["git"]
    # Converter tests compare exact Pandoc output, so they need the pinned version.
    pandoc = check_pandoc()
    if pandoc.status != "ok":
        missing.append(f"pandoc {PANDOC_VERSION}: {pandoc.detail} ({SETUP_PANDOC})")
    if selection in {"render", "all"}:
        missing.extend(tool for tool in ("node", "pdftocairo") if shutil.which(tool) is None)
        if not (shutil.which("libreoffice") or shutil.which("soffice")):
            missing.append("libreoffice/soffice (EMF regression coverage)")
        tools = toolchain()
        for check in (check_packages(tools), check_browser(tools), check_pandoc_katex(tools)):
            if check.status != "ok":
                missing.append(f"{check.name}: {check.detail} ({check.fix})")
    return missing


def load_suite(selection: str) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for directory in SUITES[selection]:
        suite.addTests(
            loader.discover(
                str(PROJECT_ROOT / "tests" / directory),
                top_level_dir=str(PROJECT_ROOT),
            )
        )
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES, default="all", nargs="?")
    args = parser.parse_args(argv)
    missing = missing_dependencies(args.suite)
    if missing:
        print(
            f"md-doc-converter tests: {args.suite} prerequisites missing: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    suite = load_suite(args.suite)
    if not suite.countTestCases():
        print(f"md-doc-converter tests: no {args.suite} tests discovered", file=sys.stderr)
        return 2
    print(
        f"md-doc-converter tests: running {suite.countTestCases()} {args.suite} cases", flush=True
    )
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if result.skipped:
        print("md-doc-converter tests: selected suites must not skip tests:", file=sys.stderr)
        for case, reason in result.skipped:
            print(f"  {case}: {reason}", file=sys.stderr)
    return int(not result.wasSuccessful() or bool(result.skipped))


if __name__ == "__main__":
    use_project_toolchain()
    raise SystemExit(main())
