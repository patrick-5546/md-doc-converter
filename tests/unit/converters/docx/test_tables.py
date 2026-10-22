"""Word tables: repair against the reference HTML, pipe tables or HTML, and empty rows and items."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx import validation as _validation
from tests.support.docx import (
    W,
    bookmark,
    cell,
    numbered,
    paragraph,
    row,
    table,
)


def pipe_table(table: ET.Element | str) -> str | None:
    """The Markdown pipe table for a reference table, or None when it must stay HTML."""
    root = _reference.parse_html(table).find("table") if isinstance(table, str) else table
    assert root is not None
    rendered = _tables.render_source_table(root)
    return rendered.text if rendered.markdown else None


def test_compact_spacing_preserves_escaped_pipes_and_empty_cells() -> None:
    assert _tables.compact_table_row(r"|  A \| B  |    |") == r"| A \| B | |"


def test_blank_header_and_all_blank_rows_survive() -> None:
    source = table(
        row(cell(""), cell(""), header=True),
        row(cell("data"), cell("")),
        row(cell(""), cell("")),
    )
    reference = _reference.parse_html(
        "<table><thead><tr><th></th><th></th></tr></thead>"
        "<tbody><tr><td>data</td><td></td></tr><tr><td></td><td></td></tr></tbody></table>"
    ).find("table")
    result = _tables.repair_source_table(source, reference)
    assert pipe_table(result) is None
    assert len(_tables.html_rows(result)) == 3


def test_first_row_is_a_markdown_header_without_repeat_header_metadata() -> None:
    source = table(row(cell("data"), cell("value")), row(cell(""), cell("")))
    reference = _reference.parse_html(
        "<table><thead><tr><th>data</th><th>value</th></tr></thead>"
        "<tbody><tr><td></td><td></td></tr></tbody></table>"
    ).find("table")
    result = _tables.repair_source_table(source, reference)
    assert result.find("thead") is None
    assert pipe_table(result) == "| data | value |\n| --- | --- |\n| | |\n"
    assert len(result.findall("./tbody/tr")) == 2


def test_zero_row_table_is_recovered() -> None:
    result = _tables.repair_source_table(table(), None)
    assert _reference.html_element(result) == "<table></table>"
    assert pipe_table(result) is None


def test_zero_cell_word_rows_are_restored_without_guessing() -> None:
    source = table(row(), row(cell("A"), cell("B")), row())
    reference = _reference.parse_html(
        "<table><tbody><tr><td>A</td><td>B</td></tr></tbody></table>"
    ).find("table")
    result = _tables.repair_source_table(source, reference)
    assert [len(item) for item in result.findall("./tbody/tr")] == [0, 2, 0]


def test_missing_cell_table_fails_closed() -> None:
    with pytest.raises(_models.ConversionError, match=r"omitted"):
        _tables.repair_source_table(table(row(cell(""))), None)


def test_nonmatching_table_text_fails_even_with_shared_words() -> None:
    source = table(row(cell("same first"), cell("required content")))
    reference = _reference.parse_html(
        "<table><tbody><tr><td>same first</td><td>different content</td></tr></tbody></table>"
    ).find("table")
    with pytest.raises(_models.ConversionError, match=r"content does not align"):
        _tables.repair_source_table(source, reference)


def test_invented_reference_rows_fail_instead_of_being_deleted() -> None:
    source = table(row(cell("data")))
    reference = _reference.parse_html(
        "<table><tbody><tr><td>data</td></tr><tr><td></td></tr></tbody></table>"
    ).find("table")
    with pytest.raises(_models.ConversionError, match=r"introduced table rows"):
        _tables.repair_source_table(source, reference)


def test_multiline_html_to_pipe_and_escaping() -> None:
    source = (
        "<table><thead><tr><th>Name</th><th>Details</th></tr></thead><tbody>"
        "<tr><td>A | B</td><td><p>First line</p><p></p><p><strong>Second</strong>"
        ' and <a href="#_Ref">target</a></p></td></tr></tbody></table>'
    )
    assert (
        pipe_table(source) == "| Name | Details |\n| --- | --- |\n"
        r"| A \| B | First line<br><br>**Second** and [target](#_Ref) |" + "\n"
    )


def test_merged_html_stays_html() -> None:
    source = table(row(cell("A", colspan=2), header=True), row(cell("x"), cell("y")))
    reference = _reference.parse_html(
        '<table><thead><tr><th colspan="2">A</th></tr></thead>'
        "<tbody><tr><td>x</td><td>y</td></tr></tbody></table>"
    ).find("table")
    assert pipe_table(_tables.repair_source_table(source, reference)) is None


def test_vertical_merge_keeps_fully_covered_blank_row() -> None:
    source = table(row(cell("A", merge="restart")), row(cell("", merge="continue")), columns=1)
    reference = _reference.parse_html(
        '<table><tbody><tr><td rowspan="2">A</td></tr></tbody></table>'
    ).find("table")
    result = _tables.repair_source_table(source, reference)
    assert [len(item) for item in result.findall("./tbody/tr")] == [1, 0]


def test_nonempty_vertical_continuation_is_not_lost() -> None:
    source = table(row(cell("A", merge="restart")), row(cell("keep", merge="continue")))
    with pytest.raises(_models.ConversionError, match=r"continuation contains retained content"):
        _tables.word_table_rows(source)


def test_nested_tables_and_caption_are_not_flattened() -> None:
    source = (
        '<table id="table-anchor"><caption>Caption</caption>'
        "<thead><tr><th>A</th></tr></thead><tbody><tr><td>"
        "<table><tbody><tr><td>nested</td></tr></tbody></table>"
        "</td></tr></tbody></table>"
    )
    assert pipe_table(source) is None


def test_word_anchor_is_removed_without_losing_cell_text() -> None:
    source = (
        "<table><thead><tr><th>A</th></tr></thead><tbody><tr>"
        '<td>before <a id="_Cell"></a>body</td></tr></tbody></table>'
    )
    assert pipe_table(source) == "| A |\n| --- |\n| before body |\n"


def test_widths_fonts_and_mixed_cell_alignment_do_not_force_html() -> None:
    source = (
        '<table style="width: 95%" width="100" class="word">'
        '<colgroup><col style="width: 20%"><col style="width: 80%"></colgroup>'
        '<tbody><tr style="height: 20pt">'
        '<td style="font-weight: bold; width: 20%; text-align: right;">A</td>'
        '<td width="80%"><span style="font-size: 10pt">B</span></td></tr>'
        '<tr><td style="text-align: left; color: red;">1</td>'
        '<td style="background: gray">2</td></tr></tbody></table>'
    )
    assert pipe_table(source) == "| A | B |\n| --- | --- |\n| 1 | 2 |\n"


@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize(
    "template",
    [
        pytest.param('align="{alignment}"', id="align-attribute"),
        pytest.param('style="text-align: {alignment} !important;"', id="text-align-style"),
    ],
)
def test_all_word_column_alignments_use_plain_markdown_separators(
    template: str, alignment: str
) -> None:
    attribute = template.format(alignment=alignment)
    source = (
        f"<table><tr><th {attribute}>Header</th></tr><tr><td {attribute}>Value</td></tr></table>"
    )
    assert pipe_table(source) == "| Header |\n| --- |\n| Value |\n"


def test_caption_alone_does_not_force_html() -> None:
    source = (
        "<table><caption>Table <strong>caption</strong></caption>"
        "<tbody><tr><td>Name</td></tr><tr><td>Value</td></tr></tbody></table>"
    )
    assert pipe_table(source) == "Table **caption**\n\n| Name |\n| --- |\n| Value |\n"


@pytest.mark.parametrize(
    "caption",
    [
        pytest.param("# Heading", id="heading"),
        pytest.param("1. Steps", id="numbered-item"),
        pytest.param("2) Steps", id="numbered-item-with-a-parenthesis"),
        pytest.param("- Note", id="dash-bullet"),
        pytest.param("+ Note", id="plus-bullet"),
        pytest.param("---", id="rule"),
    ],
)
def test_caption_cannot_create_a_heading_list_or_rule(caption: str, pandoc: str) -> None:
    rendered = pipe_table(
        f"<table><caption>{caption}</caption><tbody><tr><td>Header</td></tr></tbody></table>"
    )
    assert rendered is not None
    parsed = _reference.pandoc_html(rendered, pandoc)
    assert [node.tag for node in parsed] == ["p", "table"]
    assert "".join(parsed[0].itertext()) == caption


def test_header_breaks_are_spaces_and_cells_lose_empty_edge_lines() -> None:
    source = (
        "<table><thead><tr><th>Addr<br />\n[31:24]</th><th><p>Two</p><p></p><p>paras</p></th>"
        "<th><code>a  b</code><br />\nc</th></tr></thead><tbody>"
        "<tr><td>line1<br />\nline2</td><td><p></p><p>leading</p></td><td>end<br />\n</td></tr>"
        "<tr><td><p>in</p><p></p><p>side</p></td><td><p>trailing</p><p></p></td><td></td></tr>"
        "</tbody></table>"
    )
    assert (
        pipe_table(source) == r"| Addr \[31:24\] | Two paras | `a  b` c |"
        "\n| --- | --- | --- |\n"
        "| line1<br>line2 | leading | end |\n| in<br><br>side | trailing | |\n"
    )


def test_inline_code_uses_markdown_and_preserves_pipes_and_backticks(pandoc: str) -> None:
    source = (
        "<table><tbody><tr><td>Code</td></tr>"
        "<tr><td><code>a|b</code> and <code>`literal`</code></td></tr></tbody></table>"
    )
    rendered = pipe_table(source)
    assert rendered is not None
    assert "<code>" not in rendered
    parsed = _reference.pandoc_html(rendered, pandoc)
    assert [node.text for node in parsed.iter("code")] == ["a|b", "`literal`"]


def test_structural_fallback_reports_reason_and_discards_widths() -> None:
    source = _reference.parse_html(
        '<table style="width: 80%"><colgroup><col style="width: 80%"></colgroup>'
        '<tbody><tr><td colspan="2" align="center" style="text-align: center;">'
        "Wide</td></tr></tbody></table>"
    ).find("table")
    assert source is not None
    rendered = _tables.render_source_table(source)
    assert not rendered.markdown
    assert rendered.reason == "merged cells"
    assert 'colspan="2"' in rendered.text
    assert "width" not in rendered.text
    assert "colgroup" not in rendered.text
    assert "align" not in rendered.text
    assert "style" not in rendered.text


def _omit(markup: str) -> tuple[str, int]:
    """The HTML *markup* without its empty list items, and how many went."""
    root = _reference.parse_html(f"<div>{markup}</div>").find("div")
    assert root is not None
    count = _reference.omit_empty_list_items(root)
    return _reference.inner_html(root), count


def test_empty_bullets_go_wherever_they_are() -> None:
    assert _omit("<ul><li></li><li><p>a</p></li><li><p></p></li><li>b</li><li><br></li></ul>") == (
        "<ul><li><p>a</p></li><li>b</li></ul>",
        3,
    )


def test_empty_numbers_go_only_at_the_ends_so_others_keep_word_numbers() -> None:
    assert _omit(
        '<ol start="3"><li></li><li><p></p></li><li>c</li><li></li><li>d</li><li></li></ol>'
    ) == ('<ol start="5"><li>c</li><li></li><li>d</li></ol>', 3)


def test_lists_left_empty_go_and_their_items_are_checked_again() -> None:
    assert _omit("<ul><li>a<ul><li></li></ul></li><li><ul><li><p></p></li></ul></li></ul>") == (
        "<ul><li>a</li></ul>",
        3,
    )
    assert _omit("<p>x</p><ol><li></li></ol>") == ("<p>x</p>", 1)


def test_items_with_anchors_pictures_or_tables_stay() -> None:
    markup = (
        '<ul><li><a id="target"></a></li><li><img src="a.png"></li><li><table></table></li></ul>'
    )
    assert _omit(markup) == (markup, 0)


def _render_cell(cell_markup: str) -> _models.TableRendering:
    """Render a table whose one body row is A and a Details cell of *cell_markup*."""
    source = _reference.parse_html(
        "<table><thead><tr><th>Name</th><th>Details</th></tr></thead>"
        f"<tbody><tr><td>A</td><td>{cell_markup}</td></tr></tbody></table>"
    ).find("table")
    assert source is not None
    return _tables.render_source_table(source)


def test_one_level_lists_become_lines_in_a_markdown_table() -> None:
    rendered = _render_cell(
        "<p>Update:</p><ul><li><p>Chapter 9</p></li><li>Chapter <strong>10</strong></li></ul>"
    )
    assert rendered.markdown
    assert "| A | Update:<br>• Chapter 9<br>• Chapter **10** |" in rendered.text


def test_numbered_items_keep_word_numbers_as_decimals() -> None:
    rendered = _render_cell('<ol start="3" type="a"><li>x</li><li></li><li>y</li></ol>')
    assert "| A | 3. x<br>4.<br>5. y |" in rendered.text


def test_nested_lists_keep_the_table_html() -> None:
    nested = _render_cell("<ul><li>a<ul><li>b</li></ul></li></ul>")
    assert not nested.markdown
    assert nested.reason == "nested list in a cell"


def test_further_item_paragraphs_and_indented_paragraphs_become_lines() -> None:
    rendered = _render_cell(
        "<blockquote><p>Indented</p></blockquote>"
        "<ol><li><p>a</p><p>more about a</p></li>"
        "<li><p>b</p><blockquote><blockquote><p>quoted</p></blockquote></blockquote></li></ol>"
    )
    assert rendered.markdown
    assert "| A | Indented<br>1. a<br>more about a<br>2. b<br>quoted |" in rendered.text


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<blockquote><ul><li>a</li></ul></blockquote>", id="list-in-a-quote"),
        pytest.param('<blockquote title="q"><p>a</p></blockquote>', id="quote-with-an-attribute"),
        pytest.param("<ul><li><p>a</p><pre>b</pre></li></ul>", id="code-block-in-an-item"),
        pytest.param("<ul><li>a<p>b</p></li></ul>", id="text-and-a-paragraph-in-an-item"),
    ],
)
def test_quotes_and_items_holding_other_blocks_keep_the_table_html(markup: str) -> None:
    assert not _render_cell(markup).markdown


def test_lines_read_back_as_text_not_lists(pandoc: str) -> None:
    rendered = _render_cell("<ol><li>x</li><li>y</li></ol><ul><li>z</li></ul>")
    tree = _reference.pandoc_html(rendered.text, pandoc)
    assert not [node for node in tree.iter() if node.tag in {"ol", "ul"}]
    assert "1. x" in "".join(tree.itertext())


def test_sub_sup_mark_and_u_stay_tags_in_a_markdown_table(pandoc: str) -> None:
    rendered = _render_cell("x<sup>2</sup>, H<sub>2</sub>O, <mark>new</mark>, <u>a|b *c*</u>")
    assert rendered.markdown
    assert (
        r"| A | x<sup>2</sup>, H<sub>2</sub>O, <mark>new</mark>, <u>a\|b \*c\*</u> |"
        in rendered.text
    )
    tree = _reference.pandoc_html(rendered.text, pandoc)
    assert "".join(next(tree.iter("u")).itertext()) == "a|b *c*"


def test_tags_with_attributes_keep_the_table_html() -> None:
    assert not _render_cell('x<sup title="note">1</sup>').markdown


def _problem(cell_markup: str) -> str | None:
    """What the final-table check finds in a Markdown table cell of *cell_markup* that renders
    a Word cell of a list item, "first", and the paragraphs "1. typed" and "plain".

    The check allows a list marker only before a Word list item.
    """
    word = table(
        row(cell("Steps"), header=True),
        row(cell(numbered("first"), paragraph("1. typed"), paragraph("plain"))),
        columns=1,
    )
    items = frozenset(
        node for node in word.iter(W + "p") if node.find(f"./{W}pPr/{W}numPr") is not None
    )
    source = _models.SourceDocument(word, "TEST")
    source.markdown_tables.add("T0")
    rendered = _reference.parse_html(
        "<table><thead><tr><th>Steps</th></tr></thead>"
        f"<tbody><tr><td>{cell_markup}</td></tr></tbody></table>"
    ).find("table")
    assert rendered is not None
    descriptor = _models.SourceTable("T0", word, nested=False, listed=items)
    return _validation._table_problem(descriptor, rendered, source)


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("1. first<br>1. typed<br>plain", id="number"),
        pytest.param("\u2022 first<br>1. typed<br>plain", id="bullet"),
        pytest.param("first<br>1. typed<br>plain", id="no-marker"),
    ],
)
def test_a_bullet_or_number_may_start_a_list_item(markup: str) -> None:
    assert _problem(markup) is None


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("1. first<br>typed<br>plain", id="typed-number-lost"),
        pytest.param("1. first<br>1. typed<br>2. plain", id="number-added-to-a-paragraph"),
        pytest.param("1. 1. first<br>1. typed<br>plain", id="list-item-numbered-twice"),
    ],
)
def test_other_changes_to_numbers_are_still_found(markup: str) -> None:
    assert _problem(markup) == "changed Word cell content"


def _texts(table_node: ET.Element) -> list[list[str]]:
    return [
        [
            "".join(node.text or "" for node in item.iter(W + "t"))
            for item in row_node.findall(W + "tc")
        ]
        for row_node in table_node.findall(W + "tr")
    ]


def _merge(row_node: ET.Element, value: str | None) -> ET.Element:
    cell_node = row_node.findall(W + "tc")[0]
    properties = cell_node.find(W + "tcPr")
    if properties is None:
        properties = ET.Element(W + "tcPr")
        cell_node.insert(0, properties)
    ET.SubElement(properties, W + "vMerge", {} if value is None else {W + "val": value})
    return row_node


def _dropped(*tables: ET.Element) -> tuple[int, list[list[list[str]]]]:
    """Drop the empty rows of *tables*; return how many went and the texts of the rest."""
    body = ET.Element(W + "body")
    body.extend(tables)
    return _tables.drop_empty_rows(body), [_texts(node) for node in body]


def test_empty_rows_go_wherever_they_are() -> None:
    dropped, tables = _dropped(
        table(
            row(cell(""), cell("\u00a0")),
            row(cell("A"), cell("B")),
            row(cell(""), cell("")),
            row(cell("a"), cell("b")),
            row(cell(" "), cell("")),
        )
    )
    assert dropped == 3
    assert tables == [[["A", "B"], ["a", "b"]]]


def test_the_only_row_and_a_template_fill_in_row_stay() -> None:
    dropped, tables = _dropped(
        table(row(cell(""), cell("")), row(cell(""), cell(""))),
        table(row(cell("Name"), cell("Value")), row(cell(""), cell("")), row(cell(""), cell(""))),
        table(row(cell(""), cell(""))),
    )
    assert dropped == 2
    assert tables == [[["", ""]], [["Name", "Value"], ["", ""]], [["", ""]]]


def test_rows_in_a_vertical_merge_stay() -> None:
    dropped, tables = _dropped(
        table(
            row(cell("Head"), cell("Er")),
            _merge(row(cell("span"), cell("")), "restart"),
            _merge(row(cell(""), cell("")), None),
            row(cell(""), cell("")),
        )
    )
    assert dropped == 1
    assert len(tables[0]) == 3


def test_a_removed_rows_bookmarks_move_to_the_nearest_kept_row() -> None:
    marked = row(cell(bookmark("_Gone", "")), cell(""))
    body = ET.Element(W + "body")
    body.append(table(marked, row(cell("first"), cell("x")), row(cell("last"), cell("y"))))
    assert _tables.drop_empty_rows(body) == 1
    rows = body.findall(f"./{W}tbl/{W}tr")
    names = [node.get(W + "name") for node in rows[0].iter(W + "bookmarkStart")]
    assert names == ["_Gone"]
