"""Disposable stand-ins for the native renderer tools.

The fakes record each call in a JSON-lines log. The fake Pandoc answers the
preprocessor's ``--to=json`` parses with headings only, so test inputs should be
plain Markdown without links or reference definitions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from md_doc_converter.toolchain.environment import Toolchain, write_stamp

VERSIONS = {
    "pandoc": "pandoc 3.12.1",
    "node": "v24.0.0",
    "npm": "11.0.0",
    "pandoc-katex": "pandoc-katex 0.1.11\nkatex 0.16.4",
}
RECORDED_VARIABLES = (
    "PATH",
    "PUPPETEER_CACHE_DIR",
    "MERMAID_FILTER_CMD_MMDC",
    "MERMAID_FILTER_FORMAT",
    "MERMAID_FILTER_MERMAID_CONFIG",
)

FAKE_TOOL = """#!{python}
import json, os, re, sys
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
elif tool == "npm":
    modules = Path.cwd() / "node_modules"
    for name in ("mermaid-filter", "mmdc"):
        executable = modules / ".bin" / name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\\n")
        executable.chmod(0o755)
    (modules / "pagedjs-cli").mkdir(exist_ok=True)
    (modules / "pagedjs-cli" / "package.json").write_text("{{}}")
    (Path(os.environ["PUPPETEER_CACHE_DIR"]) / "chrome").mkdir(parents=True, exist_ok=True)
with log.open("a") as stream:
    stream.write(json.dumps(record) + "\\n")
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
        python=sys.executable, log=str(log), versions=selected, variables=RECORDED_VARIABLES
    )
    for name in names:
        executable = directory / name
        executable.write_text(script)
        executable.chmod(0o755)
    return directory


def install_fake_toolchain(root: Path, log: Path) -> Toolchain:
    """Create a complete, current toolchain, with the pinned Pandoc, whose executables are fakes."""
    tools = Toolchain(root)
    for name in ("mermaid-filter", "mmdc"):
        executable = tools.node_executable(name)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    (tools.node_modules / "pagedjs-cli").mkdir()
    (tools.node_modules / "pagedjs-cli" / "package.json").write_text("{}")
    (tools.puppeteer_cache / "chrome").mkdir(parents=True)
    write_fake_tools(tools.bin_dir, log, ("pandoc", "pandoc-katex"))
    write_stamp(tools)
    return tools


def read_log(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]
