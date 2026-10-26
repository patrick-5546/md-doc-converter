"""A stand-in for pandoc, node, npm, and the mermaid-cli and Puppeteer command lines.

`fake_tools.write_fake_tools` writes executables that call `main` with their configuration.
Each fake answers ``--version`` with its configured version, records every other call
(arguments, working directory and selected environment variables) in a JSON-lines log, and
writes the files that the real tool would:

- pandoc answers the preprocessor's ``--to=json`` parses with headings only, so test inputs
- pandoc answers the preprocessor's ``--to=json`` parses with headings only, so test inputs
  should be plain Markdown without links or reference definitions; answers a folder's
  heading-identifier run by reading its pages' headings as one document; and otherwise writes
  the headings of the document it reads, as Markdown, wrapped in the inlined styles as the HTML;
- node runs a script that holds ``fake:<command>`` as that command, recorded under its name;
  renders each formula that katex-render.mjs reads as its TeX in a katex tag, marked if
  displayed; and otherwise writes a placeholder PDF, as the PDF printer would;
- npm installs the package files that rendering reads, with the scripts of mermaid-cli's mmdc
  and Puppeteer's command line;
- mmdc writes each diagram's text as its SVG, or a diagram that is SVG as it is, and fails on a
  diagram that contains ``FAIL``;
- puppeteer installs a browser executable.

Setting ``FAKE_TOOL_FAIL`` to a tool's name makes that tool exit with status 3.
"""

from __future__ import annotations

import json
import os
import re
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
    return {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": blocks}


def _identified(document: object) -> dict[str, object]:
    """What assets/heading-ids.lua makes of *document*: its pages' headings, read as one."""
    pages = document.get("blocks", []) if isinstance(document, dict) else []
    return _headings("".join(_heading_lines({"blocks": page["c"][1]}) for page in pages))


def _heading_lines(document: object) -> str:
    """The headings of a document that `_headings` made, as Markdown."""
    lines: list[str] = []
    blocks = document.get("blocks", []) if isinstance(document, dict) else []
    for block in blocks if isinstance(blocks, list) else []:
        if isinstance(block, dict) and block.get("t") == "Header":
            level, _attributes, inlines = block["c"]
            lines.append("#" * level + " " + "".join(inline["c"] for inline in inlines))
    return "".join(f"{line}\n" for line in lines)


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
        # A diagram that is already SVG, such as one that names its type, is its own image.
        svg = diagram if diagram.startswith("<svg") else f"<svg>{diagram}</svg>"
        image.write_text(svg, encoding="utf-8")
    return 0


def _katex(record: dict[str, object]) -> None:
    formulas = json.loads(sys.stdin.read())
    record["formulas"] = formulas
    print(json.dumps([f"<katex{' display' * display}>{tex}</katex>" for tex, display in formulas]))


def _npm(config: Config) -> None:
    modules = Path.cwd() / "node_modules"
    for name, text in config["packages"].items():
        (modules / name).parent.mkdir(parents=True, exist_ok=True)
        (modules / name).write_text(text, encoding="utf-8")


def _script_command(arguments: list[str]) -> str | None:
    """The command that node's script, its first argument, stands in for, if it is a fake."""
    script = Path(arguments[0]) if arguments else None
    if script is None or not script.is_file():
        return None
    text = script.read_text(encoding="utf-8")
    return text.removeprefix("fake:") if text.startswith("fake:") else None


def main(config: Config) -> int:
    tool = Path(sys.argv[0]).stem
    arguments = sys.argv[1:]
    if arguments[:1] == ["--version"]:
        print(config["versions"][tool])
        return 0
    if tool == "node" and (command := _script_command(arguments)) is not None:
        tool, arguments = command, arguments[1:]
    if tool == "pandoc" and "--to=json" in arguments:
        source = sys.stdin.read()
        document = _identified(json.loads(source)) if "--from=json" in arguments else None
        print(json.dumps(document or _headings(source)))
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
        document = Path(arguments[-1]).read_text(encoding="utf-8")
        record["input"] = document
        record["styles"] = styles
        Path(_option(arguments, "-o")).write_text(
            f"<html>{styles}{_heading_lines(json.loads(document))}</html>", encoding="utf-8"
        )
    elif tool == "node" and arguments[0].endswith("katex-render.mjs"):
        _katex(record)
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
