"""Disposable stand-ins for the native renderer tools, and a fake toolchain.

The executables are small scripts that run `tests.support.fake_tool`, which describes what
each fake does; every call is recorded in a JSON-lines log.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from md_doc_converter.toolchain.environment import (
    BROWSER,
    MERMAID_CLI,
    Platform,
    Toolchain,
    host_platform,
    write_stamp,
)
from tests.support.fake_tool import Config
from tests.support.project import PROJECT_ROOT

VERSIONS = {
    "pandoc": "pandoc 3.12.1",
    "node": "v24.0.0",
    "npm": "11.0.0",
}
# The files that rendering reads from the installed npm packages, by path in node_modules, with
# the scripts of the npm commands that md-doc-converter runs, which the fake node runs.
PACKAGE_FILES = {
    f"{MERMAID_CLI}/package.json": json.dumps({"bin": {"mmdc": "src/cli.js"}}),
    f"{MERMAID_CLI}/src/cli.js": "fake:mmdc",
    "puppeteer/package.json": json.dumps({"bin": {"puppeteer": "lib/puppeteer/node/cli.js"}}),
    "puppeteer/lib/puppeteer/node/cli.js": "fake:puppeteer",
    "katex/package.json": "{}",
    "katex/dist/katex.min.css": (
        "@font-face{font-family:KaTeX_Main;src:url(fonts/KaTeX_Main-Regular.woff2) "
        'format("woff2"),url(fonts/KaTeX_Main-Regular.woff) format("woff")}'
        ".katex{font:normal 1.21em KaTeX_Main}"
    ),
    "katex/dist/fonts/KaTeX_Main-Regular.woff2": "wOF2",
    "github-markdown-css/package.json": "{}",
    "github-markdown-css/github-markdown-light.css": ".markdown-body { color: #1f2328; }\n",
}
BROWSER_VERSION = "131.0.6778.204"


def browser_path(system: Platform) -> str:
    """Where Puppeteer's cache keeps a chrome-headless-shell build for *system*."""
    build = f"{system.browser_platform}-{BROWSER_VERSION}"
    return f"{BROWSER}/{build}/{BROWSER}-{system.browser_folder}/{system.executable(BROWSER)}"


BROWSER_PATH = browser_path(host_platform())
RECORDED_VARIABLES = (
    "PATH",
    "PUPPETEER_CACHE_DIR",
    "MD_DOC_CONVERTER_NODE",
    "MD_DOC_CONVERTER_MMDC",
    "MD_DOC_CONVERTER_MERMAID_CONFIG",
    "MD_DOC_CONVERTER_NODE_MODULES",
    "MD_DOC_CONVERTER_KATEX_CSS",
)

SHIM = """#!{python}
import sys
sys.path.insert(0, {root!r})
from tests.support.fake_tool import main
sys.exit(main({config!r}))
"""


def write_fake_tools(
    directory: Path,
    log: Path,
    names: tuple[str, ...] = ("pandoc", "node", "npm"),
    versions: dict[str, str] | None = None,
) -> Path:
    """Create fake executables, by default pandoc, node and npm, in a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    config: Config = {
        "log": str(log),
        "versions": {**VERSIONS, **(versions or {})},
        "variables": list(RECORDED_VARIABLES),
        "browser_path": BROWSER_PATH,
        "packages": PACKAGE_FILES,
    }
    script = SHIM.format(python=sys.executable, root=str(PROJECT_ROOT), config=config)
    for name in names:
        executable = directory / name
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o755)
    return directory


def install_fake_toolchain(root: Path, log: Path) -> Toolchain:
    """Create a complete, current toolchain, with the pinned Pandoc, whose executables are fakes."""
    tools = Toolchain(root)
    write_packages(tools)
    browser = tools.puppeteer_cache / BROWSER_PATH
    browser.parent.mkdir(parents=True)
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    browser.chmod(0o755)
    write_fake_tools(tools.bin_dir, log, ("pandoc",))
    write_stamp(tools)
    return tools


def write_packages(tools: Toolchain) -> None:
    """Write the npm package files that rendering reads."""
    for name, text in PACKAGE_FILES.items():
        path = tools.node_modules / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def read_log(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@dataclass(frozen=True)
class FakeTools:
    """Fake executables in one directory, which record their calls in one log."""

    bin: Path
    log: Path

    def write(self, *names: str, versions: dict[str, str] | None = None) -> Path:
        """Write the named fakes, by default pandoc, node and npm; return their directory."""
        return write_fake_tools(self.bin, self.log, names or ("pandoc", "node", "npm"), versions)

    def path(self, name: str) -> Path:
        """The fake executable *name*, which `write` writes."""
        return self.bin / host_platform().executable(name)

    def toolchain(self, root: Path) -> Toolchain:
        """Install a complete, current fake toolchain at root, logging to the same log."""
        return install_fake_toolchain(root, self.log)

    def calls(self, tool: str | None = None) -> list[dict[str, object]]:
        """The recorded calls, of one tool or of all."""
        return [call for call in read_log(self.log) if tool is None or call["tool"] == tool]
