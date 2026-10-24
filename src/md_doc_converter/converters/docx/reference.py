"""Strict reference HTML, URI handling, and converter tool invocation."""

from __future__ import annotations

import html
import json
import re
import string
import subprocess
import sys
import unicodedata
import urllib.parse
from collections.abc import Callable
from html.parser import HTMLParser
from typing import override
from xml.etree import ElementTree as ET

from md_doc_converter import executables
from md_doc_converter.converters.docx.models import ConversionError
from md_doc_converter.markdown.syntax import VOID_TAGS
from md_doc_converter.pandoc import JSONValue, PandocError, ParsedMarkdown, decode_document

# Without bare-URL autolinks: a URL as link text would become a link nested in the link. The
# Markdown docx-to-md writes keeps such URLs as text, which Markdown viewers link when shown.
DOCX_READER = "gfm-autolink_bare_uris+raw_html"
# A web or e-mail address that docx-to-md writes as an autolink when it is plain text.
ADDRESS = (
    r"(?:(?P<url>(?:https?|ftp)://[^\s<>\"'`]+)"
    r"|(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+))"
)


class HTMLTree(HTMLParser):
    """Parse converter HTML without treating void tags as XML containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("div")
        self.stack = [self.root]

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = ET.SubElement(self.stack[-1], tag, {key: value or "" for key, value in attrs})
        if tag not in VOID_TAGS:
            self.stack.append(node)

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ConversionError(f"Unbalanced reference HTML closing tag </{tag}>")
        self.stack.pop()

    @override
    def handle_data(self, data: str) -> None:
        parent = self.stack[-1]
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + data
        else:
            parent.text = (parent.text or "") + data

    @override
    def handle_comment(self, data: str) -> None:
        self.stack[-1].append(ET.Comment(data))


def parse_html(value: str) -> ET.Element:
    parser = HTMLTree()
    parser.feed(value)
    parser.close()
    if len(parser.stack) != 1:
        raise ConversionError(f"Unclosed reference HTML element <{parser.stack[-1].tag}>")
    return parser.root


def html_element(node: ET.Element) -> str:
    tail = node.tail
    node.tail = None
    result = ET.tostring(node, encoding="unicode", method="html")
    node.tail = tail
    return result


def _shows_nothing(item: ET.Element) -> bool:
    """A list item with no text and nothing but empty paragraphs or line breaks."""
    return (
        not item.attrib
        and not "".join(item.itertext()).strip()
        and all(
            node.tag in {"p", "br"} and not node.attrib for node in item.iter() if node is not item
        )
    )


def omit_empty_list_items(root: ET.Element) -> int:
    """Remove list items that show nothing, such as Word list paragraphs without text.

    Bullets go wherever they are. Numbered items go only at the start or end of a list, with
    the start number raised for those at the start, so the other items keep Word's numbers;
    between items they stay. A list left with no items goes too. Return how many items went.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    count = 0
    for node in reversed([node for node in root.iter() if node.tag in {"ol", "ul"}]):
        items = [child for child in node if child.tag == "li"]
        drop = [item for item in items if _shows_nothing(item)]
        if node.tag == "ol":
            try:
                start = int(node.get("start", "1"))
            except ValueError:
                continue
            leading = next(
                (index for index, item in enumerate(items) if not _shows_nothing(item)),
                len(items),
            )
            trailing = next(
                (index for index, item in enumerate(reversed(items)) if not _shows_nothing(item)),
                len(items),
            )
            drop = (
                items[:leading] + items[len(items) - trailing :] if leading < len(items) else items
            )
            if leading and leading < len(items):
                node.set("start", str(start + leading))
        for item in drop:
            node.remove(item)
        count += len(drop)
        owner = parents.get(node)
        if owner is not None and not any(child.tag == "li" for child in node):
            owner.remove(node)
    return count


def run_command(
    command: list[str], description: str, *, input_text: str | None = None, quiet: bool = False
) -> str:
    """Run *command* and return its standard output.

    Its standard error explains a failure; after success it is shown unless *quiet*.
    """
    try:
        result = subprocess.run(
            [executables.resolve(command[0]), *command[1:]],
            input=input_text,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise ConversionError(
            f"{description} requires unavailable command {command[0]!r}"
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise ConversionError(f"{description} failed: {detail or error}") from error
    if result.stderr.strip() and not quiet:
        print(f"{description}: {result.stderr.strip()}", file=sys.stderr)
    return result.stdout


def markdown_ast(markdown: str, pandoc: str, description: str) -> ParsedMarkdown:
    payload = run_command(
        [pandoc, f"--from={DOCX_READER}", "--to=json"], description, input_text=markdown
    )
    try:
        return ParsedMarkdown(markdown, DOCX_READER, decode_document(payload, pandoc=pandoc))
    except PandocError as error:
        raise ConversionError(f"{description}: {error}") from error


def split_uri(target: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(target)
    except ValueError as error:
        raise ConversionError(f"Invalid retained URI {target!r}: {error}") from error
    if "\x00" in urllib.parse.unquote(parsed.path):
        raise ConversionError("Retained URI contains a NUL path character")
    if parsed.scheme.casefold() in {"http", "https"} and not parsed.netloc:
        raise ConversionError(f"Retained HTTP link has no host: {target!r}")
    return parsed


def encoded_uri(target: str) -> str:
    """Use the emitted URI spelling without decoding existing escapes or delimiters."""
    return urllib.parse.quote(target, safe="/:#?=[]&%+@;,$!~*-._")


def plain_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))


def json_list(value: JSONValue) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ConversionError("Unexpected Pandoc JSON array structure")
    return value


def json_string(value: JSONValue) -> str:
    if not isinstance(value, str):
        raise ConversionError("Unexpected Pandoc JSON text structure")
    return value


def code_span(text: str, *, table_cell: bool = False) -> str:
    """*text* as a Markdown code span; in a pipe-table cell, with its pipes escaped.

    GFM ends a cell at any unescaped pipe, even in a code span, and drops the backslash of
    an escaped one before it reads the cell's code spans.
    """
    delimiter = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    padding = (
        text.startswith("`")
        or text.endswith("`")
        or (text.startswith(" ") and text.endswith(" ") and bool(text.strip(" ")))
    )
    if table_cell:
        text = text.replace("|", r"\|")
    space = " " if padding else ""
    return f"{delimiter}{space}{text}{space}{delimiter}"


def code_fence(text: str, info: str = "") -> str:
    """A fenced code block of *text* labeled *info*, with a fence no line of it could close."""
    fence = "```"
    while re.search(rf"^ {{0,3}}{fence}", text, re.MULTILINE):
        fence += "`"
    return f"{fence}{info}\n{text}\n{fence}" if text else f"{fence}{info}\n{fence}"


# Blocks whose content Pandoc's GFM writer writes as Markdown, where a raw GFM fence fits.
_FENCE_CONTAINERS = frozenset({"BlockQuote", "BulletList", "OrderedList", "Div"})


def fence_code_blocks(value: JSONValue) -> JSONValue:
    """Write labeled code blocks as raw GFM fences that keep their language label.

    Since 3.10.1, Pandoc's GFM writer only writes a class that is one of its highlighting
    languages, so it drops labels such as ``text``
    (https://github.com/jgm/pandoc/issues/11987). An unlabeled block, which the writer
    indents, and a block with an id or attributes, or one in a table, figure or definition
    list, which the writer may write as HTML, stays as is.
    """
    if isinstance(value, dict):
        blocks = value.get("blocks")
        return {**value, "blocks": _fence_blocks(blocks)} if isinstance(blocks, list) else value
    return _fence_blocks(value) if isinstance(value, list) else value


def _fence_blocks(blocks: list[JSONValue]) -> list[JSONValue]:
    return [_fence_block(block) for block in blocks]


def _fence_block(block: JSONValue) -> JSONValue:
    if not isinstance(block, dict):
        return block
    kind, content = block.get("t"), block.get("c")
    if kind == "CodeBlock" and isinstance(content, list) and len(content) == 2:
        attributes, text = content
        if (
            isinstance(attributes, list)
            and len(attributes) == 3
            and attributes[0] == ""
            and attributes[2] == []
            and isinstance(attributes[1], list)
            and attributes[1]
            and isinstance(text, str)
        ):
            info = attributes[1][0]
            if isinstance(info, str) and info and not re.search(r"[\s`]", info):
                return {"t": "RawBlock", "c": ["gfm", code_fence(text, info)]}
        return block
    if kind not in _FENCE_CONTAINERS or not isinstance(content, list):
        return block
    if kind == "BlockQuote":
        return {**block, "c": _fence_blocks(content)}
    if kind == "BulletList":
        return {**block, "c": [_fence_blocks(json_list(item)) for item in content]}
    if kind == "OrderedList" and len(content) == 2:
        items = json_list(content[1])
        return {**block, "c": [content[0], [_fence_blocks(json_list(item)) for item in items]]}
    if kind == "Div" and len(content) == 2:
        return {**block, "c": [content[0], _fence_blocks(json_list(content[1]))]}
    return block


def _pipe_table(content: list[JSONValue]) -> bool:
    """Whether Pandoc's CommonMark writers write a table with this content as a pipe table,
    rather than as HTML: when each cell holds at most one paragraph, without line breaks."""
    try:
        _attr, _caption, _specs, head, bodies, foot = content
        sections = [json_list(head)[1], json_list(foot)[1]]
        for body in json_list(bodies):
            sections.extend(json_list(body)[2:4])
        cells = [
            json_list(json_list(cell)[4])
            for section in sections
            for row in json_list(section)
            for cell in json_list(json_list(row)[1])
        ]
    except ConversionError, ValueError, IndexError:
        return False
    return all(
        not blocks
        or (
            len(blocks) == 1
            and isinstance(blocks[0], dict)
            and blocks[0].get("t") in ("Plain", "Para")
            and not _has_line_break(blocks[0])
        )
        for blocks in cells
    )


def escape_link_titles(value: JSONValue) -> JSONValue:
    r"""Escape the characters a Markdown link title reads specially: \, " and &.

    Pandoc's GFM writer (3.12.1) writes a title between double quotes as it is, so a double
    quote, or a final backslash, breaks the link, and an entity such as &amp; reads back as
    its character. Since 3.12, Pandoc reads Word ScreenTips as link titles. Spaces around a
    title are dropped: Pandoc's Markdown reader reads no link whose title starts with one.
    """
    if isinstance(value, dict):
        kind, content = value.get("t"), value.get("c")
        if kind in {"Link", "Image"} and isinstance(content, list) and len(content) == 3:
            attributes, inlines, target = content
            if isinstance(target, list) and len(target) == 2 and isinstance(target[1], str):
                title = re.sub(r'([\\"&])', r"\\\1", target[1].strip())
                return {**value, "c": [attributes, escape_link_titles(inlines), [target[0], title]]}
        return {key: escape_link_titles(item) for key, item in value.items()}
    if isinstance(value, list):
        return [escape_link_titles(item) for item in value]
    return value


def gfm_input(value: JSONValue) -> str:
    """Pandoc JSON for its GFM writer, with the fixes its output needs."""
    return json.dumps(protect_backslashes(fence_code_blocks(escape_link_titles(value))))


# A backslash written as a character reference, which Markdown and HTML read as a backslash.
BACKSLASH_REFERENCE = "&#92;"


def protect_backslashes(value: JSONValue, *, table_cell: bool = False) -> JSONValue:
    r"""Give text that Pandoc's CommonMark writers garble a form that reads back unchanged.

    Those writers drop the character after a backslash that precedes ASCII punctuation:
    "a\*b" is written "a\\b", which reads back as "a\b". "&#92;" reads back as a
    backslash, both in Markdown and in the HTML the writers use for complex tables. They
    also leave a pipe in a code span in a pipe table unescaped, which ends the cell; such a
    span is written as raw Markdown, with the pipe escaped.
    """
    if isinstance(value, dict):
        kind, content = value.get("t"), value.get("c")
        if table_cell and kind == "Code" and isinstance(content, list) and len(content) == 2:
            text = content[1]
            if isinstance(text, str) and "|" in text:
                return {"t": "RawInline", "c": ["gfm", code_span(text, table_cell=True)]}
        if kind == "Table" and isinstance(content, list) and _pipe_table(content):
            # The caption, second, is written as a paragraph; the head, bodies, and foot follow.
            parts = [
                protect_backslashes(part, table_cell=index > 2)
                for index, part in enumerate(content)
            ]
            return {**value, "c": parts}
        return {
            key: protect_backslashes(item, table_cell=table_cell) for key, item in value.items()
        }
    if not isinstance(value, list):
        return value
    result: list[JSONValue] = []
    for item in value:
        text = item.get("c") if isinstance(item, dict) and item.get("t") == "Str" else None
        if not isinstance(text, str) or "\\" not in text:
            result.append(protect_backslashes(item, table_cell=table_cell))
            continue
        start = 0
        for index, character in enumerate(text):
            following = text[index + 1 : index + 2]
            if character == "\\" and (not following or following in string.punctuation):
                if index > start:
                    result.append({"t": "Str", "c": text[start:index]})
                result.append({"t": "RawInline", "c": ["html", BACKSLASH_REFERENCE]})
                start = index + 1
        if start < len(text):
            result.append({"t": "Str", "c": text[start:]})
    return result


def _has_line_break(value: JSONValue) -> bool:
    if isinstance(value, dict):
        if value.get("t") == "LineBreak":
            return True
        if value.get("t") == "Note":
            return False
        return any(_has_line_break(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_line_break(item) for item in value)
    return False


def pandoc_html(markdown: str, pandoc: str) -> ET.Element:
    return parse_html(
        run_command(
            [
                pandoc,
                f"--from={DOCX_READER}",
                "--to=html5",
                "--wrap=none",
            ],
            "Markdown integrity parsing",
            input_text=markdown,
        )
    )


class HTMLLinkRewriter(HTMLParser):
    """Rewrite URL attributes only, including raw HTML inside a Pandoc AST."""

    def __init__(self, rewrite: Callable[[str], str]) -> None:
        super().__init__(convert_charrefs=False)
        self.rewrite = rewrite
        self.parts: list[str] = []
        self.literal: list[str] = []
        self.ids: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        original = self.get_starttag_text()
        if original is None:
            raise ConversionError("Cannot recover original HTML start tag")
        if self.literal or tag in {"pre", "code", "script", "style", "textarea"}:
            if tag not in VOID_TAGS:
                self.literal.append(tag)
            self.parts.append(original)
            return
        self.ids.extend(
            value
            for key, value in attrs
            if (key == "id" or (tag == "a" and key == "name")) and value is not None
        )

        def replace(match: re.Match[str]) -> str:
            target = html.unescape(match.group(3) if match.group(2) else match.group(4))
            return f'{match.group(1)}"{html.escape(self.rewrite(target), quote=True)}"'

        self.parts.append(
            re.sub(
                r"(\b(?:href|src|poster|data)\s*=\s*)(?:(['\"])(.*?)\2|([^\s>]+))",
                replace,
                original,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        previous = len(self.literal)
        self.handle_starttag(tag, attrs)
        del self.literal[previous:]

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.literal and self.literal[-1] == tag:
            self.literal.pop()
        self.parts.append(f"</{tag}>")

    @override
    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    @override
    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    @override
    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    @override
    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    @override
    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    @override
    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")


def rewrite_html_targets(value: str, rewrite: Callable[[str], str]) -> str:
    parser = HTMLLinkRewriter(rewrite)
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def declared_html_ids(markdown: str, pandoc: str) -> set[str]:
    if re.search(r"\b(?:id|name)\s*=", markdown, re.IGNORECASE) is None:
        return set()
    data = markdown_ast(markdown, pandoc, "Generated anchor inspection").ast
    raw: list[str] = []

    def collect(value: JSONValue) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if value.get("t") in ("RawInline", "RawBlock"):
                content = json_list(value.get("c"))
                if len(content) == 2 and content[0] == "html":
                    raw.append(json_string(content[1]))
            for item in value.values():
                collect(item)

    collect(data)
    parser = HTMLLinkRewriter(lambda target: target)
    parser.feed("".join(raw))
    parser.close()
    return set(parser.ids)
