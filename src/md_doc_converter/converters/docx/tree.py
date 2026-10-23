"""Pandoc's document tree of a Word document: read with Pandoc, rewritten, written as GFM.

Elements are Pandoc's JSON: dictionaries with a tag "t" and, for most, content "c". Block and
inline tags differ, so a list of elements is a list of blocks or of inlines by its first tag.
Traversals use explicit stacks, because Word documents can nest tables deeper than Python's
recursion limit.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.models import ConversionError
from md_doc_converter.pandoc import JSONValue, PandocError, decode_document

Element = dict[str, JSONValue]
# Replaces one element of a list with the elements returned, possibly none.
Rewrite = Callable[[Element], list[Element]]
MathSyntax = Literal["github", "dollars"]

BLOCKS = frozenset(
    {
        "Plain",
        "Para",
        "LineBlock",
        "CodeBlock",
        "RawBlock",
        "BlockQuote",
        "OrderedList",
        "BulletList",
        "DefinitionList",
        "Header",
        "HorizontalRule",
        "Table",
        "Figure",
        "Div",
    }
)
INLINES = frozenset(
    {
        "Str",
        "Emph",
        "Underline",
        "Strong",
        "Strikeout",
        "Superscript",
        "Subscript",
        "SmallCaps",
        "Quoted",
        "Cite",
        "Code",
        "Space",
        "SoftBreak",
        "LineBreak",
        "Math",
        "RawInline",
        "Link",
        "Image",
        "Note",
        "Span",
    }
)
# Inlines whose content is only a list of inlines.
WRAPPERS = frozenset(
    {"Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps"}
)
BREAKS = frozenset({"Space", "SoftBreak", "LineBreak"})


def items(value: JSONValue) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ConversionError("Unexpected Pandoc JSON: a list was expected")
    return value


def text(value: JSONValue) -> str:
    if not isinstance(value, str):
        raise ConversionError("Unexpected Pandoc JSON: text was expected")
    return value


def node(value: JSONValue) -> Element:
    if not isinstance(value, dict) or not isinstance(value.get("t"), str):
        raise ConversionError("Unexpected Pandoc JSON: an element was expected")
    return value


def tag(value: JSONValue) -> str | None:
    if isinstance(value, dict):
        found = value.get("t")
        return found if isinstance(found, str) else None
    return None


def element(name: str, content: JSONValue = None) -> Element:
    return {"t": name} if content is None else {"t": name, "c": content}


def string(value: str) -> Element:
    return element("Str", value)


def raw_inline(form: str, value: str) -> Element:
    return element("RawInline", [form, value])


def raw_block(form: str, value: str) -> Element:
    return element("RawBlock", [form, value])


def attributes(name: str = "", classes: list[str] | None = None) -> list[JSONValue]:
    return [name, list[JSONValue](classes or []), []]


def kind(value: list[JSONValue]) -> Literal["block", "inline"] | None:
    """Whether *value* is a list of blocks or of inlines, by its first element."""
    first = tag(value[0]) if value else None
    if first in BLOCKS:
        return "block"
    if first in INLINES:
        return "inline"
    return None


def inline_children(inline: Element) -> list[JSONValue] | None:
    """The list of inlines that *inline* holds, if it holds one."""
    name, content = inline["t"], inline.get("c")
    if name in WRAPPERS:
        return items(content)
    if name in {"Quoted", "Cite", "Span"}:
        return items(items(content)[1])
    if name in {"Link", "Image"}:
        return items(items(content)[1])
    return None


def block_inlines(block: Element) -> list[JSONValue] | None:
    """The inlines of a paragraph, plain block, or heading."""
    if block["t"] in {"Para", "Plain"}:
        return items(block["c"])
    if block["t"] == "Header":
        return items(items(block["c"])[2])
    return None


def _containers(value: JSONValue, skip: frozenset[str]) -> list[JSONValue]:
    """The lists and dictionaries in *value*, in order, except in elements tagged in *skip*."""
    if isinstance(value, dict):
        if value.get("t") in skip:
            return []
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return []
    return [child for child in children if isinstance(child, (list, dict))]


def lists(root: JSONValue, skip: frozenset[str] = frozenset()) -> list[list[JSONValue]]:
    """Every list in *root*, in document order, except in elements tagged in *skip*, with each
    list after the lists it holds."""
    order: list[list[JSONValue]] = []
    stack: list[tuple[JSONValue, bool]] = [(root, False)]
    while stack:
        value, done = stack.pop()
        if done:
            if isinstance(value, list):
                order.append(value)
            continue
        if isinstance(value, list):
            stack.append((value, True))
        stack.extend((child, False) for child in reversed(_containers(value, skip)))
    return order


def rewrite(
    root: JSONValue,
    *,
    block: Rewrite | None = None,
    inline: Rewrite | None = None,
    skip: frozenset[str] = frozenset(),
) -> None:
    """Replace, in place, each block with block(block) and each inline with inline(inline),
    except inside elements tagged in *skip*.

    Children come first, so a function sees content already rewritten; what it returns is not
    rewritten again.
    """
    for value in lists(root, skip):
        found = kind(value)
        visit = block if found == "block" else inline if found == "inline" else None
        if visit is not None:
            value[:] = [result for child in value for result in visit(node(child))]


def walk(root: JSONValue, skip: frozenset[str] = frozenset()) -> Iterator[Element]:
    """Every element of *root*, in document order; the content of elements tagged in *skip*
    is left out."""
    stack: list[JSONValue] = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, dict) and isinstance(value.get("t"), str):
            yield value
        stack.extend(reversed(_containers(value, skip)))


def stringify(value: JSONValue) -> str:
    """The text that *value* shows: text, code, and math; breaks are spaces; notes are left out."""
    parts: list[str] = []
    stack: list[JSONValue] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(reversed(current))
            continue
        name = tag(current)
        if name is None or name in {"Note", "RawInline", "RawBlock"}:
            continue
        element_ = node(current)
        if name == "Str":
            parts.append(text(element_["c"]))
        elif name in BREAKS:
            parts.append(" ")
        elif name in {"Code", "Math", "CodeBlock"}:
            parts.append(text(items(element_["c"])[1]))
        else:
            children = inline_children(element_) if name in INLINES else None
            if children is not None:
                stack.append(children)
            elif name in BLOCKS:
                stack.append(element_.get("c"))
    return "".join(parts)


def shows_something(value: JSONValue) -> bool:
    """Whether *value* shows text or an image."""
    return bool(stringify(value).strip()) or any(found["t"] == "Image" for found in walk(value))


def read_docx(path: Path, media: Path, pandoc: str) -> dict[str, JSONValue]:
    """Pandoc's tree of the Word document at *path*, its images extracted under *media*.

    Empty paragraphs are kept, because a table cell shows them as blank lines.
    """
    output = _reference.run_command(
        [
            pandoc,
            str(path),
            "--from=docx+empty_paragraphs",
            "--to=json",
            "--track-changes=accept",
            f"--extract-media={media}",
        ],
        "Pandoc reading the Word document",
    )
    try:
        return decode_document(output, pandoc=pandoc)
    except (PandocError, RecursionError) as error:
        raise ConversionError(
            f"Pandoc's reading of the Word document cannot be used: {error}"
        ) from error


def document(blocks: list[JSONValue], api: JSONValue) -> dict[str, JSONValue]:
    return {"pandoc-api-version": api, "meta": {}, "blocks": blocks}


def _escape_destinations(root: JSONValue) -> None:
    r"""Escape, in place, what Pandoc's GFM writer (3.12.1) writes as is in link and image
    destinations: an ampersand, which could start a character reference such as &copy;, and
    in a title, \, " and & (https://github.com/jgm/pandoc/issues/11994). A title's
    surrounding spaces go: Markdown reads no link whose title starts with one."""
    for found in walk(root):
        if found["t"] in {"Link", "Image"}:
            target = items(items(found["c"])[2])
            target[0] = html.escape(text(target[0]), quote=False)
            target[1] = re.sub(r'([\\"&])', r"\\\1", text(target[1]).strip())


def write(tree: dict[str, JSONValue], pandoc: str, to: str, description: str) -> str:
    """*tree* written by Pandoc in the format *to*; for GFM, its destinations are escaped
    in place first."""
    if to.startswith("gfm"):
        _escape_destinations(tree["blocks"])
    try:
        payload = json.dumps(tree)
    except RecursionError as error:
        raise ConversionError(f"{description}: the document is nested too deeply") from error
    return _reference.run_command(
        [pandoc, "--from=json", f"--to={to}", "--wrap=none"],
        description,
        input_text=payload,
    )


def gfm_writer(math: MathSyntax) -> str:
    """The GFM writer: GitHub's math syntax, or TeX's dollars."""
    return "gfm" if math == "github" else "gfm-tex_math_gfm"
