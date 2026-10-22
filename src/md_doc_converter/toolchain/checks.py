"""Prerequisite checks shared by rendering and ``md-doc-converter doctor``."""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from md_doc_converter.resources import package_version
from md_doc_converter.toolchain.environment import (
    NODE_MINIMUM,
    PANDOC_KATEX_VERSION,
    PANDOC_VERSION,
    Toolchain,
    packages_current,
    toolchain,
)

PANDOC_FIX = "Run `md-doc-converter setup`, or `setup --no-render` for conversion only"
NODE_FIX = (
    "Install Node.js 22.12 or newer with npm, from your distribution, nvm or https://nodejs.org/"
)
SETUP_FIX = "Run `md-doc-converter setup`"
REINSTALL_FIX = "Run `md-doc-converter setup --force`"
# Executables that Pandoc runs as filters or that mermaid-filter calls.
FILTER_EXECUTABLES = ("mermaid-filter", "mmdc")
PDF_PACKAGE = "pagedjs-cli"


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
    except (OSError, subprocess.SubprocessError):
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
    return path.is_file() and shutil.which(path.name, path=str(path.parent)) is not None


def check_platform() -> Check:
    machine = platform.machine()
    if sys.platform.startswith("linux") and machine.lower() in {"x86_64", "amd64"}:
        return Check("platform", "ok", f"Linux {machine}")
    return Check(
        "platform",
        "warn",
        f"{sys.platform} {machine} is not supported; setup requires Linux x86_64",
    )


def check_pandoc() -> Check:
    """The Pandoc that conversion and rendering use: setup's pinned copy, first on PATH."""
    pandoc = shutil.which("pandoc")
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
    node = shutil.which("node")
    if node is None:
        return Check("node", "error", "not found on PATH", NODE_FIX)
    version = _version([node, "--version"])
    if version is None:
        return Check("node", "error", f"{node} did not report a version", NODE_FIX)
    if version < NODE_MINIMUM:
        return Check("node", "error", f"{_text(version)} is older than 22.12 ({node})", NODE_FIX)
    return Check("node", "ok", f"{_text(version)} ({node})")


def check_npm(tools: Toolchain) -> Check:
    npm = shutil.which("npm")
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
    missing = [name for name in FILTER_EXECUTABLES if not _executable(tools.node_executable(name))]
    if pdf and not (tools.node_modules / PDF_PACKAGE / "package.json").is_file():
        missing.append(PDF_PACKAGE)
    if missing:
        return Check("npm packages", "error", "missing " + ", ".join(missing), REINSTALL_FIX)
    return Check("npm packages", "ok", str(tools.node_modules))


def check_browser(tools: Toolchain) -> Check:
    cache = tools.puppeteer_cache
    browsers = (
        sorted(path.name for path in cache.iterdir() if path.is_dir()) if cache.is_dir() else []
    )
    if not browsers:
        return Check("browser", "error", f"no Puppeteer browser in {cache}", REINSTALL_FIX)
    return Check("browser", "ok", f"{', '.join(browsers)} ({cache})")


def check_pandoc_katex(tools: Toolchain, *, verify_version: bool = True) -> Check:
    binary = tools.pandoc_katex
    if not _executable(binary):
        return Check("pandoc-katex", "error", f"not installed at {binary}", SETUP_FIX)
    if verify_version and installed_version(binary) != PANDOC_KATEX_VERSION:
        return Check(
            "pandoc-katex",
            "error",
            f"{binary} is not version {PANDOC_KATEX_VERSION}",
            REINSTALL_FIX,
        )
    return Check("pandoc-katex", "ok", f"{PANDOC_KATEX_VERSION} ({binary})")


def _optional(name: str, candidates: tuple[str, ...], purpose: str, fix: str) -> Check:
    for candidate in candidates:
        path = shutil.which(candidate)
        if path is not None:
            return Check(name, "ok", path)
    return Check(name, "warn", f"not found; {purpose}", fix)


def check_git() -> Check:
    return _optional(
        "git", ("git",), "Git-ignored files are included when rendering a folder", "Install Git"
    )


def check_libreoffice() -> Check:
    return _optional(
        "libreoffice",
        ("libreoffice", "soffice"),
        "EMF/WMF drawings in Word documents are kept in their original format",
        "Install LibreOffice",
    )


def check_pdftocairo() -> Check:
    return _optional(
        "pdftocairo",
        ("pdftocairo",),
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
        check_pandoc_katex(tools, verify_version=False),
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
        check_pandoc_katex(tools),
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


if __name__ == "__main__":
    sys.exit(main())
