"""Disposable stand-ins for the native renderer tools.

The fakes record each call in a JSON-lines log. The fake Pandoc answers the
preprocessor's ``--to=json`` parses with headings only, so test inputs should be
plain Markdown without links or reference definitions. The fake mmdc writes each
diagram's text as its SVG and fails on diagrams that contain ``FAIL``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from md_doc_converter.toolchain.environment import BROWSER, Toolchain, write_stamp

VERSIONS = {
    "pandoc": "pandoc 3.12.1",
    "node": "v24.0.0",
    "npm": "11.0.0",
}
KATEX_VERSION = "0.19.0"
BROWSER_BUILD = "linux-131.0.6778.204"
# Where Puppeteer's cache keeps a chrome-headless-shell build.
BROWSER_PATH = f"{BROWSER}/{BROWSER_BUILD}/{BROWSER}-linux64/{BROWSER}"
RECORDED_VARIABLES = (
    "PATH",
    "PUPPETEER_CACHE_DIR",
    "MD_DOC_CONVERTER_MMDC",
    "MD_DOC_CONVERTER_MERMAID_CONFIG",
    "MD_DOC_CONVERTER_NODE_MODULES",
)

FAKE_TOOL = """#!{python}
import json, os, re, shutil, sys
from pathlib import Path

log = Path({log!r})
versions = {versions!r}
tool = Path(sys.argv[0]).name
arguments = sys.argv[1:]
if arguments[:1] == ["--version"]:
    print(versions[tool])
    sys.exit(0)
if tool == "pandoc" and "--to=json" in arguments:
    blocks, used = [], set()
    for line in sys.stdin.read().splitlines():
        heading = re.match(r"^(#{{1,6}})\\s+(.*)", line)
        if not heading:
            continue
        title = heading.group(2)
        base = re.sub(r"[^\\w -]", "", title.lower()).replace(" ", "-")
        identifier, index = base, 0
        while identifier in used:
            index += 1
            identifier = base + "-" + str(index)
        used.add(identifier)
        blocks.append({{"t": "Header", "c": [len(heading.group(1)), [identifier, [], []],
                       [{{"t": "Str", "c": title}}]]}})
    print(json.dumps({{"blocks": blocks}}))
    sys.exit(0)
if os.environ.get("FAKE_TOOL_FAIL") == tool:
    sys.exit(3)
status = 0
record = {{
    "tool": tool,
    "path": sys.argv[0],
    "arguments": arguments,
    "cwd": os.getcwd(),
    "environment": {{name: os.environ.get(name) for name in {variables!r}}},
}}
if tool == "pandoc":
    styles = Path(arguments[arguments.index("--include-in-header") + 1]).read_text()
    record["input"] = Path(arguments[-1]).read_text()
    record["styles"] = styles
    Path(arguments[arguments.index("-o") + 1]).write_text(
        "<html>" + styles + record["input"] + "</html>"
    )
elif tool == "node":
    Path(arguments[arguments.index("-o") + 1]).write_bytes(b"%PDF-fake")
elif tool == "mmdc":
    def option(name):
        return arguments[arguments.index(name) + 1]
    source = Path(option("--input")).read_text()
    output = Path(option("--output"))
    if "--artefacts" in arguments:
        diagrams = re.findall(r"^```mermaid\\n(.*?)\\n```$", source, re.S | re.M)
        images = [
            Path(option("--artefacts")) / (output.stem + "-" + str(number) + ".svg")
            for number in range(1, len(diagrams) + 1)
        ]
    else:
        diagrams, images = [source], [output]
    record["diagrams"] = diagrams
    if any("FAIL" in diagram for diagram in diagrams):
        sys.stderr.write("Error: Parse error on line 2\\n    at parse (file:///mermaid.mjs:1:1)\\n")
        status = 1
    else:
        for diagram, image in zip(diagrams, images):
            image.write_text("<svg>" + diagram + "</svg>")
elif tool == "npm":
    modules = Path.cwd() / "node_modules"
    executable = modules / ".bin" / "mmdc"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\\n")
    executable.chmod(0o755)
    # The fake Puppeteer CLI is this script under another name.
    shutil.copy(sys.argv[0], modules / ".bin" / "puppeteer")
    for package, manifest in (("pagedjs-cli", {{}}), ("katex", {{"version": {katex!r}}})):
        (modules / package).mkdir(exist_ok=True)
        (modules / package / "package.json").write_text(json.dumps(manifest))
elif tool == "puppeteer":
    browser = Path(os.environ["PUPPETEER_CACHE_DIR"]) / {browser_path!r}
    browser.parent.mkdir(parents=True, exist_ok=True)
    browser.write_text("#!/bin/sh\\n")
    browser.chmod(0o755)
with log.open("a") as stream:
    stream.write(json.dumps(record) + "\\n")
sys.exit(status)
"""


def write_fake_tools(
    directory: Path,
    log: Path,
    names: tuple[str, ...] = ("pandoc", "node", "npm"),
    versions: dict[str, str] | None = None,
) -> Path:
    """Create fake executables, by default pandoc, node and npm, in a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    selected = {**VERSIONS, **(versions or {})}
    script = FAKE_TOOL.format(
        python=sys.executable,
        log=str(log),
        versions=selected,
        variables=RECORDED_VARIABLES,
        browser_path=BROWSER_PATH,
        katex=KATEX_VERSION,
    )
    for name in names:
        executable = directory / name
        executable.write_text(script)
        executable.chmod(0o755)
    return directory


def install_fake_toolchain(root: Path, log: Path) -> Toolchain:
    """Create a complete, current toolchain, with the pinned Pandoc, whose executables are fakes."""
    tools = Toolchain(root)
    executable = tools.node_executable("mmdc")
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    manifests: dict[str, dict[str, str]] = {"pagedjs-cli": {}, "katex": {"version": KATEX_VERSION}}
    for package, manifest in manifests.items():
        (tools.node_modules / package).mkdir()
        (tools.node_modules / package / "package.json").write_text(json.dumps(manifest))
    browser = tools.puppeteer_cache / BROWSER_PATH
    browser.parent.mkdir(parents=True)
    browser.write_text("#!/bin/sh\n")
    browser.chmod(0o755)
    write_fake_tools(tools.bin_dir, log, ("pandoc",))
    write_stamp(tools)
    return tools


def read_log(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
