from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from md_doc_converter import pandoc as _pandoc


@dataclass(frozen=True)
class Heading:
    level: int
    identifier: str
    text: str


class HeadingError(RuntimeError):
    pass


def headings(
    markdown: str, *, pandoc: str = "pandoc", reader: str = "gfm+raw_html"
) -> list[Heading]:
    """Return Pandoc-assigned heading identities for *markdown*.

    The document is parsed once through Pandoc's typed JSON AST. Only real
    Markdown ``Header`` blocks outside tables are returned; headings appearing
    as literal code/examples or inside table cells are ignored.
    """
    try:
        document = _pandoc.parse_markdown(
            markdown, pandoc=pandoc, reader=reader, description="parsing markdown headings"
        )
    except _pandoc.PandocError as error:
        raise HeadingError(str(error)) from error
    return collect_headings(document.blocks)


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
