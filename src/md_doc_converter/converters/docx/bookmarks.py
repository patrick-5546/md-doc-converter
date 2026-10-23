"""Word bookmarks and internal links in Pandoc's tree: where each leads, for the pages.

Pandoc writes a link to a bookmarked heading as a link to the heading's ID, and any other
bookmark as an empty span with the bookmark's name, which Markdown cannot hold. Each bookmark
leads instead to the heading it marks or, inside a section, to the section's heading.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator

from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    BookmarkSection,
    ConversionError,
    DocumentNavigation,
    SourceHeading,
)
from md_doc_converter.converters.docx.prepare import MISSING_BOOKMARK
from md_doc_converter.converters.docx.tree import Element, JSONValue

# Content that shows something, so that a bookmark before it is not at the next heading.
_VISIBLE = frozenset({"Code", "Math", "Image", "CodeBlock", "RawBlock", "Table"})


def _anchor(span: Element) -> str | None:
    """The bookmark name of a span that Pandoc made of a Word bookmark."""
    identity, classes, _attributes = _tree.items(_tree.items(span["c"])[0])
    return _tree.text(identity) if "anchor" in _tree.items(classes) and identity else None


def _events(blocks: JSONValue) -> Iterator[tuple[Element, bool, str | None]]:
    """Each element in document order, whether it is in a table, and the key of the body
    heading that holds it, if any."""
    stack: list[tuple[JSONValue, bool, str | None]] = [(blocks, False, None)]
    while stack:
        value, in_table, heading = stack.pop()
        if isinstance(value, dict) and isinstance(value.get("t"), str):
            element = _tree.node(value)
            yield element, in_table, heading
            if element["t"] == "Table":
                in_table = True
            elif element["t"] == "Header" and not in_table:
                heading = _tree.text(_tree.items(_tree.items(element["c"])[1])[0])
            children: list[JSONValue] = list(value.values())
        elif isinstance(value, list):
            children = value
        else:
            continue
        stack.extend(
            (child, in_table, heading)
            for child in reversed(children)
            if isinstance(child, (list, dict))
        )


def read_probe(blocks: list[JSONValue], probe: str, names: list[str]) -> dict[str, str]:
    """Remove the paragraph of links to each Word bookmark in *names* that the preparation
    ends the document with, and return the ID that Pandoc gave each bookmark, by name."""
    if not probe or not names:
        return {}
    for index in range(len(blocks) - 1, -1, -1):
        inlines = _tree.block_inlines(_tree.node(blocks[index]))
        if inlines and _tree.tag(inlines[0]) == "Str" and _tree.node(inlines[0])["c"] == probe:
            break
    else:
        raise ConversionError("Pandoc's reading of the Word document lost its bookmarks")
    del blocks[index]
    targets = [
        _tree.text(_tree.items(_tree.items(found["c"])[2])[0])
        for found in map(_tree.node, inlines)
        if found["t"] == "Link"
    ]
    if len(targets) != len(names) or not all(target.startswith("#") for target in targets):
        raise ConversionError("Pandoc's reading of the Word document lost its bookmarks")
    return {name: target[1:] for name, target in zip(names, targets, strict=True)}


def collect_navigation(
    blocks: list[JSONValue], resolved: dict[str, str], report: TransformReport
) -> DocumentNavigation:
    """Record the body headings, the section of each Word bookmark, and every link target;
    remove the bookmarks' spans; and unlink a link to a bookmark that the document lacks.

    *resolved* gives the ID that Pandoc gave each Word bookmark: its own name, or the ID of the
    heading that holds it or of a bookmark beside it.
    """
    navigation = DocumentNavigation()
    sections = navigation.bookmark_sections
    current: str | None = None
    pending: list[str] = []
    spans: set[str] = set()
    for element, in_table, heading in _events(blocks):
        name = element["t"]
        if name == "Header" and in_table:
            key = _tree.text(_tree.items(_tree.items(element["c"])[1])[0])
            if key:
                sections.setdefault(key, BookmarkSection(current, False))
        elif name == "Header":
            level, attributes, inlines = _tree.items(element["c"])
            key = _tree.text(_tree.items(attributes)[0])
            if not isinstance(level, int) or not key:
                continue
            navigation.headings.append(SourceHeading(key, level, False, _tree.stringify(inlines)))
            sections.setdefault(key, BookmarkSection(key, True))
            for bookmark in pending:
                sections[bookmark] = BookmarkSection(key, True)
            pending.clear()
            current = key
        elif name == "Span" and (bookmark := _anchor(element)) is not None:
            if bookmark in sections:
                continue
            spans.add(bookmark)
            if heading is not None:
                sections[bookmark] = BookmarkSection(heading, True)
            elif in_table:
                sections[bookmark] = BookmarkSection(current, False)
            else:
                # A bookmark with nothing visible before the next heading is at that heading,
                # as when Enter at the start of a heading leaves its bookmark above it.
                pending.append(bookmark)
        elif name in _VISIBLE or (name == "Str" and _tree.text(element["c"]).strip()):
            for bookmark in pending:
                sections[bookmark] = BookmarkSection(current, False)
            pending.clear()
        if name == "Link":
            navigation.link_targets.append(_tree.text(_tree.items(_tree.items(element["c"])[2])[0]))
    for bookmark in pending:
        sections[bookmark] = BookmarkSection(current, False)
    for bookmark, identity in resolved.items():
        if identity in sections:
            sections.setdefault(bookmark, sections[identity])
            navigation.bookmarks.add(bookmark)
    # Bookmarks that the preparation did not list, such as those in notes, keep their names.
    navigation.bookmarks.update(spans - set(resolved.values()))

    def inline(element: Element) -> list[Element]:
        if element["t"] == "Span" and _anchor(element) is not None:
            return [_tree.node(child) for child in _tree.items(_tree.items(element["c"])[1])]
        if element["t"] != "Link":
            return [element]
        target = _tree.text(_tree.items(_tree.items(element["c"])[2])[0])
        if not target.startswith("#"):
            return [element]
        name = urllib.parse.unquote(target[1:])
        if name in sections:
            return [element]
        report.skipped(
            "links", MISSING_BOOKMARK, f"link to missing Word bookmark {name!r}; kept as plain text"
        )
        return [_tree.node(child) for child in _tree.items(_tree.items(element["c"])[1])]

    _tree.rewrite(blocks, inline=inline)
    return navigation
