"""Rewrite Pandoc's tree of a prepared Word document into the Markdown that docx-to-md writes."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import ConversionError, ConversionPaths
from md_doc_converter.converters.docx.prepare import PreparedDocument
from md_doc_converter.converters.docx.tree import Element, JSONValue, MathSyntax
from md_doc_converter.markdown import syntax as _syntax
from md_doc_converter.pandoc import decode_document

EMPTY_ITEMS = "empty Word list item(s) omitted"
_CAPTIONED = "image(s) described by their caption"
_LISTS = frozenset({"BulletList", "OrderedList"})


def _children(element: Element, position: int) -> list[Element]:
    return [_tree.node(child) for child in _tree.items(_tree.items(element["c"])[position])]


def _paragraph(block: Element) -> Element:
    """A plain block, which Markdown would run into the next one, as a paragraph."""
    return _tree.element("Para", block["c"]) if block["t"] == "Plain" else block


# Pandoc reads the arrows of symbol fonts, such as Wingdings, as characters of Unicode's
# Supplemental Arrows-C block, which few fonts have, so PDFs and browsers show boxes for them.
# Each that points one way becomes the arrow that every font has. Unicode never renames a
# character, so the block as Unicode 7.0 defined it maps the same with every Python.
_ARROW_DIRECTIONS = (
    ("NORTH WEST", "\u2196"),
    ("NORTH EAST", "\u2197"),
    ("SOUTH EAST", "\u2198"),
    ("SOUTH WEST", "\u2199"),
    ("LEFT RIGHT", "\u2194"),
    ("UP DOWN", "\u2195"),
    ("LEFTWARDS", "\u2190"),
    ("UPWARDS", "\u2191"),
    ("RIGHTWARDS", "\u2192"),
    ("DOWNWARDS", "\u2193"),
)


def _arrow_table() -> dict[int, str]:
    table: dict[int, str] = {}
    for code in range(0x1F800, 0x1F8AE):
        name = unicodedata.name(chr(code), "")
        arrows = {arrow for words, arrow in _ARROW_DIRECTIONS if words in name}
        if len(arrows) == 1:
            table[code] = arrows.pop()
    return table


PLAIN_ARROWS = _arrow_table()


def plain_arrows(blocks: list[JSONValue], report: TransformReport) -> None:
    """Write each symbol font's arrow, in text and image alt text, as the arrow every font has."""
    changed = 0
    for element in _tree.walk(blocks):
        if element["t"] == "Str" and isinstance(text := element["c"], str):
            plain = text.translate(PLAIN_ARROWS)
            if plain != text:
                changed += sum(ord(character) in PLAIN_ARROWS for character in text)
                element["c"] = plain
    report.applied("symbols", count=changed)


def unwrap_containers(blocks: list[JSONValue]) -> None:
    """Keep the content of the figures, divs and spans that Markdown cannot hold.

    A figure becomes its picture and then its caption, each a paragraph. A highlight is written
    <mark>, as in a table cell. A chart or diagram, which has no picture, goes; the
    preparation reported it. The spans of Word bookmarks stay, for the bookmarks module.
    """

    def block(element: Element) -> list[Element]:
        if element["t"] == "Figure":
            _attributes, caption, content = _tree.items(element["c"])
            figure = [*_tree.items(content), *_tree.items(_tree.items(caption)[1])]
            return [_paragraph(_tree.node(child)) for child in figure]
        if element["t"] == "Div":
            return _children(element, 1)
        return [element]

    def inline(element: Element) -> list[Element]:
        if element["t"] == "SmallCaps":
            return [_tree.node(child) for child in _tree.items(element["c"])]
        if element["t"] != "Span":
            return [element]
        identity, classes, _attributes = _tree.items(_tree.items(element["c"])[0])
        names = {_tree.text(name) for name in _tree.items(classes)}
        if "anchor" in names and identity:
            return [element]
        content = _children(element, 1)
        if names & {"chart", "diagram"}:
            return []
        if "mark" in names:
            return [
                _tree.raw_inline("html", "<mark>"),
                *content,
                _tree.raw_inline("html", "</mark>"),
            ]
        return content

    _tree.rewrite(blocks, block=block, inline=inline)


def _one_line(inlines: list[JSONValue]) -> None:
    """Make *inlines*, and the inlines that they hold, one line: a break becomes a space."""
    for value in _tree.lists(inlines):
        if _tree.kind(value) != "inline":
            continue
        result: list[JSONValue] = []
        for child in value:
            item = _tree.node(child)
            if item["t"] in _tree.BREAKS:
                if result and _tree.tag(result[-1]) == "Space":
                    continue
                item = _tree.element("Space")
            result.append(item)
        value[:] = result
    while inlines and _tree.tag(inlines[0]) == "Space":
        del inlines[0]
    while inlines and _tree.tag(inlines[-1]) == "Space":
        del inlines[-1]


def single_lines(blocks: list[JSONValue], report: TransformReport) -> None:
    """Write each heading and link text on one line, which Markdown needs: a Word line break
    in one becomes a space."""
    for element in _tree.walk(blocks):
        if element["t"] not in {"Header", "Link"}:
            continue
        inlines = _tree.items(_tree.items(element["c"])[2 if element["t"] == "Header" else 1])
        if element["t"] == "Header" and any(
            found["t"] == "LineBreak" for found in _tree.walk(inlines)
        ):
            report.skipped(
                "headings",
                "Word line break in a heading; a Markdown heading is one line",
                section="markdown",
            )
        _one_line(inlines)


def _trim_breaks(inlines: list[JSONValue], *, end: bool) -> None:
    """Remove the breaks and spaces at one end of *inlines*, inside formatting there too, and
    the formatting that they leave empty."""
    edge = -1 if end else 0
    path = [inlines]
    while path:
        current = path[-1]
        while current and _tree.tag(current[edge]) in _tree.BREAKS:
            del current[edge]
        if not current:
            path.pop()
            if path:
                del path[-1][edge]
            continue
        last = _tree.node(current[edge])
        if last["t"] not in _tree.WRAPPERS:
            return
        path.append(_tree.items(last["c"]))


def _split_at_blank_lines(inlines: list[JSONValue]) -> list[list[JSONValue]]:
    """*inlines* split where two or more line breaks in a row leave a blank line."""
    parts: list[list[JSONValue]] = [[]]
    index = 0
    while index < len(inlines):
        after = index
        breaks = 0
        while after < len(inlines) and _tree.tag(inlines[after]) in _tree.BREAKS:
            breaks += _tree.tag(inlines[after]) == "LineBreak"
            after += 1
        if breaks >= 2 and parts[-1] and after < len(inlines):
            parts.append([])
            index = after
            continue
        parts[-1].extend(inlines[index : max(after, index + 1)])
        index = max(after, index + 1)
    return parts


def tidy_line_breaks(blocks: list[JSONValue]) -> None:
    """Keep Word line breaks as Markdown can show them.

    A break that starts or ends a paragraph shows nothing in Word, and Markdown would show a
    backslash for one at the end. Two or more in a row leave a blank line, which Markdown
    writes as a new paragraph; in a table cell they stay line breaks.
    """

    def block(element: Element) -> list[Element]:
        if element["t"] not in {"Para", "Plain"}:
            return [element]
        inlines = _tree.items(element["c"])
        _trim_breaks(inlines, end=False)
        _trim_breaks(inlines, end=True)
        parts = _split_at_blank_lines(inlines)
        if len(parts) == 1:
            return [element]
        for part in parts:
            _trim_breaks(part, end=True)
        return [_tree.element("Para", part) for part in parts]

    _tree.rewrite(blocks, block=block, skip=frozenset({"Table"}))

    def cell(element: Element) -> list[Element]:
        if element["t"] in {"Para", "Plain"}:
            inlines = _tree.items(element["c"])
            _trim_breaks(inlines, end=False)
            _trim_breaks(inlines, end=True)
        return [element]

    for table in [found for found in _tree.walk(blocks) if found["t"] == "Table"]:
        _tree.rewrite(table, block=cell)


def short_rules(blocks: list[JSONValue]) -> None:
    """Write horizontal lines as ---, rather than Pandoc's line of 72 dashes; a table writes
    its own."""

    def block(element: Element) -> list[Element]:
        if element["t"] == "HorizontalRule":
            return [_tree.raw_block("gfm", "---")]
        return [element]

    _tree.rewrite(blocks, block=block, skip=frozenset({"Table"}))


def merge_code(blocks: list[JSONValue]) -> None:
    """Join adjacent code spans, which Markdown would read as one with backticks inside.

    Pandoc's Markdown writers write them back to back
    (https://github.com/jgm/pandoc/issues/11993).
    """
    for value in _tree.lists(blocks):
        if _tree.kind(value) != "inline":
            continue
        merged: list[JSONValue] = []
        for child in value:
            item = _tree.node(child)
            previous = _tree.node(merged[-1]) if merged else None
            if item["t"] == "Code" and previous is not None and previous["t"] == "Code":
                content = _tree.items(previous["c"])
                content[1] = _tree.text(content[1]) + _tree.text(_tree.items(item["c"])[1])
                continue
            merged.append(item)
        value[:] = merged


def _shows(value: JSONValue) -> bool:
    """Whether *value* shows text, an image, or a note's number."""
    return _tree.shows_something(value) or any(found["t"] == "Note" for found in _tree.walk(value))


def tidy_links(blocks: list[JSONValue]) -> None:
    """Keep as links the Word hyperlinks that Markdown can hold, with their targets encoded.

    Markdown cannot nest links, so a link inside another is its text, which the preparation
    reported; a note's reference in a link follows it; and a link that shows nothing is its
    content. A target outside the document is percent-encoded, so that every renderer reads it
    whole.
    """
    within = frozenset({"Note"})

    def unlink(element: Element) -> list[Element]:
        return _children(element, 1) if element["t"] == "Link" else [element]

    def inline(element: Element) -> list[Element]:
        if element["t"] != "Link":
            return [element]
        content = _tree.items(element["c"])
        notes: list[Element] = []

        def lift(found: Element) -> list[Element]:
            if found["t"] != "Note":
                return [found]
            notes.append(found)
            return []

        _tree.rewrite(_tree.items(content[1]), inline=unlink, skip=within)
        _tree.rewrite(_tree.items(content[1]), inline=lift, skip=within)
        if not _shows(content[1]):
            return [*_children(element, 1), *notes]
        target = _tree.items(content[2])
        address = _tree.text(target[0])
        if not address.startswith("#"):
            target[0] = _reference.encoded_uri(address)
        return [element, *notes]

    _tree.rewrite(blocks, inline=inline)


def drop_empty_paragraphs(blocks: list[JSONValue]) -> None:
    """Omit the paragraphs that show nothing, except in tables, where they are blank lines."""

    def block(element: Element) -> list[Element]:
        if element["t"] in {"Para", "Plain"} and not _shows(element):
            return []
        return [element]

    _tree.rewrite(blocks, block=block, skip=frozenset({"Table"}))


# Inlines that Markdown writes between delimiters that must open and close as such.
_DELIMITED = {"Strong": "strong", "Emph": "em", "Strikeout": "del"}
# A character that stands for how other inlines are written at their start and end.
_WRITTEN = {
    "Strong": ("*", "*"),
    "Emph": ("*", "*"),
    "Strikeout": ("~", "~"),
    "Underline": ("<", ">"),
    "Superscript": ("<", ">"),
    "Subscript": ("<", ">"),
    "Code": ("`", "`"),
    "Math": ("$", "$"),
    "Link": ("[", ")"),
    "Image": ("!", ")"),
    "Note": ("[", "]"),
}


def _edge(inlines: list[JSONValue], *, start: bool) -> str | None:
    """The character that *inlines* are written to start, or end, with; None for none."""
    stack = [_tree.node(value) for value in (reversed(inlines) if start else inlines)]
    while stack:
        element = stack.pop()
        name = _tree.text(element["t"])
        if name in {"Str", "RawInline"}:
            text = _tree.text(element["c"] if name == "Str" else _tree.items(element["c"])[1])
            if text:
                return text[0] if start else text[-1]
        elif name in _tree.BREAKS:
            return " "
        elif name in _WRITTEN:
            return _WRITTEN[name][0 if start else 1]
        else:
            children = _tree.inline_children(element) or []
            stack.extend(_tree.node(child) for child in (reversed(children) if start else children))
    return None


# A line of only dashes or equals signs, which Markdown can read as a horizontal rule or as
# the underline of a heading.
_RULE_OR_UNDERLINE = re.compile(r"(?:-+|=+)[ \t]*$")


def escape_rules_and_underlines(blocks: list[JSONValue]) -> None:
    """Keep as text a paragraph's last line of dashes, or of equals signs after a line break.

    Pandoc's GFM writer (3.12.1) writes them as they are
    (https://github.com/jgm/pandoc/issues/11992), so "---" alone would be a horizontal rule,
    and after a Word line break it, or "===", would make the paragraph a heading. Tables
    write their own lines.
    """
    for element in _tree.walk(blocks, skip=frozenset({"Table"})):
        if element["t"] not in {"Para", "Plain"}:
            continue
        inlines = _tree.items(element["c"])
        breaks = [index for index, value in enumerate(inlines) if _tree.tag(value) == "LineBreak"]
        start = breaks[-1] + 1 if breaks else 0
        while start < len(inlines) and _tree.tag(inlines[start]) == "Space":
            start += 1
        if start == len(inlines) or _tree.tag(inlines[start]) != "Str":
            continue
        line = _tree.stringify(inlines[start:])
        if not _RULE_OR_UNDERLINE.match(line) or (line.startswith("=") and not breaks):
            continue
        first = _tree.text(_tree.node(inlines[start])["c"])
        rest: list[JSONValue] = [_tree.string(first[1:])] if first[1:] else []
        inlines[start : start + 1] = [_tree.raw_inline("gfm", "\\" + first[0]), *rest]


def unnest_emphasis(blocks: list[JSONValue]) -> None:
    """Keep only the outermost of emphasis inside emphasis of the same kind, as bold in bold.

    It shows the same, and Markdown cannot write it: Pandoc writes italic in italic as plain
    text and strikethrough in strikethrough as a run of tildes, which reads as text.
    """
    unwrap: set[int] = set()
    stack: list[tuple[list[JSONValue], frozenset[str]]] = [
        (inlines, frozenset())
        for element in _tree.walk(blocks)
        if (inlines := _tree.block_inlines(element)) is not None
    ]
    while stack:
        inlines, kinds = stack.pop()
        for value in inlines:
            element = _tree.node(value)
            name = _tree.text(element["t"])
            if name in _DELIMITED and name in kinds:
                unwrap.add(id(element))
            children = _tree.inline_children(element)
            if children is not None:
                stack.append((children, kinds | ({name} & _DELIMITED.keys())))

    def inline(element: Element) -> list[Element]:
        if id(element) not in unwrap:
            return [element]
        return [_tree.node(child) for child in _tree.items(element["c"])]

    if unwrap:
        _tree.rewrite(blocks, inline=inline)


def _write_as_html(blocks: list[JSONValue], convert: set[int]) -> None:
    """Write the emphasis elements in *convert*, by identity, as HTML tags around their content."""

    def inline(element: Element) -> list[Element]:
        if id(element) not in convert:
            return [element]
        tag = _DELIMITED[_tree.text(element["t"])]
        return [
            _tree.raw_inline("html", f"<{tag}>"),
            *(_tree.node(child) for child in _tree.items(element["c"])),
            _tree.raw_inline("html", f"</{tag}>"),
        ]

    if convert:
        _tree.rewrite(blocks, inline=inline, skip=frozenset({"Table"}))


def html_emphasis(blocks: list[JSONValue]) -> None:
    """Write as HTML the emphasis whose Markdown delimiters would not read as such.

    Markdown reads ** as bold only beside certain characters: "**Note:**next" shows its
    asterisks, because a closing ** after punctuation must be followed by a space or more
    punctuation. Pandoc writes the delimiters regardless; such bold is written <strong>, as
    italic is <em> and strikethrough <del>. GitHub and newer CommonMark differ on which
    symbols count as punctuation, so both must read the delimiters; how delimiters that meet
    are matched, verify_emphasis checks. Tables write their own.
    """
    convert: set[int] = set()
    # Each list of inlines, the characters around it, and the delimiters open around it.
    stack: list[tuple[list[JSONValue], str | None, str | None, frozenset[str]]] = [
        (inlines, None, None, frozenset())
        for element in _tree.walk(blocks, skip=frozenset({"Table"}))
        if (inlines := _tree.block_inlines(element)) is not None
    ]
    while stack:
        inlines, before, after, open_delimiters = stack.pop()
        for index, value in enumerate(inlines):
            element = _tree.node(value)
            name = _tree.text(element["t"])
            if name in _DELIMITED:
                content = _tree.items(element["c"])
                first, last = _edge(content, start=True), _edge(content, start=False)
                previous = _edge(inlines[:index], start=False)
                following = _edge(inlines[index + 1 :], start=True)
                left = before if previous is None else previous
                right = after if following is None else following
                delimiter = _WRITTEN[name][0]
                # Delimiters beside the same delimiter join it in one run. Tildes are stricter
                # than asterisks: a run of them is not split again, and at a line's start opens
                # a code fence; renderers match strikethrough within strikethrough differently.
                tilde = delimiter == "~"
                beside = {previous, following} | ({left, right} if tilde else set())
                nested = delimiter in open_delimiters
                if (
                    first is not None
                    and last is not None
                    and (
                        delimiter in beside
                        or (tilde and nested)
                        or not _syntax.emphasis_delimits(left, first, last, right)
                    )
                ):
                    convert.add(id(element))
                    stack.append((content, ">", "<", open_delimiters))
                else:
                    stack.append((content, delimiter, delimiter, open_delimiters | {delimiter}))
            elif name in {"Link", "Image"}:
                # Markdown reads the emphasis in link text on its own.
                stack.append((_tree.items(_tree.items(element["c"])[1]), "[", "]", frozenset()))
            elif (children := _tree.inline_children(element)) is not None:
                stack.append((children, "<", ">", open_delimiters))

    _write_as_html(blocks, convert)


def _emphasis(value: JSONValue) -> tuple[Counter[tuple[str, str]], str]:
    """The emphasis in *value*, as kinds and texts, and its text; notes are left out."""
    found = Counter(
        (_tree.text(element["t"]), _tree.stringify(element))
        for element in _tree.walk(value, skip=frozenset({"Note"}))
        if element["t"] in _DELIMITED
    )
    return found, _tree.stringify(value)


def verify_emphasis(blocks: list[JSONValue], pandoc: str, api: JSONValue) -> None:
    """Write as HTML the emphasis of each paragraph that Pandoc does not read back as written.

    Delimiters that meet, as in ***x*** or **a*b***, form one run, which Markdown matches by
    rules beyond the characters around it; Pandoc reads it as GitHub does. Tables write their
    own.
    """
    lists = [
        inlines
        for element in _tree.walk(blocks, skip=frozenset({"Table"}))
        if (inlines := _tree.block_inlines(element)) is not None
        and any(found["t"] in _DELIMITED for found in _tree.walk(inlines))
    ]
    if not lists:
        return
    marker = "DOCXTOMDEMPHASIS" + uuid.uuid4().hex.upper()
    batch: list[JSONValue] = []
    for inlines in lists:
        batch += [_tree.element("Para", [_tree.string(marker)]), _tree.element("Para", inlines)]
    try:
        # Writing escapes link destinations in place; the paragraphs must stay as they are.
        written = _tree.write(
            json.loads(json.dumps(_tree.document(batch, api))), pandoc, "gfm", "Checking emphasis"
        )
    except RecursionError:
        return
    read = decode_document(
        _reference.run_command(
            [pandoc, "--from=gfm-emoji", "--to=json"], "Checking emphasis", input_text=written
        ),
        pandoc=pandoc,
    )
    groups: list[list[JSONValue]] = []
    for block in _tree.items(read["blocks"]):
        if _tree.block_inlines(_tree.node(block)) == [_tree.string(marker)]:
            groups.append([])
        elif groups:
            groups[-1].append(block)
    if len(groups) != len(lists):
        return
    convert = {
        id(element)
        for inlines, group in zip(lists, groups, strict=True)
        if _emphasis(inlines) != _emphasis(group)
        for element in _tree.walk(inlines, skip=frozenset({"Note"}))
        if element["t"] in _DELIMITED
    }
    _write_as_html(blocks, convert)


def fence_code_blocks(blocks: list[JSONValue], report: TransformReport) -> None:
    """Write each code block outside tables as a fenced text block, with tabs as four spaces
    and Word's typographic quotes, dashes and ellipses undone; a table writes its own."""

    def block(element: Element) -> list[Element]:
        if element["t"] != "CodeBlock":
            return [element]
        code = _tree.text(_tree.items(element["c"])[1]).replace("\t", "    ")
        code, straightened = _ooxml.straighten_code(code)
        report.applied("code", "curly quote(s) and dash(es) straightened", straightened)
        report.applied("code", "code block(s)")
        return [_tree.raw_block("gfm", _reference.code_fence(code, "text"))]

    _tree.rewrite(blocks, block=block, skip=frozenset({"Table"}))


@dataclass
class ImageCounts:
    occurrences: int = 0
    unresolved_alt_text: int = 0


def publish_images(
    blocks: list[JSONValue],
    prepared: PreparedDocument,
    stager: _media.ImageStager,
    paths: ConversionPaths,
    media_folder: str,
    report: TransformReport,
) -> ImageCounts:
    """Publish each picture once, under its Word name, and show it by its published path,
    with its alt text and without Word's display size."""
    counts = ImageCounts()
    for element in _tree.walk(blocks):
        if element["t"] != "Image":
            continue
        content = _tree.items(element["c"])
        source = Path(_tree.text(_tree.items(content[2])[0]))
        try:
            data = source.read_bytes()
        except OSError as error:
            raise ConversionError(
                f"Cannot read the image that Pandoc extracted: {error}"
            ) from error
        image = prepared.images.get(source.name)
        if image is None:
            published = stager.publish_data("", data, report, source.suffix)
            alt_text: str | None = _tree.stringify(content[1]).strip()
        else:
            published = stager.publish(
                "part:" + image.part, data, _media.media_name(image.part, media_folder), report
            )
            alt_text = image.alt_text
            if image.from_caption:
                report.applied("images", _CAPTIONED)
            if alt_text is None:
                counts.unresolved_alt_text += 1
                report.warn(
                    f"Unresolved alt text: {image.part} at source paragraph "
                    f"{image.paragraph_index + 1}; leaving alt text empty",
                    kind="image without alt text; alt text left empty",
                )
        report.applied("images")
        counts.occurrences += 1
        alt: list[JSONValue] = [_tree.string(alt_text)] if alt_text else []
        target: list[JSONValue] = [_media.image_url(published, paths), ""]
        content[0], content[1], content[2] = _tree.attributes(), alt, target
    return counts


def _start(block: JSONValue) -> int:
    start = _tree.items(_tree.items(_tree.node(block)["c"])[0])[0]
    return start if isinstance(start, int) else 1


def _tight(entries: list[JSONValue]) -> bool:
    """Whether a list of *entries* says the same written tight: each item one paragraph, or
    none, and perhaps lists, but none numbered from other than 1 right after the paragraph,
    which Markdown would read as more of the paragraph."""
    for entry in entries:
        content = _tree.items(entry)
        if not content:
            continue
        if _tree.tag(content[0]) not in {"Para", "Plain"}:
            return False
        if any(_tree.tag(block) not in _LISTS for block in content[1:]):
            return False
        if len(content) > 1 and _tree.tag(content[1]) == "OrderedList" and _start(content[1]) != 1:
            return False
    return True


def tidy_lists(blocks: list[JSONValue], report: TransformReport) -> None:
    """Omit the list items that show nothing, and write a list tight when it can be.

    Bullets go wherever they are; numbered items go only at the start or end of their list,
    with the start number raised, so the other items keep Word's numbers. Word list items are
    paragraphs, which Markdown writes loose, with a blank line between items; a list of
    one-paragraph items is written tight. Tables write their own lists.
    """

    def block(element: Element) -> list[Element]:
        if element["t"] not in _LISTS:
            return [element]
        ordered = element["t"] == "OrderedList"
        content = _tree.items(element["c"])
        entries = _tree.items(content[1] if ordered else element["c"])
        shown = [_tree.shows_something(entry) for entry in entries]
        if ordered:
            leading = next((index for index, value in enumerate(shown) if value), len(entries))
            trailing = next(
                (index for index, value in enumerate(reversed(shown)) if value), len(entries)
            )
            kept = entries[leading : len(entries) - trailing] if leading < len(entries) else []
            if leading and kept:
                attributes = _tree.items(content[0])
                attributes[0] = _start(element) + leading
        else:
            kept = [entry for entry, value in zip(entries, shown, strict=True) if value]
        report.applied("lists", EMPTY_ITEMS, len(entries) - len(kept))
        if not kept:
            return []
        tight = _tight(kept)
        for entry in kept:
            for position, child in enumerate(_tree.items(entry)):
                item = _tree.node(child)
                if position == 0 and item["t"] in {"Para", "Plain"}:
                    item["t"] = "Plain" if tight else "Para"
                elif item["t"] == "Plain":
                    item["t"] = "Para"
        entries[:] = kept
        report.applied("lists")
        return [element]

    _tree.rewrite(blocks, block=block, skip=frozenset({"Table"}))


@dataclass
class Notes:
    """Word's footnotes and endnotes, by number, each referred to by a placeholder until the
    Markdown is written."""

    token: str = field(default_factory=lambda: "DOCXTOMDNOTE" + uuid.uuid4().hex.upper())
    contents: list[list[JSONValue]] = field(default_factory=list[list[JSONValue]])

    def placeholder(self, number: int) -> str:
        return f"{self.token}N{number}X"

    def references(self, markdown: str) -> str:
        """*markdown* with each placeholder as its footnote reference."""
        return re.sub(re.escape(self.token) + r"N(\d+)X", r"[^\1]", markdown)


def extract_notes(blocks: list[JSONValue], report: TransformReport) -> Notes:
    """Number the notes in document order and replace each with its placeholder; a note that
    shows nothing, which Markdown cannot define, is omitted with a warning."""
    notes = Notes()
    numbers: dict[int, int | None] = {}
    empty = 0
    for block in blocks:
        for element in _tree.walk(block):
            if element["t"] != "Note":
                continue
            if not _shows(element["c"]):
                numbers[id(element)] = None
                empty += 1
                continue
            notes.contents.append(_tree.items(element["c"]))
            numbers[id(element)] = len(notes.contents)
    if numbers:

        def inline(element: Element) -> list[Element]:
            if element["t"] != "Note":
                return [element]
            number = numbers[id(element)]
            return [] if number is None else [_tree.string(notes.placeholder(number))]

        _tree.rewrite(blocks, inline=inline)
    if empty:
        report.warn(
            f"{empty} Word footnote(s) or endnote(s) without text omitted",
            kind="Word footnotes or endnotes without text omitted",
        )
    return notes


def define_notes(
    blocks: list[JSONValue], notes: Notes, pandoc: str, api: JSONValue, math: MathSyntax
) -> None:
    """Define the notes in GFM, in order, at the end of the document."""
    if not notes.contents:
        return
    batch: list[JSONValue] = []
    markers = [f"{notes.token}D{number}X" for number in range(1, len(notes.contents) + 1)]
    for marker, content in zip(markers, notes.contents, strict=True):
        batch.append(_tree.element("Para", [_tree.string(marker)]))
        batch.extend(content)
    written = _tree.write(
        _tree.document(batch, api), pandoc, _tree.gfm_writer(math), "Writing Word's notes"
    )
    bodies: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in written.split("\n"):
        if line.strip() in markers:
            current = bodies.setdefault(line.strip(), [])
        elif current is not None:
            current.append(line)
    definitions: list[str] = []
    for number, marker in enumerate(markers, 1):
        lines = "\n".join(bodies.get(marker, [])).strip("\n").split("\n")
        first = f"[^{number}]: {lines[0]}".rstrip()
        rest = "".join("\n" + (f"    {line}" if line.strip() else "") for line in lines[1:])
        definitions.append(first + rest)
    blocks.append(_tree.raw_block("gfm", "\n\n".join(definitions)))
