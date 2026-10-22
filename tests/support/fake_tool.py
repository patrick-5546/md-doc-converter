"""A stand-in for pandoc, node, npm, mmdc and Puppeteer's command line.

`fake_tools.write_fake_tools` writes executables that call `main` with their configuration.
Each fake answers ``--version`` with its configured version, records every other call
(arguments, working directory and selected environment variables) in a JSON-lines log, and
writes the files that the real tool would:

- pandoc answers the preprocessor's ``--to=json`` parses with headings only, so test inputs
  should be plain Markdown without links or reference definitions, and otherwise writes its
  input wrapped in the inlined styles as the HTML;
- node writes a placeholder PDF;
- npm installs the package files that rendering reads, an mmdc and a Puppeteer command line;
- mmdc writes each diagram's text as its SVG, and fails on a diagram that contains ``FAIL``;
- puppeteer installs a browser executable.

Setting ``FAKE_TOOL_FAIL`` to a tool's name makes that tool exit with status 3.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TypedDict


class Config(TypedDict):
    log: str
    versions: dict[str, str]
    variables: list[str]
    browser_path: str
    packages: dict[str, str]


def _headings(markdown: str) -> dict[str, object]:
    """A Pandoc JSON document with the Markdown's ATX headings and their identifiers."""
    blocks: list[object] = []
    used: set[str] = set()
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if not heading:
            continue
        title = heading[2]
        base = re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")
        identifier, index = base, 0
        while identifier in used:
            index += 1
            identifier = f"{base}-{index}"
        used.add(identifier)
        blocks.append(
            {
                "t": "Header",
                "c": [len(heading[1]), [identifier, [], []], [{"t": "Str", "c": title}]],
            }
        )
    return {"blocks": blocks}


def _option(arguments: list[str], name: str) -> str:
    return arguments[arguments.index(name) + 1]


def _mmdc(arguments: list[str], record: dict[str, object]) -> int:
    source = Path(_option(arguments, "--input")).read_text(encoding="utf-8")
    output = Path(_option(arguments, "--output"))
    if "--artefacts" in arguments:
        diagrams = re.findall(r"^```mermaid\n(.*?)\n```$", source, re.DOTALL | re.MULTILINE)
        artefacts = Path(_option(arguments, "--artefacts"))
        images = [
            artefacts / f"{output.stem}-{number}.svg" for number in range(1, len(diagrams) + 1)
        ]
    else:
        diagrams, images = [source], [output]
    record["diagrams"] = diagrams
    if any("FAIL" in diagram for diagram in diagrams):
        sys.stderr.write("Error: Parse error on line 2\n    at parse (file:///mermaid.mjs:1:1)\n")
        return 1
    for diagram, image in zip(diagrams, images, strict=True):
        image.write_text(f"<svg>{diagram}</svg>", encoding="utf-8")
    return 0


def _npm(config: Config) -> None:
    modules = Path.cwd() / "node_modules"
    executable = modules / ".bin" / "mmdc"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    # The fake Puppeteer command line is this executable under another name.
    shutil.copy(sys.argv[0], modules / ".bin" / "puppeteer")
    for name, text in config["packages"].items():
        (modules / name).parent.mkdir(parents=True, exist_ok=True)
        (modules / name).write_text(text, encoding="utf-8")


def main(config: Config) -> int:
    tool = Path(sys.argv[0]).name
    arguments = sys.argv[1:]
    if arguments[:1] == ["--version"]:
        print(config["versions"][tool])
        return 0
    if tool == "pandoc" and "--to=json" in arguments:
        print(json.dumps(_headings(sys.stdin.read())))
        return 0
    if os.environ.get("FAKE_TOOL_FAIL") == tool:
        return 3
    status = 0
    record: dict[str, object] = {
        "tool": tool,
        "path": sys.argv[0],
        "arguments": arguments,
        "cwd": str(Path.cwd()),
        "environment": {name: os.environ.get(name) for name in config["variables"]},
    }
    if tool == "pandoc":
        styles = Path(_option(arguments, "--include-in-header")).read_text(encoding="utf-8")
        markdown = Path(arguments[-1]).read_text(encoding="utf-8")
        record["input"] = markdown
        record["styles"] = styles
        Path(_option(arguments, "-o")).write_text(
            f"<html>{styles}{markdown}</html>", encoding="utf-8"
        )
    elif tool == "node":
        Path(_option(arguments, "-o")).write_bytes(b"%PDF-fake")
    elif tool == "mmdc":
        status = _mmdc(arguments, record)
    elif tool == "npm":
        _npm(config)
    elif tool == "puppeteer":
        browser = Path(os.environ["PUPPETEER_CACHE_DIR"]) / config["browser_path"]
        browser.parent.mkdir(parents=True, exist_ok=True)
        browser.write_text("#!/bin/sh\n", encoding="utf-8")
        browser.chmod(0o755)
    with Path(config["log"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    return status
