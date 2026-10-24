"""The KaTeX Lua filter, which renders all of a document's math in one Node.js run.

The tests run the pinned Pandoc with the filter and a fake node, which renders each formula that
katex-render.mjs reads as its TeX in a katex tag, marked if it is displayed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote

import pytest

from md_doc_converter.resources import asset_path
from tests.support.fake_tools import FakeTools

STYLESHEET = ".katex{font:normal 1.21em KaTeX_Main;color:#1f2328}"
STYLESHEET_URI = "data:text/css;charset=utf-8,"


class Convert(Protocol):
    def __call__(
        self, markdown: str, *arguments: str, tools: Path
    ) -> subprocess.CompletedProcess[str]: ...


def raw_html(node: object) -> list[str]:
    """The text of every raw HTML inline in *node*, in document order."""
    if isinstance(node, list):
        return [html for item in node for html in raw_html(item)]
    if not isinstance(node, dict):
        return []
    if node.get("t") == "RawInline":
        kind, html = node["c"]
        return [html] if kind == "html" else []
    return [html for value in node.values() for html in raw_html(value)]


@pytest.fixture
def convert(pandoc: str, tmp_path: Path) -> Convert:
    """Convert Markdown to Pandoc JSON through the filter, with only *tools* on the PATH."""
    stylesheet = tmp_path / "katex.css"
    stylesheet.write_text(STYLESHEET, encoding="utf-8")

    def run(markdown: str, *arguments: str, tools: Path) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PATH": str(tools),
            "MD_DOC_CONVERTER_KATEX_CSS": str(stylesheet),
        }
        return subprocess.run(
            [
                pandoc,
                "--from=markdown",
                "--to=json",
                f"--lua-filter={asset_path('katex.lua')}",
                *arguments,
            ],
            input=markdown,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def test_all_math_renders_in_one_node_run_in_its_place(
    convert: Convert, fake_tools: FakeTools
) -> None:
    result = convert(
        "% Title $t$\n\n"
        "Inline $a^2$, displayed:\n\n$$\\frac{1}{2}$$\n\n"
        "| Cell |\n|------|\n| $x$  |\n\n"
        "A note.[^1]\n\n[^1]: With $n$.\n",
        tools=fake_tools.write("node"),
    )

    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert raw_html(document["meta"]["title"]) == ["<katex>t</katex>"]
    assert raw_html(document["blocks"]) == [
        "<katex>a^2</katex>",
        "<katex display>\\frac{1}{2}</katex>",
        "<katex>x</katex>",
        "<katex>n</katex>",
    ]
    [call] = fake_tools.calls("node")
    formulas = call["formulas"]
    assert isinstance(formulas, list)
    assert sorted(map(tuple, formulas)) == [
        ("\\frac{1}{2}", True),
        ("a^2", False),
        ("n", False),
        ("t", False),
        ("x", False),
    ]
    [stylesheet] = document["meta"]["css"]["c"]
    assert stylesheet["t"] == "MetaString"
    assert stylesheet["c"].startswith(STYLESHEET_URI)
    assert unquote(stylesheet["c"].removeprefix(STYLESHEET_URI)) == STYLESHEET


def test_a_document_without_math_runs_no_node(convert: Convert, tmp_path: Path) -> None:
    # Nothing is on the PATH: running node would fail the conversion.
    result = convert("# Prices\n\nFrom $5 to $6.\n", tools=tmp_path / "empty")

    assert result.returncode == 0, result.stderr
    assert raw_html(json.loads(result.stdout)) == []
    assert "css" not in json.loads(result.stdout)["meta"]


def test_the_stylesheet_follows_the_documents_own(convert: Convert, fake_tools: FakeTools) -> None:
    result = convert("$x$\n", "--metadata=css:page.css", tools=fake_tools.write("node"))

    assert result.returncode == 0, result.stderr
    first, second = json.loads(result.stdout)["meta"]["css"]["c"]
    assert first == {"t": "MetaString", "c": "page.css"}
    assert second["c"].startswith(STYLESHEET_URI)


@pytest.mark.parametrize("failure", ["node fails", "no node"])
def test_a_failed_render_names_setup(
    convert: Convert,
    fake_tools: FakeTools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    tools = tmp_path / "empty"
    if failure == "node fails":
        monkeypatch.setenv("FAKE_TOOL_FAIL", "node")
        tools = fake_tools.write("node")

    result = convert("$x$\n", tools=tools)

    assert result.returncode != 0
    assert "katex.lua: cannot render math (" in result.stderr
    if failure == "node fails":
        assert "(node exited with status 3)" in result.stderr
    assert "run `md-doc-converter setup`" in result.stderr
