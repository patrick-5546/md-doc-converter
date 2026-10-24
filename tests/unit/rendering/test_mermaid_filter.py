"""The Mermaid Lua filter, which renders every diagram with mermaid-cli into an inline SVG image.

The tests run the pinned Pandoc with the filter and a fake mmdc, which writes each diagram's text
as its SVG and fails on a diagram that contains FAIL.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from md_doc_converter.resources import asset_path
from tests.support.fake_tools import FakeTools

DATA_URI = "data:image/svg+xml;base64,"
Convert = Callable[[str, Path], subprocess.CompletedProcess[str]]


def images(document: object) -> list[dict[str, object]]:
    """Every Pandoc JSON Image in document order, as id, classes and decoded SVG."""
    found: list[dict[str, object]] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("t") == "Image":
                [identifier, classes, _], _, [source, _] = node["c"]
                assert isinstance(source, str)
                assert source.startswith(DATA_URI), source
                svg = base64.b64decode(source.removeprefix(DATA_URI)).decode()
                found.append({"id": identifier, "classes": classes, "svg": svg})
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)
    return found


def mmdc_calls(fake_tools: FakeTools) -> list[list[str]]:
    """The arguments of each recorded mmdc run."""
    calls: list[list[str]] = []
    for call in fake_tools.calls():
        arguments = call["arguments"]
        assert isinstance(arguments, list)
        calls.append([str(argument) for argument in arguments])
    return calls


@pytest.fixture
def mmdc(tmp_path: Path) -> Path:
    """A fake mermaid-cli script, which the fake node runs as mmdc, recorded by fake_tools."""
    script = tmp_path / "mmdc.js"
    script.write_text("fake:mmdc", encoding="utf-8")
    return script


@pytest.fixture
def convert(pandoc: str, fake_tools: FakeTools) -> Convert:
    """Convert Markdown to Pandoc JSON through the filter, which runs the given mmdc script with
    the fake node."""
    fake_tools.write("node")

    def run(markdown: str, mmdc: Path) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "MD_DOC_CONVERTER_NODE": str(fake_tools.path("node")),
            "MD_DOC_CONVERTER_MMDC": str(mmdc),
            "MD_DOC_CONVERTER_MERMAID_CONFIG": "/config/mermaid.json",
        }
        return subprocess.run(
            [pandoc, "--from=markdown", "--to=json", f"--lua-filter={asset_path('mermaid.lua')}"],
            input=markdown,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def test_all_diagrams_render_in_one_run_and_keep_their_place(
    convert: Convert, mmdc: Path, fake_tools: FakeTools
) -> None:
    result = convert(
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "```python\nprint('kept')\n```\n\n"
        "- item\n\n  ```{.mermaid #sequence}\n  sequenceDiagram\n    A->>B: Hi\n  ```\n",
        mmdc,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert images(document) == [
        {"id": "", "classes": ["mermaid-diagram"], "svg": "<svg>flowchart LR\n  A --> B</svg>"},
        {
            "id": "sequence",
            "classes": ["mermaid-diagram"],
            "svg": "<svg>sequenceDiagram\n  A->>B: Hi</svg>",
        },
    ]
    assert document["blocks"][1]["t"] == "CodeBlock"
    [call] = mmdc_calls(fake_tools)
    assert call[call.index("--configFile") + 1] == "/config/mermaid.json"
    assert "--quiet" in call
    assert "--artefacts" in call


def test_documents_without_diagrams_need_no_mmdc(convert: Convert, tmp_path: Path) -> None:
    # The mmdc given does not exist: running it would fail the conversion.
    result = convert("```python\nprint('kept')\n```\n", tmp_path / "missing")
    assert result.returncode == 0, result.stderr
    assert images(json.loads(result.stdout)) == []


def test_missing_mmdc_names_setup(convert: Convert, tmp_path: Path) -> None:
    result = convert("```mermaid\nflowchart LR\n```\n", tmp_path / "missing")
    assert result.returncode != 0
    assert "mermaid-cli (mmdc) is not installed" in result.stderr
    assert "md-doc-converter setup" in result.stderr


def test_missing_node_is_named(pandoc: str, mmdc: Path) -> None:
    environment = {**os.environ, "MD_DOC_CONVERTER_NODE": "", "MD_DOC_CONVERTER_MMDC": str(mmdc)}
    result = subprocess.run(
        [pandoc, "--from=markdown", "--to=json", f"--lua-filter={asset_path('mermaid.lua')}"],
        input="```mermaid\nflowchart LR\n```\n",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Node.js, which runs mermaid-cli, was not found on PATH" in result.stderr


def test_a_failing_diagram_is_named_with_the_mermaid_message(
    convert: Convert, mmdc: Path, fake_tools: FakeTools
) -> None:
    result = convert(
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "```mermaid\n---\ntitle: Broken\n---\ngraph TD\n  FAIL\n```\n",
        mmdc,
    )
    assert result.returncode != 0
    assert "Mermaid diagram 2 of 2 (graph TD) could not be rendered:" in result.stderr
    assert "Error: Parse error on line 2" in result.stderr
    assert "file:///mermaid.mjs" not in result.stderr
    calls = mmdc_calls(fake_tools)
    assert len(calls) == 3
    assert "--artefacts" in calls[0]
    assert all("--artefacts" not in call for call in calls[1:])


def test_a_diagram_with_a_fence_line_renders_on_its_own(
    convert: Convert, mmdc: Path, fake_tools: FakeTools
) -> None:
    result = convert(
        "````mermaid\nflowchart LR\n```\n````\n\n```mermaid\nflowchart TD\n```\n", mmdc
    )
    assert result.returncode == 0, result.stderr
    assert [image["svg"] for image in images(json.loads(result.stdout))] == [
        "<svg>flowchart LR\n```</svg>",
        "<svg>flowchart TD</svg>",
    ]
    batch, alone = mmdc_calls(fake_tools)
    assert "--artefacts" in batch
    assert "--artefacts" not in alone
