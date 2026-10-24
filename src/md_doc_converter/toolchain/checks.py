"""Prerequisite checks shared by rendering and ``md-doc-converter doctor``."""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from md_doc_converter import executables
from md_doc_converter.resources import package_version
from md_doc_converter.toolchain.environment import (
    BROWSER,
    MERMAID_CLI,
    NODE_MINIMUM,
    PANDOC_VERSION,
    PLATFORMS,
    Toolchain,
    current_platform,
    packages_current,
    toolchain,
)

PANDOC_FIX = "Run `md-doc-converter setup`, or `setup --no-render` for conversion only"
NODE_REQUIRED = ".".join(str(part) for part in NODE_MINIMUM[:2])
# The platforms that setup supports, for messages: "setup requires ...".
SUPPORTED_PLATFORMS = " or ".join(system.name for system in PLATFORMS.values())
NODE_FIX = (
    f"Install Node.js {NODE_REQUIRED} or newer with npm, from your distribution, nvm or "
    "https://nodejs.org/"
)
SETUP_FIX = "Run `md-doc-converter setup`"
REINSTALL_FIX = "Run `md-doc-converter setup --force`"
# The npm commands that the Pandoc filters run with Node.js, by package.
FILTER_COMMANDS = ((MERMAID_CLI, "mmdc"),)
# Packages that every render reads: KaTeX for math, and the GitHub Markdown stylesheet.
RENDER_PACKAGES = ("katex", "github-markdown-css")
# The PDF printer's package.
PDF_PACKAGE = "puppeteer"


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "ok", "warn" or "error"
    detail: str
    fix: str = ""


def _version(command: list[str]) -> tuple[int, ...] | None:
    """Return the first dotted version a command reports, padded to three parts."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    except OSError, subprocess.SubprocessError:
        return None
    match = re.search(r"(\d+(?:\.\d+)+)", result.stdout or result.stderr or "")
    if result.returncode or match is None:
        return None
    version = tuple(int(part) for part in match.group(1).split("."))
    return version + (0,) * (3 - len(version))


def _text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def installed_version(executable: Path) -> str | None:
    """The version an installed executable reports, or None when it is missing or silent."""
    if not _executable(executable):
        return None
    version = _version([str(executable), "--version"])
    return None if version is None else _text(version)


def _executable(path: Path) -> bool:
    return path.is_file() and executables.runnable(path)


def check_platform() -> Check:
    system = current_platform()
    if system is not None:
        return Check("platform", "ok", system.name)
    return Check(
        "platform",
        "warn",
        f"{sys.platform} {platform.machine()} is not supported; setup requires "
        f"{SUPPORTED_PLATFORMS}",
    )


def check_pandoc() -> Check:
    """The Pandoc that conversion and rendering use: setup's pinned copy, first on PATH."""
    pandoc = executables.which("pandoc")
    if pandoc is None:
        return Check("pandoc", "error", "not installed", PANDOC_FIX)
    version = installed_version(Path(pandoc))
    if version is None:
        return Check("pandoc", "error", f"{pandoc} did not report a version", PANDOC_FIX)
    if version != PANDOC_VERSION:
        return Check(
            "pandoc",
            "warn",
            f"{version} ({pandoc}); md-doc-converter is tested with {PANDOC_VERSION}, "
            "so converted Markdown may differ",
            PANDOC_FIX,
        )
    return Check("pandoc", "ok", f"{version} ({pandoc})")


def check_node() -> Check:
    node = executables.which("node")
    if node is None:
        return Check("node", "error", "not found on PATH", NODE_FIX)
    version = _version([node, "--version"])
    if version is None:
        return Check("node", "error", f"{node} did not report a version", NODE_FIX)
    if version < NODE_MINIMUM:
        detail = f"{_text(version)} is older than {NODE_REQUIRED} ({node})"
        return Check("node", "error", detail, NODE_FIX)
    return Check("node", "ok", f"{_text(version)} ({node})")


def check_npm(tools: Toolchain) -> Check:
    npm = executables.which("npm")
    if npm is not None:
        return Check("npm", "ok", npm)
    # npm is only needed to install or update the renderer packages.
    status = "warn" if packages_current(tools) else "error"
    return Check("npm", status, "not found on PATH; setup needs it", NODE_FIX)


def check_packages(tools: Toolchain, *, pdf: bool = True) -> Check:
    if not packages_current(tools):
        return Check(
            "npm packages", "error", f"not installed or out of date in {tools.node_dir}", SETUP_FIX
        )
    missing = [
        command
        for package, command in FILTER_COMMANDS
        if tools.node_script(package, command) is None
    ]
    packages = [*RENDER_PACKAGES, *([PDF_PACKAGE] if pdf else [])]
    missing += [
        name for name in packages if not (tools.node_modules / name / "package.json").is_file()
    ]
    if missing:
        return Check("npm packages", "error", "missing " + ", ".join(missing), REINSTALL_FIX)
    return Check("npm packages", "ok", str(tools.node_modules))


def check_browser(tools: Toolchain) -> Check:
    cache = tools.puppeteer_cache
    builds = [path.parents[1].name for path in tools.browser_builds() if _executable(path)]
    if not builds:
        return Check("browser", "error", f"no {BROWSER} in {cache}", REINSTALL_FIX)
    prefix = f"{tools.system.browser_platform}-"
    versions = ", ".join(build.removeprefix(prefix) for build in builds)
    return Check("browser", "ok", f"{BROWSER} {versions} ({cache})")


def _optional(name: str, path: str | None, purpose: str, fix: str) -> Check:
    if path is not None:
        return Check(name, "ok", path)
    return Check(name, "warn", f"not found; {purpose}", fix)


def check_git() -> Check:
    return _optional(
        "git",
        executables.which("git"),
        "Git-ignored files are included when rendering a folder",
        "Install Git",
    )


def check_libreoffice() -> Check:
    return _optional(
        "libreoffice",
        executables.libreoffice(),
        "EMF/WMF drawings in Word documents are kept in their original format",
        "Install LibreOffice",
    )


def check_pdftocairo() -> Check:
    return _optional(
        "pdftocairo",
        executables.which("pdftocairo"),
        "the PDF fallback for EMF/WMF drawings is unavailable",
        "Install Poppler (`poppler-utils` in most distributions)",
    )


def render_checks(tools: Toolchain, *, pdf: bool) -> list[Check]:
    """Checks that must pass before rendering; fast enough to run for every render."""
    return [
        check_pandoc(),
        check_node(),
        check_packages(tools, pdf=pdf),
        check_browser(tools),
    ]


def render_problems(tools: Toolchain, *, pdf: bool) -> list[str]:
    return [
        f"{check.name}: {check.detail}. {check.fix}".rstrip()
        for check in render_checks(tools, pdf=pdf)
        if check.status == "error"
    ]


def doctor_checks(tools: Toolchain) -> list[Check]:
    return [
        check_platform(),
        check_pandoc(),
        check_node(),
        check_npm(tools),
        check_packages(tools),
        check_browser(tools),
        check_git(),
        check_libreoffice(),
        check_pdftocairo(),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter doctor",
        description="Check the prerequisites for conversion and rendering.",
    )
    parser.parse_args(argv)
    tools = toolchain()
    report = doctor_checks(tools)
    print(f"md-doc-converter {package_version()} (Python {platform.python_version()})")
    print(f"Toolchain: {tools.root}")
    print()
    width = max(len(check.name) for check in report)
    for check in report:
        print(f"  {check.status:<6} {check.name:<{width}}  {check.detail}")
        if check.fix and check.status != "ok":
            print(f"  {'':<6} {'':<{width}}  -> {check.fix}")
    print()
    errors = sum(check.status == "error" for check in report)
    if errors:
        print(f"Not ready to render: {errors} problem(s) listed above.")
        return 1
    print("Ready: conversion and rendering prerequisites are installed.")
    return 0
