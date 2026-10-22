"""Markdown clean-up: escapes, lists, tables, whitespace, and duplicate headings."""

from __future__ import annotations

from unittest import mock

import pytest

from md_doc_converter import pandoc as _pandoc
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import navigation as _navigation
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import recovery as _recovery
from md_doc_converter.converters.docx import reference as _reference


def test_signal_identifier_escapes_are_removed_only_from_prose(pandoc: str) -> None:
    text = (
        r"Signal \_ctl_s_ready and \_x." + "\n\n"
        r"Inline `\_ctl_s_ready` stays literal." + "\n\n"
        "```text\n" + r"\_ctl_s_ready" + "\n```\n\n"
        "<pre>" + r"\_ctl_s_ready" + "</pre>\n\n"
        "    " + r"\_ctl_s_ready" + "\n\n"
        r"\_literal\_ and **bold** stay unchanged." + "\n"
    )
    assert _normalization.simplify_identifier_escapes(text, pandoc) == text.replace(
        r"Signal \_ctl_s_ready and \_x.", "Signal _ctl_s_ready and _x."
    )


def test_identifier_escape_stays_when_needed_to_prevent_emphasis(pandoc: str) -> None:
    text = r"\_ctl_s_ready and a closing_" + "\n"
    assert _normalization.simplify_identifier_escapes(text, pandoc) == text


def test_literal_backslash_before_identifier_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    text = r"Literal \\\_ctl_s_ready" + "\n"
    command = mock.Mock()
    monkeypatch.setattr(_reference, "run_command", command)
    assert _normalization.simplify_identifier_escapes(text, "pandoc") == text
    command.assert_not_called()


def test_compact_table_formatting_preserves_literal_regions() -> None:
    table_text = (
        "| Name   | Details  |\n|:------:|---------:|\n"
        "| A \\| B | first    |\n| :---:  | ---:     |\n"
    )
    literal = (
        "```\n" + table_text + "```\n\n"
        "<table><tr><td>\n| keep   | spaces   |\n</td></tr></table>\n\n"
        "    | indented  | code   |\n"
    )
    assert (
        _normalization.compact_markdown_tables(table_text + "\n" + literal)
        == "| Name | Details |\n| --- | --- |\n| A \\| B | first |\n| :---: | ---: |\n\n" + literal
    )


def test_normalizes_nested_lists_and_preserves_fences() -> None:
    text = "* top\n  + nested\n    - child\n```\n* literal\n```\n"
    assert _normalization.normalize_unordered_lists(text) == (
        "- top\n  - nested\n    - child\n```\n* literal\n```\n",
        2,
    )


def test_preserves_task_list_content_and_ordered_numbers() -> None:
    text = "* [ ] pending\n+ [x] complete\n3. Third\n9) Ninth\n"
    assert (
        _normalization.normalize_unordered_lists(text)[0]
        == "- [ ] pending\n- [x] complete\n3. Third\n9) Ninth\n"
    )


def test_nested_marker_chains() -> None:
    assert (
        _normalization.normalize_unordered_lists("* + - nested\n    + * child\n")[0]
        == "- - - nested\n    - - child\n"
    )


@pytest.mark.parametrize("tag", ["pre", "script", "style", "div", "table", "details"])
def test_does_not_touch_raw_html(tag: str) -> None:
    text = f"<{tag}>\n\n* literal\n+ literal\n</{tag}>\n\n* item\n"
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("* item", "- item")


def test_fence_before_html_and_tilde_fences() -> None:
    text = "~~~html\n<pre>\n~~~\n\n+ item\n"
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("+ item", "- item")


def test_indented_code_and_nested_list_code() -> None:
    text = "    * literal\n    + literal\n\n* real\n\n      + nested code\n\n  + child\n"
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace(
        "* real", "- real"
    ).replace("  + child", "  - child")


def test_comment_and_quoted_code() -> None:
    text = "<!--\n* literal\n-->\n\n> ```\n> + literal\n> ```\n\n> + item\n"
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("> + item", "> - item")


def test_keeps_thematic_breaks_and_inline_code() -> None:
    text = "* * *\n\n* `+ literal`\n"
    assert _normalization.normalize_unordered_lists(text)[0] == "* * *\n\n- `+ literal`\n"


def test_empty_list_items_also_use_dashes() -> None:
    assert _normalization.normalize_unordered_lists("+\n*\n")[0] == "-\n-\n"


def test_empty_nested_lists_do_not_turn_into_thematic_breaks() -> None:
    assert _normalization.normalize_unordered_lists("+ + +\n")[0] == "-\n  -\n    -\n"


def test_generic_raw_html_and_multiline_attributes_are_literal() -> None:
    text = '<widget\n class="literal">\n\n* literal\n</widget>\n\n+ item\n'
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("+ item", "- item")


def test_raw_script_text_cannot_hide_subsequent_lists() -> None:
    text = '<script>\nconst example = "<script>";\n</script>\n\n+ item\n'
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("+ item", "- item")


def test_self_closing_raw_tags_do_not_hide_lists() -> None:
    text = '<widget\n value="x" />\n\n+ item\n'
    assert _normalization.normalize_unordered_lists(text)[0] == text.replace("+ item", "- item")


def test_unclosed_inline_backtick_does_not_hide_later_lists() -> None:
    assert (
        _normalization.normalize_unordered_lists("A literal `\n\n+ item\n")[0]
        == "A literal `\n\n- item\n"
    )


def test_narrow_tooltip_repair() -> None:
    text = '[ADO](https://example.test/item?id=1" \\o "Tooltip")\n'
    assert _normalization.repair_word_tooltip_links(text) == (
        "[ADO](https://example.test/item?id=1)\n",
        1,
    )


def test_normal_quoted_links_and_fenced_tooltips_are_unchanged() -> None:
    text = '[ADO](https://example.test/item "Tooltip")\n\n```\n[x](https://x" \\o "Tip")\n```\n'
    assert _normalization.repair_word_tooltip_links(text) == (text, 0)


def test_final_newline_only() -> None:
    assert _normalization.normalize_final_newline("content  \n\n") == "content  \n"


def test_ordered_list_markers_get_one_space() -> None:
    text = "1.  Alpha\n\n    continued\n\n2)   Beta\n\n10. Ten\n    1.  Nested\n> 3.  Quoted\n"
    assert _normalization.normalize_ordered_list_spacing(text) == (
        "1. Alpha\n\n    continued\n\n2) Beta\n\n10. Ten\n    1. Nested\n> 3. Quoted\n",
        4,
    )


def test_ordered_list_spacing_leaves_literal_regions_and_code_items() -> None:
    text = "```\n1.  literal\n```\n\n<div>\n\n2.  literal\n</div>\n\n    3.  code\n\n4.     code\n"
    assert _normalization.normalize_ordered_list_spacing(text) == (text, 0)


# An autolink is not an HTML tag, so it does not start a raw HTML block.
@pytest.mark.parametrize(
    "link",
    [
        pytest.param("<https://example.test/spec>", id="url-autolink"),
        pytest.param("<a@example.test>", id="email-autolink"),
        pytest.param("<b>Bold</b> text", id="inline-html"),
    ],
)
def test_lines_after_an_autolink_paragraph_are_still_normalized(link: str) -> None:
    text = f"{link}\n\n| A   | B |\n|-----|---|\n| one | 2 |\n\n1.  Step\n"
    assert (
        _normalization.compact_markdown_tables(text)
        == f"{link}\n\n| A | B |\n| --- | --- |\n| one | 2 |\n\n1.  Step\n"
    )
    assert _normalization.normalize_ordered_list_spacing(text) == (
        text.replace("1.  Step", "1. Step"),
        1,
    )


def test_trailing_whitespace_is_trimmed_outside_code_and_html() -> None:
    literal = "```\nliteral  \n```\n\n<div>\ncell \u00a0\n</div>\n\n    code  \n"
    assert _normalization.trim_trailing_whitespace(
        "Text \xa0\n\n- Item\xa0\n-  \n- Last\n\n### \n\n" + literal
    ) == ("Text\n\n- Item\n-\n- Last\n\n###\n\n" + literal, 4)


def test_trailing_whitespace_keeps_line_breaks_and_backslashes() -> None:
    text = "First line  \nsecond line\n\nEscaped \\ \n\n\u00a0\n\nLast  \n"
    assert _normalization.trim_trailing_whitespace(text) == (
        "First line  \nsecond line\n\nEscaped \\ \n\n\xa0\n\nLast\n",
        1,
    )


def test_source_heading_formatting_around_alignment_markers_is_preserved() -> None:
    source = "#### **MARKBEGINTitle.MARKEND**\n"
    result = _recovery._primary_heading(source, _models.SourceHeading("MARK", 4, False))
    assert result == "#### **Title.**\n"


def test_qualification_keeps_levels_periods_and_anchors() -> None:
    text = (
        '# Chapter.\n\n### Gap.\n\n## One.\n\n<a id="_A"></a>\n\n'
        '#### Flow.\n\n## Two.\n\n<a id="_B"></a>\n\n#### Flow.\n'
    )
    result, changes = _navigation.qualify_duplicate_headings(text)
    assert "### Gap." in result
    assert "#### One. - Flow." in result
    assert "#### Two. - Flow." in result
    assert '<a id="_A"></a>' in result
    assert '<a id="_B"></a>' in result
    assert len(changes) == 2


def test_identical_ancestry_uses_occurrences() -> None:
    result, changes = _navigation.qualify_duplicate_headings("# Same.\n\n# Same.\n")
    assert len(changes) == 2
    assert "# Same. - occurrence 1" in result
    assert "# Same. - occurrence 2" in result


def test_qualification_compares_visible_titles_and_keeps_inline_format() -> None:
    content = "# One\n\n## **Flow.**\n\n# Two\n\n## Flow.\n"
    result, changes = _navigation.qualify_duplicate_headings(content)
    assert len(changes) == 2
    assert "## One - **Flow.**" in result
    assert "## Two - Flow." in result


def test_qualification_cannot_create_title_collisions() -> None:
    text = (
        "# Parent\n\n## Flow\n\n# Other\n\n## Flow\n\n"
        "# Parent - Flow\n\n# Parent - Flow - occurrence 1\n"
    )
    result, _changes = _navigation.qualify_duplicate_headings(text)
    titles = [
        _normalization.heading_reference_key(title)
        for _level, title in _normalization.markdown_heading_lines(result).values()
    ]
    assert len(titles) == len(set(titles))


def test_qualification_does_not_change_other_editorial_content() -> None:
    text = (
        "# Title.\n\n### Gap.\n\n**1)**\n\nNext paragraph.\n\n"
        "**Closed.**\n\n# Contents\n\nOriginal index\n"
    )
    assert _navigation.qualify_duplicate_headings(text) == (text, [])


def test_heading_parser_ignores_fences_html_and_indented_examples() -> None:
    text = "    # Code\n\n# Real\n\n```\n# Code\n```\n<table>\n# Cell\n</table>\n"
    assert _normalization.markdown_heading_lines(text) == {2: (1, "Real")}


def test_heading_parser_reads_marker_only_headings() -> None:
    text = "# A\n\n##\n\n### \n\n#5 not a heading\n\n#\tTab\n"
    assert _normalization.markdown_heading_lines(text) == {
        0: (1, "A"),
        2: (2, ""),
        4: (3, ""),
        8: (1, "Tab"),
    }


def test_qualification_skips_empty_headings_and_their_titles() -> None:
    empty = "# One\n\n##\n\n# Two\n\n##\n\n"
    text = empty + "# A\n\n## X\n\n##\n\n### Flow\n\n# B\n\n## Flow\n"
    result, changes = _navigation.qualify_duplicate_headings(text)
    assert result == empty + "# A\n\n## X\n\n##\n\n### A - Flow\n\n# B\n\n## B - Flow\n"
    assert len(changes) == 2


def test_wiki_slug() -> None:
    assert _navigation.wiki_page_slug("Module (Data Flow) / RTL") == "module-data-flow-rtl"
    assert _navigation.wiki_page_slug("***") == "page"


def test_brackets_and_cell_numbers_lose_their_escapes(pandoc: str) -> None:
    text = (
        r"Bits \[3:0\] of reg\[7\], an \[ alone, and \[e\]: mid-line." + "\n\n"
        "| Field | Steps |\n| --- | --- |\n"
        r"| ctrl\[1:0\] | 1\. first<br>2. second |" + "\n"
    )
    expected = (
        "Bits [3:0] of reg[7], an [ alone, and [e]: mid-line.\n\n"
        "| Field | Steps |\n| --- | --- |\n"
        "| ctrl[1:0] | 1. first<br>2. second |\n"
    )
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (expected, 10)


def test_brackets_markdownlint_could_read_as_links_stay_escaped(pandoc: str) -> None:
    text = (
        r"See \[a\](b), (x)\[y\], !\[i\] and \[c\]\[d\]." + "\n\n"
        r"- \[1:0\]: low bits" + "\n\n"
        r"Field x\[1:0\]." + "\n"
    )
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        r"See \[a\](b), (x)\[y\], !\[i\] and [c]\[d\]." + "\n\n"
        r"- \[1:0\]: low bits" + "\n\n"
        "Field x[1:0].\n",
        4,
    )


def test_only_the_brackets_that_change_a_block_stay_escaped(pandoc: str) -> None:
    # md-to-html's reader links "[Overview]" to the heading.
    text = (
        "# Overview\n\n"
        r"See \[Overview\] for \[3:0\]." + "\n\n"
        "| Name | Bits |\n| --- | --- |\n"
        r"| \[Overview\] | \[3:0\] |" + "\n"
        r"| x\[1\] | 7\) |" + "\n"
    )
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        "# Overview\n\n"
        r"See \[Overview\] for [3:0]." + "\n\n"
        "| Name | Bits |\n| --- | --- |\n"
        r"| \[Overview\] | [3:0] |" + "\n"
        "| x[1] | 7) |\n",
        7,
    )


def test_long_table_lines_do_not_keep_escapes(pandoc: str) -> None:
    # Pandoc gives a pipe table column widths when one of its lines is longer than 72
    # characters; neither the Wiki nor md-to-html uses them.
    long_row = r"| trace_src_sel_in\[7:0\] | I | \[15:0\] | Selects the trace source bus |"
    assert len(long_row) == 73
    text = "| Signal | I/O | Bits | Description |\n| --- | --- | --- | --- |\n" + long_row + "\n"
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        text.replace("\\[", "[").replace("\\]", "]"),
        4,
    )


def test_brackets_that_only_pass_one_at_a_time_stay_escaped(pandoc: str) -> None:
    text = "a \\[b\nc\\](d)\n\n" + r"Bits \[3:0\]." + "\n"
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        text.replace(r"\[3:0\]", "[3:0]"),
        2,
    )


def test_code_and_raw_html_keep_their_escapes(pandoc: str) -> None:
    text = (
        r"Inline `a\[0\]` stays; b\[0\] does not." + "\n\n"
        "```text\n" + r"c\[0\]" + "\n```\n\n"
        "<p>" + r"d\[0\]" + "</p>\n"
    )
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        text.replace(r"b\[0\]", "b[0]"),
        2,
    )


def test_bit_notation_in_an_html_cell_does_not_define_a_link(pandoc: str) -> None:
    # md-to-html escapes "[1:0]:" at the start of an HTML cell before reading the page.
    text = (
        "<table>\n<tr>\n<td>[1:0]: mode</td>\n</tr>\n</table>\n\n"
        r"Field \[1:0\] selects the mode." + "\n"
    )
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (
        text.replace(r"\[1:0\]", "[1:0]"),
        2,
    )


def test_page_that_would_parse_differently_is_unchanged(pandoc: str) -> None:
    text = r"See \[x\]." + "\n\n[x]: https://example.com\n"
    assert _normalization.drop_unneeded_escapes(text, pandoc) == (text, 0)


def test_page_is_unchanged_when_pandoc_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    text = r"Bits \[3:0\]." + "\n"
    error = _pandoc.PandocError("pandoc failed")
    monkeypatch.setattr(_pandoc, "parse_markdown", mock.Mock(side_effect=error))
    assert _normalization.drop_unneeded_escapes(text, "pandoc") == (text, 0)


def test_text_without_escapes_is_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Plain [x] text.\n"
    parse = mock.Mock()
    monkeypatch.setattr(_pandoc, "parse_markdown", parse)
    assert _normalization.drop_unneeded_escapes(text, "pandoc") == (text, 0)
    parse.assert_not_called()
