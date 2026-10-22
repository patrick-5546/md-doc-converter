"""Heading identifiers as Pandoc assigns them, and the anchors that preprocessing rewrites."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

import pytest

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.headings import Heading, HeadingError, headings

RENDERER_READER = "markdown-implicit_figures-yaml_metadata_block+gfm_auto_identifiers"


def test_headings_reads_document_once(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = (
        '{"blocks":['
        '{"t":"Header","c":[1,["first",[],[]],[{"t":"Str","c":"First"}]]},'
        '{"t":"Table","c":[{"t":"Header","c":[3,["cell",[],[]],[{"t":"Str","c":"Cell"}]]}]},'
        '{"t":"Header","c":[2,["second",[],[]],[{"t":"Str","c":"Second"}]]}'
        "]}"
    )
    completed = subprocess.CompletedProcess(
        ["pandoc", "--from=gfm+raw_html", "--to=json"],
        0,
        stdout=payload,
        stderr="",
    )
    run = mock.Mock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", run)

    actual = headings("# First\n\n## Second\n")

    assert actual == [Heading(1, "first", "First"), Heading(2, "second", "Second")]
    run.assert_called_once()


def test_missing_pandoc_reports_the_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = mock.Mock(side_effect=FileNotFoundError("missing executable"))
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(HeadingError, match=r"failed to run.*missing executable"):
        headings("# Heading\n", pandoc="missing-pandoc")


def test_failed_pandoc_reports_its_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(["pandoc"], 1, stdout="", stderr="bad reader")
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=completed))
    with pytest.raises(HeadingError, match=r"bad reader"):
        headings("# Heading\n")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("not JSON", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="no-blocks"),
        pytest.param('{"blocks":[{"t":"Header","c":[]}]}', id="header-without-parts"),
        pytest.param(
            '{"blocks":[{"t":"Header","c":["1",["id",[],[]],[]]}]}', id="level-not-an-integer"
        ),
        pytest.param(
            '{"blocks":[{"t":"Header","c":[1,[1,[],[]],[]]}]}', id="identifier-not-a-string"
        ),
    ],
)
def test_malformed_pandoc_output_is_rejected(payload: str, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(["pandoc"], 0, stdout=payload, stderr="")
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=completed))
    with pytest.raises(HeadingError):
        headings("# Heading\n")


def test_gfm_heading_ids_match_pandoc(pandoc: str) -> None:
    markdown = """# 1. Overview
# Foo.bar & Baz
# Résumé
# Same
# Same
# **Formatted** `Text` [Link](target.md)

```
# Fenced Example
```

| Col |
| --- |
| # Table Example |
"""

    actual = headings(markdown, pandoc=pandoc)

    assert [heading.identifier for heading in actual] == [
        "1-overview",
        "foobar--baz",
        "résumé",
        "same",
        "same-1",
        "formatted-text-link",
    ]
    assert actual[-1].text == "Formatted Text Link"


def test_renderer_reader_honors_explicit_heading_ids(pandoc: str) -> None:
    actual = headings(
        "# Explicit {#custom-id}\n# 1. Overview\n",
        pandoc=pandoc,
        reader=RENDERER_READER,
    )

    assert actual == [
        Heading(1, "custom-id", "Explicit"),
        Heading(1, "1-overview", "1. Overview"),
    ]


def test_same_file_anchor_warning_uses_renderer_ids(capsys: pytest.CaptureFixture[str]) -> None:
    markdown = (
        "[new](#1-overview)\n[old](#overview)\n"
        "[encoded](#r%C3%A9sum%C3%A9)\n[literal](#r\u00e9sum\u00e9)\n"
        "[custom](#custom-id)\n\n"
        '<table><tr><td><a href="#r%C3%A9sum%C3%A9">HTML link</a></td></tr></table>\n\n'
        '```\n<a href="#code-only">Literal HTML</a>\n```\n\n'
        "# 1. Overview\n# R\u00e9sum\u00e9\n# Explicit {#custom-id}\n"
    )

    preprocess.preprocess(markdown, source_path="input.md")

    warnings = capsys.readouterr().err
    assert "#1-overview" not in warnings
    assert "#r%C3%A9sum%C3%A9" not in warnings
    assert "#résumé" not in warnings
    assert "#custom-id" not in warnings
    assert "#code-only" not in warnings
    assert "broken anchor '#overview'" in warnings


def test_directory_links_remap_to_combined_renderer_ids(
    tmp_path: Path, pandoc: str, capsys: pytest.CaptureFixture[str]
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(
        """# Same
[bare](b%20page.md)
[to duplicate](b%20page.md#same)
[to unicode](b%20page.md#résumé)
[encoded unicode](b%20page.md#r%C3%A9sum%C3%A9)
[custom](b%20page.md#custom-id)
[old id](b%20page.md#overview)

<table><tr><td colspan="2"><a href="b%20page.md#r%C3%A9sum%C3%A9">HTML target</a></td></tr></table>

# Résumé
""",
        encoding="utf-8",
    )
    (docs / "b page.md").write_text(
        """# Same
[self](#same)

# 1. Overview
# Résumé
[encoded self](#r%C3%A9sum%C3%A9)

<table><tr><td colspan="2"><a href="#same">HTML self</a></td></tr></table>

`[Literal](#same)` and `<a href="#same">Literal HTML</a>`

# Explicit {#custom-id}
""",
        encoding="utf-8",
    )
    stats: dict[str, Counter[str]] = defaultdict(Counter)

    result = preprocess.collect_directory(docs, stats)

    stderr = capsys.readouterr().err
    assert "[bare](#same-1)" in result
    assert "[to duplicate](#same-1)" in result
    assert "[self](#same-1)" in result
    assert "[to unicode](#r%C3%A9sum%C3%A9-1)" in result
    assert "[encoded unicode](#r%C3%A9sum%C3%A9-1)" in result
    assert "[encoded self](#r%C3%A9sum%C3%A9-1)" in result
    assert "[custom](#custom-id)" in result
    assert '<a href="#r%C3%A9sum%C3%A9-1">HTML target</a>' in result
    assert '<a href="#same-1">HTML self</a>' in result
    assert '`[Literal](#same)` and `<a href="#same">Literal HTML</a>`' in result
    assert re.search(r"\[old id\]\([^)]*b%20page\.md#overview\)", result)
    assert "heading not found in target file" in stderr
    assert "broken anchor" not in stderr
    assert "target not found" not in stderr

    rendered = subprocess.run(
        [pandoc, f"--from={RENDERER_READER}", "--to=json"],
        input=result,
        capture_output=True,
        text=True,
        check=True,
    )
    blocks = json.loads(rendered.stdout)["blocks"]
    assert [block["c"][1][0] for block in blocks if block["t"] == "Header"] == [
        "same",
        "résumé",
        "same-1",
        "1-overview",
        "résumé-1",
        "custom-id",
    ]
