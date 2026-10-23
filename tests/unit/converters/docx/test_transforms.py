"""The transforms of Pandoc's tree of a Word document, before Pandoc writes it as GFM."""

from __future__ import annotations

import random
import re

import pytest

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import transforms as _transforms
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.pandoc import JSONValue, decode_document


def read(html: str, pandoc: str, reader: str = "html") -> tuple[list[JSONValue], JSONValue]:
    """Pandoc's blocks of *html*, and its API version."""
    document = decode_document(
        _reference.run_command(
            [pandoc, f"--from={reader}", "--to=json"], "Reading test HTML", input_text=html
        ),
        pandoc=pandoc,
    )
    return _tree.items(document["blocks"]), document["pandoc-api-version"]


def gfm(blocks: list[JSONValue], api: JSONValue, pandoc: str) -> str:
    return _tree.write(_tree.document(blocks, api), pandoc, "gfm", "Writing test GFM")


def html(blocks: list[JSONValue], api: JSONValue, pandoc: str) -> str:
    return _tree.write(_tree.document(blocks, api), pandoc, "html5", "Writing test HTML")


def same_emphasis(html: str) -> str:
    """*html* with bold italic always written <strong><em>: ***x*** reads as either."""
    return re.sub(r"<em><strong>(.*?)</strong></em>", r"<strong><em>\1</em></strong>", html)


def gfm_as_html(markdown: str, pandoc: str) -> str:
    return _reference.run_command(
        # Without GitHub's emoji, which a text such as ":a:" would show.
        [pandoc, "--from=gfm-emoji", "--to=html5", "--wrap=none"],
        "Reading test GFM",
        input_text=markdown,
    )


@pytest.mark.parametrize(
    ("paragraph", "markdown"),
    [
        pytest.param(
            "<strong>Note:</strong>next", "<strong>Note:</strong>next", id="punctuation-then-letter"
        ),
        pytest.param("word<strong>(x)</strong>tail", "word<strong>(x)</strong>tail", id="parens"),
        pytest.param("a <strong>b</strong> c", "a **b** c", id="between-spaces"),
        pytest.param("in<em>tra</em>word", "in*tra*word", id="intraword"),
        pytest.param("<strong>Note:</strong> next", "**Note:** next", id="punctuation-then-space"),
        pytest.param("x<del>(y)</del>z", "x<del>(y)</del>z", id="strikethrough"),
        # CommonMark reads symbols as punctuation since 0.31, GitHub not yet; HTML suits both.
        pytest.param("a<strong>€5</strong>", "a<strong>€5</strong>", id="symbol"),
    ],
)
def test_emphasis_markdown_cannot_close_is_html(paragraph: str, markdown: str, pandoc: str) -> None:
    blocks, api = read(f"<p>{paragraph}</p>", pandoc)
    _transforms.html_emphasis(blocks)
    assert gfm(blocks, api, pandoc) == markdown + "\n"


def test_adjacent_emphasis_is_html(pandoc: str) -> None:
    # Pandoc reads <em>a</em><em>b</em> as one; Word runs can be two.
    _blocks, api = read("<p>x</p>", pandoc)
    italic = [_tree.element("Emph", [_tree.string(text)]) for text in ("a", "b")]
    blocks: list[JSONValue] = [_tree.element("Para", list[JSONValue](italic))]
    _transforms.html_emphasis(blocks)
    assert gfm(blocks, api, pandoc) == "<em>a</em><em>b</em>\n"


@pytest.mark.parametrize(
    "paragraph",
    [
        "_:_<strong><em>(+:Z9.-aZ9a)</em>:)<em>-</em></strong>",
        "\u2192)<strong><em>\u20ac*)</em>):(-<em>:-'\u2192.*</em></strong>",
    ],
)
def test_emphasis_where_delimiters_meet_is_html_unless_it_reads_back(
    paragraph: str, pandoc: str
) -> None:
    # The characters around each delimiter allow it, but delimiters that meet form one run,
    # which Markdown matches otherwise.
    blocks, api = read(f"<p>{paragraph}</p>", pandoc)
    expected = html(blocks, api, pandoc)
    _transforms.html_emphasis(blocks)
    assert same_emphasis(gfm_as_html(gfm(blocks, api, pandoc), pandoc)) != same_emphasis(expected)
    _transforms.verify_emphasis(blocks, pandoc, api)
    assert same_emphasis(gfm_as_html(gfm(blocks, api, pandoc), pandoc)) == same_emphasis(expected)


def test_bold_italic_that_reads_back_stays_markdown(pandoc: str) -> None:
    blocks, api = read(
        "<p>A <em><strong><u>b</u></strong></em>. <em><strong>(c)</strong></em></p>", pandoc
    )
    _transforms.html_emphasis(blocks)
    _transforms.verify_emphasis(blocks, pandoc, api)
    assert gfm(blocks, api, pandoc) == "A ***<u>b</u>***. ***(c)***\n"


@pytest.mark.parametrize("seed", [20261025, 1, 2, 20])
def test_emphasis_reads_back_as_written_wherever_it_is(seed: int, pandoc: str) -> None:
    # Emphasis of random text beside random text: letters, digits, punctuation, symbols,
    # and spaces, and emphasis inside emphasis and links.
    pieces = ["a", "Z9", ":", "(", ")", ".", "-", "'", "€", "+", "→", " ", "_", "*"]
    tags = ["strong", "em", "del"]
    generator = random.Random(seed)

    def inline(depth: int) -> str:
        parts = []
        for _index in range(generator.randint(1, 4)):
            # Emphasis or a link of only spaces shows nothing, and is not written.
            if depth < 2 and generator.random() < 0.4:
                tag = generator.choice(tags)
                parts.append(f"<{tag}>{inline(depth + 1).strip() or 'a'}</{tag}>")
            elif depth < 2 and generator.random() < 0.1:
                content = inline(depth + 1).strip() or "a"
                parts.append(f'<a href="https://example.test/">{content}</a>')
            else:
                parts.append("".join(generator.choices(pieces, k=generator.randint(1, 3))))
        return "".join(parts)

    source = "".join(f"<p>{inline(0)}</p>" for _paragraph in range(400))
    blocks, api = read(source, pandoc)
    _transforms.unnest_emphasis(blocks)
    expected = html(blocks, api, pandoc)
    _transforms.html_emphasis(blocks)
    _transforms.escape_rules_and_underlines(blocks)
    _transforms.verify_emphasis(blocks, pandoc, api)
    actual = gfm_as_html(gfm(blocks, api, pandoc), pandoc)
    for want, got in zip(expected.split("\n"), actual.split("\n"), strict=True):
        assert same_emphasis(got) == same_emphasis(want)


@pytest.mark.parametrize(
    "line",
    ["---", "-----", "-", "- - -", "===", "=", "- item", "1. step", "1) step", "+++", "# x"],
)
@pytest.mark.parametrize("position", ["alone", "last-after-a-break", "between-breaks"])
def test_each_line_stays_text(line: str, position: str, pandoc: str) -> None:
    _blocks, api = read("<p>x</p>", pandoc)
    words: list[JSONValue] = []
    for index, word in enumerate(line.split(" ")):
        words.extend([_tree.element("Space")] if index else [])
        words.append(_tree.string(word))
    lead: list[JSONValue] = [_tree.string("Lead"), _tree.element("LineBreak")]
    tail: list[JSONValue] = [_tree.element("LineBreak"), _tree.string("end")]
    inlines = {
        "alone": words,
        "last-after-a-break": [*lead, *words],
        "between-breaks": [*lead, *words, *tail],
    }[position]
    blocks: list[JSONValue] = [_tree.element("Para", inlines)]
    expected = html(blocks, api, pandoc)
    _transforms.escape_rules_and_underlines(blocks)
    assert gfm_as_html(gfm(blocks, api, pandoc), pandoc) == expected


def link(target: str, *content: JSONValue) -> JSONValue:
    return _tree.element("Link", [_tree.attributes(), list(content), [target, ""]])


def test_links_keep_what_markdown_can_hold(pandoc: str) -> None:
    # Pandoc's HTML reader cannot nest links, as Word can.
    _blocks, api = read("<p>x</p>", pandoc)
    inner = link("https://example.test/inner", _tree.string("Inner"))
    outer = link("https://example.test/outer", _tree.string("Outer"), _tree.element("Space"), inner)
    blank = link("https://example.test/blank", _tree.element("Space"))
    blocks: list[JSONValue] = [
        _tree.element("Para", [outer]),
        _tree.element("Para", [blank, _tree.string("after")]),
        _tree.element(
            "Para",
            [
                link("https://example.test/R\u00e9sum\u00e9_(1)", _tree.string("CV")),
                _tree.element("Space"),
                link("#_Ref1", _tree.string("Back")),
            ],
        ),
    ]
    _transforms.tidy_links(blocks)
    assert gfm(blocks, api, pandoc) == (
        "[Outer Inner](https://example.test/outer)\n\nafter\n\n"
        "[CV](https://example.test/R%C3%A9sum%C3%A9_%281%29) [Back](#_Ref1)\n"
    )


def test_a_note_reference_follows_the_link_that_held_it() -> None:
    note = _tree.element("Note", [_tree.element("Para", [_tree.string("Note")])])
    link = _tree.element(
        "Link",
        [_tree.attributes(), [_tree.string("Label"), note], ["https://example.test/", ""]],
    )
    blocks: list[JSONValue] = [_tree.element("Para", [link])]
    _transforms.tidy_links(blocks)
    assert [_tree.tag(item) for item in _tree.items(_tree.node(blocks[0])["c"])] == [
        "Link",
        "Note",
    ]


def test_empty_paragraphs_go_except_in_tables(pandoc: str) -> None:
    blocks, _api = read(
        "<p>a</p><p></p><p> </p><table><tr><td><p>x</p><p></p><p>y</p></td></tr></table>",
        pandoc,
        "html+empty_paragraphs",
    )
    blocks.insert(1, _tree.element("Para", []))
    _transforms.drop_empty_paragraphs(blocks)
    assert [_tree.tag(block) for block in blocks] == ["Para", "Table"]
    cell_blocks = [found for found in _tree.walk(blocks[1]) if found["t"] in {"Para", "Plain"}]
    assert len(cell_blocks) == 3


def test_figures_divs_and_spans_keep_their_content(pandoc: str) -> None:
    blocks, api = read(
        '<figure><img src="a.png" alt="A"><figcaption>Caption</figcaption></figure>'
        '<div class="x"><p>In a div <mark>new</mark> <span class="chart">[CHART]</span>'
        '<span id="b" class="anchor"></span> <span dir="rtl">\u05e9</span></p></div>',
        pandoc,
    )
    _transforms.unwrap_containers(blocks)
    assert [_tree.tag(block) for block in blocks] == ["Para", "Para", "Para"]
    assert gfm(blocks, api, pandoc) == (
        '![A](a.png)\n\nCaption\n\nIn a div <mark>new</mark> <span id="b" class="anchor">'
        "</span> \u05e9\n"
    )


def test_headings_and_link_text_are_one_line(pandoc: str) -> None:
    blocks, api = read(
        '<h1>Top<br>bottom</h1><p><a href="https://example.test/">one<br>two</a><br>next</p>',
        pandoc,
    )
    report = TransformReport()
    _transforms.single_lines(blocks, report)
    assert gfm(blocks, api, pandoc) == (
        "# Top bottom\n\n[one two](https://example.test/)\\\nnext\n"
    )
    assert dict(report.records["headings"].skipped) == {
        "Word line break in a heading; a Markdown heading is one line": 1
    }


def test_adjacent_code_spans_are_one(pandoc: str) -> None:
    blocks, api = read("<p><code>a(</code><code>b)</code> and <code>c</code></p>", pandoc)
    _transforms.merge_code(blocks)
    assert gfm(blocks, api, pandoc) == "`a(b)` and `c`\n"


def test_code_blocks_are_fenced_text_with_straight_quotes(pandoc: str) -> None:
    blocks, api = read(
        "<pre><code>if (\u201cx\u201d)\n\tgo\u2014now\n```\n</code></pre>"
        "<table><tr><td><pre><code>in a cell</code></pre></td></tr></table>",
        pandoc,
    )
    report = TransformReport()
    _transforms.fence_code_blocks(blocks, report)
    assert _tree.tag(blocks[0]) == "RawBlock"
    assert gfm(blocks[:1], api, pandoc) == '````text\nif ("x")\n    go--now\n```\n````\n'
    assert any(found["t"] == "CodeBlock" for found in _tree.walk(blocks[1]))
    assert report.summary()[
        report.summary().index(
            "  code: applied (1 code block(s), 3 curly quote(s) and dash(es) straightened)"
        )
    ]


@pytest.mark.parametrize(
    ("items", "markdown"),
    [
        pytest.param(
            "<ul><li></li><li>a</li><li><p></p></li><li>b</li></ul>", "- a\n- b\n", id="bullets"
        ),
        pytest.param(
            '<ol start="3"><li></li><li>c</li><li></li><li>d</li><li></li></ol>',
            "4.  c\n5.  \n6.  d\n",
            id="numbers-at-the-ends",
        ),
        pytest.param(
            "<ul><li><p>a</p><p>more</p></li><li>b</li></ul>", "- a\n\n  more\n\n- b\n", id="loose"
        ),
        pytest.param(
            '<ol><li>a<ol start="2"><li>b</li></ol></li></ol>',
            "1.  a\n\n    2.  b\n",
            id="nested-numbered-from-2",
        ),
        pytest.param("<ul><li></li></ul><p>after</p>", "after\n", id="all-empty"),
    ],
)
def test_lists_lose_empty_items_and_are_tight_when_they_can_be(
    items: str, markdown: str, pandoc: str
) -> None:
    blocks, api = read(items, pandoc)
    _transforms.drop_empty_paragraphs(blocks)
    _transforms.tidy_lists(blocks, TransformReport())
    assert gfm(blocks, api, pandoc) == markdown


def test_notes_are_numbered_in_order_and_defined_after_their_block(pandoc: str) -> None:
    def note(*paragraphs: str) -> JSONValue:
        return _tree.element(
            "Note", [_tree.element("Para", [_tree.string(text)]) for text in paragraphs]
        )

    _blocks, api = read("<p>x</p>", pandoc)
    blocks: list[JSONValue] = [
        _tree.element("Para", [_tree.string("First"), note("One")]),
        _tree.element("Para", [_tree.string("Plain")]),
        _tree.element(
            "BulletList",
            [[_tree.element("Plain", [_tree.string("Item"), note("Two", "More")])]],
        ),
    ]
    notes = _transforms.extract_notes(blocks, TransformReport())
    _transforms.define_notes(blocks, notes, pandoc, api, "github")
    markdown = notes.references(gfm(blocks, api, pandoc))
    assert markdown == ("First[^1]\n\n[^1]: One\n\nPlain\n\n- Item[^2]\n\n[^2]: Two\n\n    More\n")
    assert not re.search(notes.token, markdown)


def test_a_note_that_shows_nothing_is_omitted_with_a_warning(
    pandoc: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _blocks, api = read("<p>x</p>", pandoc)
    empty = _tree.element("Note", [_tree.element("Para", [_tree.element("Space")])])
    kept = _tree.element("Note", [_tree.element("Para", [_tree.string("Kept")])])
    blocks: list[JSONValue] = [
        _tree.element("Para", [_tree.string("A"), empty, _tree.string("B"), kept])
    ]
    notes = _transforms.extract_notes(blocks, TransformReport())
    _transforms.define_notes(blocks, notes, pandoc, api, "github")
    assert notes.references(gfm(blocks, api, pandoc)) == "AB[^1]\n\n[^1]: Kept\n"
    assert "1 Word footnote(s) or endnote(s) without text omitted" in capsys.readouterr().err
