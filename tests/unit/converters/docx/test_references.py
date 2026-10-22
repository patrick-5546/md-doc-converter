"""Pandoc round trips: recovered link targets, paragraph splits, and code spans in tables."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.pandoc import JSONValue
from tests.support.docx import W

ValidateLink = Callable[[str, str], list[_validation.Issue]]


@pytest.fixture
def validate_external_link(tmp_path: Path, pandoc: str) -> ValidateLink:
    """Validate a page whose one link renders as `rendered`, for a Word hyperlink to `expected`."""
    source = tmp_path / "input.docx"
    source.write_bytes(b"source")
    paths = _publication.validate_paths(source, tmp_path / "guide.md")
    staged_images = tmp_path / "staged-images"
    staged_images.mkdir()

    def validate(expected: str, rendered: str) -> list[_validation.Issue]:
        document = _models.SourceDocument(
            ET.Element(W + "document"), "test", link_targets=[expected]
        )
        link = ET.Element("a", {"href": rendered})
        link.text = "Link"
        return _validation.validate_outputs(
            {Path("guide.md"): _reference.html_element(link)},
            paths,
            staged_images,
            pandoc,
            document,
        )

    return validate


def split(markdown: str, pandoc: str, filler: str | None = None) -> str:
    """Split paragraphs at line breaks, the word `filler` being filler; write Markdown back."""
    tree = _reference.markdown_ast(markdown, pandoc, "Paragraph split test").ast
    result = _reference.split_paragraphs_at_line_breaks(
        tree, lambda item: filler is not None and item == {"t": "Str", "c": filler}
    )
    return _reference.run_command(
        [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
        "Paragraph split test",
        input_text=json.dumps(result),
    )


@pytest.mark.parametrize(
    ("expected", "rendered"),
    [
        pytest.param(
            "https://example.test/topic_(detail)",
            "https://example.test/topic_%28detail%29",
            id="parentheses",
        ),
        pytest.param(
            "https://example.test/R\u00e9sum\u00e9",
            "https://example.test/R%C3%A9sum%C3%A9",
            id="unicode-path",
        ),
        pytest.param(
            "https://example.test/?q=caf\u00e9#r\u00e9sum\u00e9",
            "https://example.test/?q=caf%C3%A9#r%C3%A9sum%C3%A9",
            id="unicode-query-and-fragment",
        ),
    ],
)
def test_rendered_parentheses_and_unicode_match_recovered_urls(
    expected: str, rendered: str, validate_external_link: ValidateLink
) -> None:
    assert validate_external_link(expected, rendered) == []


@pytest.mark.parametrize(
    ("expected", "rendered"),
    [
        pytest.param("https://example.test/a/b", "https://example.test/a%2Fb", id="slash"),
        pytest.param(
            "https://example.test/a?x=y", "https://example.test/a%3Fx=y", id="question-mark"
        ),
        pytest.param("https://example.test/a#b", "https://example.test/a%23b", id="hash"),
        pytest.param(
            "https://example.test/?a=b&c=d", "https://example.test/?a=b%26c=d", id="ampersand"
        ),
        pytest.param("https://example.test/?a=b=c", "https://example.test/?a=b%3Dc", id="equals"),
        pytest.param(
            "https://example.test/?q=a+b", "https://example.test/?q=a%20b", id="plus-and-space"
        ),
        pytest.param(
            "https://example.test/100%25",
            "https://example.test/100%2525",
            id="percent-encoded-twice",
        ),
        pytest.param(
            "https://example.test/%252F",
            "https://example.test/%2F",
            id="encoded-percent-and-encoded-slash",
        ),
        pytest.param(
            "https://example.test/?a=b&copy;",
            "https://example.test/?a=b%C2%A9",
            id="entity-text-and-its-character",
        ),
    ],
)
def test_distinct_reserved_delimiters_and_percent_sequences_are_not_equivalent(
    expected: str, rendered: str, validate_external_link: ValidateLink
) -> None:
    details = [detail for _reason, detail in validate_external_link(expected, rendered)]
    assert any("do not preserve every recovered Word hyperlink" in detail for detail in details), (
        details
    )


def test_markdown_emission_preserves_entity_shaped_query_text(pandoc: str) -> None:
    target = "https://example.test/?q=one&copy;=two&notit=three"
    link = ET.Element("a", {"href": target})
    link.text = "Query"

    rendered = _tables.table_inline(link)

    assert rendered is not None
    assert _validation.collect_references(_reference.pandoc_html(rendered, pandoc)).links == [
        target
    ]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        pytest.param("Line one\\\nLine two", "Line one\n\nLine two\n", id="plain-text"),
        pytest.param("**Bold\\\ntext** after", "**Bold**\n\n**text** after\n", id="bold"),
        pytest.param("**a \\\nb**", "**a**\n\n**b**\n", id="bold-with-a-space-before"),
        pytest.param(
            "[Link\\\ntext](https://example.test/)",
            "[Link](https://example.test/)\n\n[text](https://example.test/)\n",
            id="link",
        ),
        pytest.param(
            "- Item\\\n  continued\n- Next", "- Item\n\n  continued\n\n- Next\n", id="list-item"
        ),
        pytest.param("a\\\n\\\nb", "a\n\nb\n", id="two-breaks"),
    ],
)
def test_line_breaks_become_paragraph_breaks(markdown: str, expected: str, pandoc: str) -> None:
    assert split(markdown, pandoc) == expected


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        pytest.param("text\\\nMARK", "textMARK\n", id="filler-last"),
        pytest.param("MARK\\\ntext", "MARKtext\n", id="filler-first"),
        pytest.param("**x MARK\\\ny**", "**x** MARK\n\n**y**\n", id="filler-after-bold-text"),
        pytest.param("**MARK\\\ny**", "MARK**y**\n", id="filler-first-in-bold"),
    ],
)
def test_pieces_with_only_filler_stay_with_a_neighbour(
    markdown: str, expected: str, pandoc: str
) -> None:
    assert split(markdown, pandoc, "MARK") == expected


def test_headings_and_tables_keep_their_line_breaks() -> None:
    line: JSONValue = [{"t": "Str", "c": "a"}, {"t": "LineBreak"}, {"t": "Str", "c": "b"}]
    heading: JSONValue = {"t": "Header", "c": [1, ["", [], []], line]}
    table: JSONValue = {"t": "Table", "c": [["", [], []], [None, []], [], [], [], [line]]}
    blocks: JSONValue = [heading, table]

    assert _reference.split_paragraphs_at_line_breaks(blocks) == blocks


def test_pipes_in_table_code_spans_stay_escaped_through_the_gfm_writer(pandoc: str) -> None:
    markdown = "| A | B |\n| --- | --- |\n| `a\\|b` | ``x`\\|`y`` |\n\nOutside `c|d`.\n"
    tree = _reference.markdown_ast(markdown, pandoc, "Table code test").ast
    written = _reference.run_command(
        [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
        "Table code test",
        input_text=json.dumps(_reference.protect_backslashes(tree)),
    )
    assert "`a\\|b`" in written
    assert "``x`\\|`y``" in written
    assert "Outside `c|d`." in written
    assert _reference.markdown_ast(written, pandoc, "Table code test").ast == tree


def test_run_command_shows_warnings_after_success_unless_quiet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [sys.executable, "-c", "import sys; print('out'); print('warning', file=sys.stderr)"]
    assert _reference.run_command(command, "Probe") == "out\n"
    assert capsys.readouterr().err == "Probe: warning\n"
    assert _reference.run_command(command, "Probe", quiet=True) == "out\n"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ("import sys; print('ignored'); sys.exit('broken input')", "broken input"),
        ("import sys; print('stdout reason'); sys.exit(3)", "stdout reason"),
        ("import sys; sys.exit(3)", "returned non-zero exit status 3"),
    ],
    ids=["stderr", "stdout", "status"],
)
def test_run_command_failures_name_the_step_and_the_reason(script: str, reason: str) -> None:
    with pytest.raises(_models.ConversionError, match=rf"^Probe failed: .*{re.escape(reason)}"):
        _reference.run_command([sys.executable, "-c", script], "Probe", quiet=True)


def test_run_command_names_a_missing_tool(tmp_path: Path) -> None:
    missing = tmp_path / "missing-tool"
    with pytest.raises(
        _models.ConversionError,
        match=f"^Probe requires unavailable command {re.escape(repr(str(missing)))}$",
    ):
        _reference.run_command([str(missing)], "Probe")
