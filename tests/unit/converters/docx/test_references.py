"""Pandoc round trips and link targets: encoding, entities, titles, and table code spans."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from hypothesis import given
from hypothesis import strategies as st

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.pandoc import JSONValue


@pytest.mark.parametrize(
    ("target", "encoded"),
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
def test_parentheses_and_unicode_are_percent_encoded(target: str, encoded: str) -> None:
    assert _reference.encoded_uri(target) == encoded


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("https://example.test/a/b?x=y=z&c=d#e", id="delimiters"),
        pytest.param("https://example.test/?q=a+b", id="plus"),
        pytest.param("https://example.test/100%25/%2F", id="percent-escapes"),
        pytest.param("https://example.test/?a=b&copy;", id="entity-text"),
        pytest.param("mailto:someone@example.test", id="e-mail"),
    ],
)
def test_encoding_keeps_delimiters_and_escapes_so_the_target_means_the_same(target: str) -> None:
    assert _reference.encoded_uri(target) == target


def test_markdown_emission_preserves_entity_shaped_query_text(pandoc: str) -> None:
    target = "https://example.test/?q=one&copy;=two&notit=three"
    link = ET.Element("a", {"href": target})
    link.text = "Query"

    rendered = _tables.table_inline(link)

    assert rendered is not None
    assert _validation.collect_references(_reference.pandoc_html(rendered, pandoc)).links == [
        target
    ]


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


# Raw HTML that a Word document's Markdown keeps, with every kind of markup the parser meets.
RAW_HTML = (
    "<!DOCTYPE html><?xml-stylesheet href='style.css'?>"
    '<!-- <a href="commented.md"> -->'
    '<p>Caf&eacute; &#233;&#xE9; <a id="top" href="guide.md#intro">Go</a>'
    '<img src=pic.png alt="Figure" /><br/><span/>'
    '<code><a href="literal.md">kept</a></code>'
    '<video poster="poster.png" src="clip.mp4"></video><object data="drawing.svg"></object></p>'
)


def test_rewriting_raw_html_changes_only_its_url_attributes() -> None:
    def parent(target: str) -> str:
        return f"../{target}"

    assert _reference.rewrite_html_targets(RAW_HTML, parent) == (
        "<!DOCTYPE html><?xml-stylesheet href='style.css'?>"
        '<!-- <a href="commented.md"> -->'
        '<p>Caf&eacute; &#233;&#xE9; <a id="top" href="../guide.md#intro">Go</a>'
        '<img src="../pic.png" alt="Figure" /><br/><span/>'
        '<code><a href="literal.md">kept</a></code>'
        '<video poster="../poster.png" src="../clip.mp4"></video>'
        '<object data="../drawing.svg"></object></p>'
    )


def test_parsed_raw_html_keeps_comments_and_closes_empty_elements() -> None:
    root = _reference.parse_html('<p>A<!-- note --><span/><br/>B</p><img src="pic.png">')
    paragraph, image = root
    assert [child.tag for child in paragraph] == [ET.Comment, "span", "br"]
    assert paragraph[0].text == " note "
    assert (paragraph.text, paragraph[-1].tail) == ("A", "B")
    assert image.get("src") == "pic.png"


@pytest.mark.parametrize(
    ("markup", "message"),
    [
        pytest.param(
            "<p>text</b></p>", "Unbalanced reference HTML closing tag </b>", id="unbalanced"
        ),
        pytest.param("<p><b>text</b>", "Unclosed reference HTML element <p>", id="unclosed"),
    ],
)
def test_malformed_raw_html_is_an_error(markup: str, message: str) -> None:
    with pytest.raises(_models.ConversionError, match=re.escape(message)):
        _reference.parse_html(markup)


def words(text: str) -> str:
    """*text* in NFC, the form Pandoc's GFM reader gives text, with its spaces as single spaces."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def link_titles(value: JSONValue) -> list[str]:
    """The titles of the links in Pandoc JSON *value*."""
    if isinstance(value, dict):
        content = value.get("c")
        if value.get("t") == "Link" and isinstance(content, list):
            target = content[2]
            assert isinstance(target, list)
            title = target[1]
            assert isinstance(title, str)
            return [title, *link_titles(content[1])]
        return [title for item in value.values() for title in link_titles(item)]
    if isinstance(value, list):
        return [title for item in value for title in link_titles(item)]
    return []


@pytest.mark.parametrize(
    "reader",
    ["gfm", "markdown-implicit_figures-yaml_metadata_block+gfm_auto_identifiers"],
    ids=["gfm", "renderer"],
)
@given(title=st.text(st.characters(categories=("L", "M", "N", "P", "S", "Zs")), min_size=1))
def test_any_word_screen_tip_reads_back_as_the_link_title(
    reader: str, title: str, pandoc: str
) -> None:
    link: JSONValue = {
        "t": "Link",
        "c": [["", [], []], [{"t": "Str", "c": "Spec"}], ["https://e.test", title]],
    }
    document: JSONValue = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [{"t": "Para", "c": [link]}],
    }
    markdown = _reference.run_command(
        [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none"],
        "Link title test",
        input_text=_reference.gfm_input(document),
    )
    read = _reference.run_command(
        [pandoc, f"--from={reader}", "--to=json"], "Link title test", input_text=markdown
    )
    # The link survives with the title's words; Pandoc's Markdown reader writes each run of
    # spaces in a title as one plain space.
    assert [words(text) for text in link_titles(json.loads(read))] == [words(title)]
