from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from md_doc_converter import pandoc as _pandoc
from md_doc_converter.pandoc import JSONValue
from md_doc_converter.resources import asset_path


@dataclass(frozen=True)
class Heading:
    level: int
    identifier: str
    text: str


class HeadingError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeaderBlock:
    """A Header block of Pandoc JSON, and whether it is in a table."""

    node: dict[str, JSONValue]
    in_table: bool

    @property
    def identifier(self) -> str:
        return _parse_header(self.node).identifier

    def set_identifier(self, identifier: str) -> None:
        content = self.node.get("c")
        if not isinstance(content, list) or len(content) != 3:
            raise HeadingError("pandoc JSON contained a malformed Header block")
        attributes = content[1]
        if not isinstance(attributes, list) or not attributes:
            raise HeadingError("pandoc JSON Header attribute did not include an identifier")
        attributes[0] = identifier


def header_blocks(blocks: Sequence[JSONValue]) -> list[HeaderBlock]:
    """Each Header block in *blocks*, in document order.

    collect_headings() reads those that are not in a table.
    """
    found: list[HeaderBlock] = []

    def visit(value: JSONValue, in_table: bool) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, in_table)
        elif isinstance(value, dict):
            if value.get("t") == "Header":
                found.append(HeaderBlock(value, in_table))
            else:
                visit(value.get("c"), in_table or value.get("t") == "Table")

    visit(list(blocks), False)
    return found


def combined_identifiers(
    pages: Sequence[Sequence[HeaderBlock]],
    *,
    reader: str,
    api: JSONValue,
    pandoc: str = "pandoc",
) -> list[str]:
    """The identifiers that Pandoc's *reader* gives the Header blocks of *pages*, each page's
    in document order, when the pages are read as one document.

    Each page's identifiers are its own, made unique across the pages, as Pandoc makes them:
    one Pandoc run writes each page's headings as Markdown on their own, and reads them all back
    as one document (assets/heading-ids.lua). *api* is the pages' pandoc-api-version.
    """
    count = sum(len(page) for page in pages)
    if not count:
        return []
    document: dict[str, JSONValue] = {
        "pandoc-api-version": api,
        "meta": {"md-doc-converter-reader": {"t": "MetaString", "c": reader}},
        "blocks": [
            {"t": "Div", "c": [["", [], []], [header.node for header in page]]} for page in pages
        ],
    }
    try:
        payload = _pandoc.run(
            json.dumps(document),
            "--from=json",
            "--to=json",
            "--lua-filter",
            str(asset_path("heading-ids.lua")),
            pandoc=pandoc,
            description="identifying headings",
        )
        blocks = _pandoc.decode_document(payload, pandoc=pandoc)["blocks"]
    except _pandoc.PandocError as error:
        raise HeadingError(str(error)) from error
    if not isinstance(blocks, list) or len(blocks) != count:
        raise HeadingError(f"Pandoc read {count} headings as something else")
    identifiers: list[str] = []
    for block in blocks:
        if _tag(block) != "Header":
            raise HeadingError(f"Pandoc read a heading as {_tag(block)}")
        identifiers.append(_parse_header(block).identifier)
    return identifiers


def collect_headings(blocks: Sequence[object]) -> list[Heading]:
    """Collect heading identities from already-parsed Pandoc blocks."""
    found: list[Heading] = []
    for block in blocks:
        found.extend(_collect_from_block(block))
    return found


def _collect_from_block(block: object) -> list[Heading]:
    tag = _tag(block)
    if tag == "Header":
        return [_parse_header(block)]
    if tag == "Table":
        return []

    content = _content(block)
    if content is None:
        return []
    return _collect_from_value(content)


def _collect_from_value(value: object) -> list[Heading]:
    found: list[Heading] = []
    if isinstance(value, dict):
        found.extend(_collect_from_block(value))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_from_value(item))
    return found


def _parse_header(block: object) -> Heading:
    content = _content(block)
    if not isinstance(content, list) or len(content) != 3:
        raise HeadingError("pandoc JSON contained a malformed Header block")

    level, attr, inlines = content
    if not isinstance(level, int):
        raise HeadingError("pandoc JSON Header level was not an integer")
    if not isinstance(attr, list) or not attr or not isinstance(attr[0], str):
        raise HeadingError("pandoc JSON Header attribute did not include an identifier")

    return Heading(level=level, identifier=attr[0], text=_inline_text(inlines))


def _tag(node: object) -> str | None:
    if not isinstance(node, dict):
        return None
    tag = node.get("t")
    return tag if isinstance(tag, str) else None


def _content(node: object) -> object | None:
    if not isinstance(node, dict):
        return None
    return node.get("c")


def _inline_text(value: object) -> str:
    if isinstance(value, list):
        return "".join(_inline_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    tag = _tag(value)
    content = _content(value)
    if tag == "Str" and isinstance(content, str):
        return content
    if tag in {"Space", "SoftBreak", "LineBreak"}:
        return " "
    if tag in {"Code", "Math"} and isinstance(content, list) and len(content) >= 2:
        text = content[1]
        return text if isinstance(text, str) else ""
    if tag in {"Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
        return _inline_text(content)
    if tag in {"Quoted", "Cite"} and isinstance(content, list) and len(content) >= 2:
        return _inline_text(content[1])
    if tag == "Span" and isinstance(content, list) and len(content) >= 2:
        return _inline_text(content[1])
    if tag in {"Link", "Image"} and isinstance(content, list) and len(content) >= 2:
        return _inline_text(content[1])
    return ""
