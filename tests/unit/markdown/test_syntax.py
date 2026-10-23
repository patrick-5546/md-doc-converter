"""Which Markdown lines are prose, which the cleanup steps may edit: not code or raw HTML."""

from __future__ import annotations

import pytest

from md_doc_converter.markdown import syntax as _syntax


def prose(text: str) -> list[str]:
    lines = text.splitlines()
    return [lines[index] for index in sorted(_syntax.markdown_prose_lines(text))]


def test_fenced_code_is_not_prose() -> None:
    lines = prose("* top\n  + nested\n```\n* literal\n```\n")
    assert "* top" in lines
    assert "  + nested" in lines
    assert "* literal" not in lines


@pytest.mark.parametrize("tag", ["pre", "script", "style", "div", "table", "details"])
def test_raw_html_blocks_are_not_prose(tag: str) -> None:
    lines = prose(f"<{tag}>\n\n* literal\n+ literal\n</{tag}>\n\n* item\n")
    assert "* literal" not in lines
    assert "+ literal" not in lines
    assert "* item" in lines


def test_html_in_a_tilde_fence_opens_no_raw_html() -> None:
    assert "+ item" in prose("~~~html\n<pre>\n~~~\n\n+ item\n")


def test_indented_code_is_not_prose_but_a_nested_list_is() -> None:
    lines = prose("    * literal\n    + literal\n\n* real\n\n      + nested code\n\n  + child\n")
    assert lines == ["* real", "  + child"]


def test_comments_and_quoted_code_are_not_prose() -> None:
    lines = prose("<!--\n* literal\n-->\n\n> ```\n> + literal\n> ```\n\n> + item\n")
    assert "* literal" not in lines
    assert "> + literal" not in lines
    assert "> + item" in lines


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<widget\n class="literal">\n\n* literal\n</widget>', id="multiline-tag"),
        pytest.param('<script>\nconst example = "<script>";\n</script>', id="script-text"),
        pytest.param('<widget\n value="x" />', id="self-closing"),
        pytest.param("A literal `", id="unclosed-backtick"),
    ],
)
def test_prose_resumes_after_raw_html_and_unclosed_code(html: str) -> None:
    lines = prose(html + "\n\n+ item\n")
    assert "* literal" not in lines
    assert "+ item" in lines
