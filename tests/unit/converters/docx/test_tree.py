"""Pandoc's document tree: traversal without recursion, text, and writing GFM."""

from __future__ import annotations

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.pandoc import JSONValue, decode_document


def api(pandoc: str) -> JSONValue:
    output = _reference.run_command([pandoc, "--from=html", "--to=json"], "API", input_text="x")
    return decode_document(output, pandoc=pandoc)["pandoc-api-version"]


def test_deep_nesting_is_walked_and_rewritten_without_recursion() -> None:
    # Word tables can nest deeper than Python's recursion limit.
    inner: JSONValue = _tree.string("deep")
    for _level in range(5000):
        inner = _tree.element("Emph", [inner])
    blocks: list[JSONValue] = [_tree.element("Para", [inner])]
    assert sum(1 for found in _tree.walk(blocks) if found["t"] == "Emph") == 5000
    assert _tree.stringify(blocks) == "deep"

    def plain(element: _tree.Element) -> list[_tree.Element]:
        return (
            [_tree.node(child) for child in _tree.items(element["c"])]
            if element["t"] == "Emph"
            else [element]
        )

    _tree.rewrite(blocks, inline=plain)
    assert blocks == [_tree.element("Para", [_tree.string("deep")])]


def test_rewrite_sees_children_first_and_skips_what_it_is_told() -> None:
    seen: list[str] = []

    def record(element: _tree.Element) -> list[_tree.Element]:
        seen.append(_tree.stringify(element))
        return [element]

    blocks: list[JSONValue] = [
        _tree.element("Para", [_tree.element("Strong", [_tree.string("a")]), _tree.string("b")]),
        _tree.element("Table", [_tree.element("Plain", [_tree.string("cell")])]),
    ]
    _tree.rewrite(blocks, inline=record, skip=frozenset({"Table"}))
    assert seen == ["a", "a", "b"]


def test_stringify_shows_text_code_math_and_breaks_but_not_notes() -> None:
    note = _tree.element("Note", [_tree.element("Para", [_tree.string("hidden")])])
    inlines: list[JSONValue] = [
        _tree.string("a"),
        _tree.element("Space"),
        _tree.element("Code", [_tree.attributes(), "x()"]),
        _tree.element("LineBreak"),
        _tree.element("Math", [{"t": "InlineMath"}, "y^2"]),
        note,
        _tree.raw_inline("html", "<mark>"),
    ]
    assert _tree.stringify(inlines) == "a x() y^2"
    assert not _tree.shows_something([_tree.element("Space"), note])


def test_gfm_destinations_are_escaped_and_html_ones_left_to_the_writer(pandoc: str) -> None:
    def link() -> JSONValue:
        return _tree.element(
            "Link",
            [
                _tree.attributes(),
                [_tree.string("Q")],
                ["https://example.test/?a=1&copy;=2", ' Opens the "spec" '],
            ],
        )

    version = api(pandoc)
    markdown = _tree.write(
        _tree.document([_tree.element("Para", [link()])], version), pandoc, "gfm", "GFM"
    )
    assert markdown == '[Q](https://example.test/?a=1&amp;copy;=2 "Opens the \\"spec\\"")\n'
    html = _tree.write(
        _tree.document([_tree.element("Para", [link()])], version), pandoc, "html5", "HTML"
    )
    assert 'href="https://example.test/?a=1&amp;copy;=2"' in html
