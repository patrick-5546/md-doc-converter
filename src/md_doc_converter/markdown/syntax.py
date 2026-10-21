"""Code-aware Markdown transformations shared by preprocessing and conversion."""

from __future__ import annotations

import re
from collections.abc import Callable

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def inline_code_spans(text: str) -> list[tuple[int, int]]:
    """Start and end offsets of the inline backtick code spans in *text*."""
    spans: list[tuple[int, int]] = []
    search_start = 0

    while opener := re.search(r"`+", text[search_start:]):
        opener_start = search_start + opener.start()
        opener_end = search_start + opener.end()
        delimiter = opener.group(0)
        closer = re.search(
            rf"(?<!`){re.escape(delimiter)}(?!`)",
            text[opener_end:],
        )
        if closer is None:
            search_start = opener_end
            continue

        closer_end = opener_end + closer.end()
        spans.append((opener_start, closer_end))
        search_start = closer_end

    return spans


def transform_outside_inline_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply *transform* outside inline backtick code spans."""
    parts: list[str] = []
    prose_start = 0
    for start, end in inline_code_spans(text):
        parts.extend((transform(text[prose_start:start]), text[start:end]))
        prose_start = end
    parts.append(transform(text[prose_start:]))
    return "".join(parts)


def transform_outside_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply *transform* to Markdown prose, preserving inline and fenced code."""
    parts: list[str] = []
    prose_start = 0
    fence_start: int | None = None
    fence_char = ""
    fence_length = 0
    offset = 0

    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if fence_start is None:
            opener = re.match(r"^ {0,3}(`{3,}|~{3,})", body)
            if opener:
                marker = opener.group(1)
                parts.append(transform_outside_inline_code(text[prose_start:offset], transform))
                fence_start = offset
                fence_char = marker[0]
                fence_length = len(marker)
        else:
            closer = re.match(r"^ {0,3}(`+|~+)[ \t]*$", body)
            if closer:
                marker = closer.group(1)
                if marker[0] == fence_char and len(marker) >= fence_length:
                    line_end = offset + len(line)
                    parts.append(text[fence_start:line_end])
                    prose_start = line_end
                    fence_start = None
                    fence_char = ""
                    fence_length = 0
        offset += len(line)

    if fence_start is not None:
        parts.append(text[fence_start:])
    else:
        parts.append(transform_outside_inline_code(text[prose_start:], transform))
    return "".join(parts)


RAW_BLOCK_TAGS = (
    "address|article|aside|blockquote|body|caption|center|dd|details|dialog|dir|div|dl|dt|"
    "fieldset|figcaption|figure|footer|form|h[1-6]|head|header|hgroup|hr|html|iframe|"
    "legend|li|main|menu|nav|ol|p|pre|script|section|style|summary|table|tbody|td|"
    "textarea|tfoot|th|thead|title|tr|ul"
)
# A tag name ends at whitespace, ">", "/>" or the line end, so an autolink such as
# <https://example.com> or <name@example.com> is not a tag.
TAG_NAME_END = r"(?=[ \t]|/?>|$)"


def container_line(line: str) -> tuple[str, str]:
    match = re.match(r"^( {0,3}(?:>[ \t]? {0,3})+)(.*)$", line, re.DOTALL)
    return (match.group(1), match.group(2)) if match else ("", line)


def markdown_prose_lines(markdown: str) -> set[int]:
    """Recognize literal fences, indented code, and raw HTML before editing syntax."""
    eligible: set[int] = set()
    fence: tuple[str, int, int] | None = None
    raw_tag: str | None = None
    raw_depth = 0
    raw_end: str | None = None
    incomplete_tag = False
    list_indents: list[int] = []
    blank = True
    indented_code = False
    for index, original in enumerate(markdown.splitlines()):
        _quote, line = container_line(original)
        expanded = line.expandtabs(4)
        indentation = len(expanded) - len(expanded.lstrip(" "))
        text = expanded.lstrip(" ")
        if fence is not None:
            character, length, _indent = fence
            if re.fullmatch(rf"{re.escape(character)}{{{length},}}\s*", text):
                fence = None
            continue
        if raw_end is not None:
            if raw_end in line:
                raw_end = None
            continue
        if raw_tag is not None:
            if raw_tag in {"script", "style", "textarea"}:
                if re.search(rf"</{raw_tag}\s*>", line, re.IGNORECASE):
                    raw_tag = None
                continue
            if incomplete_tag and ">" in line:
                incomplete_tag = False
                if "/>" in line:
                    raw_depth -= 1
            for closing, self_closing in re.findall(
                rf"<(/?){re.escape(raw_tag)}\b[^>]*?(/?)>", line, re.IGNORECASE
            ):
                raw_depth += -1 if closing else (0 if self_closing else 1)
            if raw_depth <= 0:
                raw_tag = None
            continue
        if not text:
            blank = True
            continue
        marker = re.match(r"(`{3,}|~{3,})(.*)$", text)
        allowed_indent = (list_indents[-1] if list_indents else 0) + 3
        if marker and indentation <= allowed_indent:
            token = marker.group(1)
            if token[0] != "`" or "`" not in marker.group(2):
                fence = (token[0], len(token), indentation)
                continue
        for start, end in (("<!--", "-->"), ("<?", "?>"), ("<![CDATA[", "]]>")):
            if text.startswith(start):
                if end not in text[len(start) :]:
                    raw_end = end
                break
        else:
            start_html = re.match(
                rf"<({RAW_BLOCK_TAGS}|[A-Za-z][\w:-]*){TAG_NAME_END}", text, re.IGNORECASE
            )
            if start_html:
                tag = start_html.group(1).casefold()
                raw_depth = sum(
                    -1 if closing else (0 if self_closing else 1)
                    for closing, self_closing in re.findall(
                        rf"<(/?){re.escape(tag)}\b[^>]*?(/?)>", line, re.IGNORECASE
                    )
                )
                incomplete_tag = ">" not in line
                if incomplete_tag:
                    raw_depth = 1
                if raw_depth > 0 and tag not in VOID_TAGS:
                    raw_tag = tag
                continue
            if re.match(r"<![A-Z]", text) or re.fullmatch(
                rf"</?[A-Za-z][\w:-]*{TAG_NAME_END}[^>]*>\s*", text
            ):
                continue
            list_match = re.match(r"(?:[-+*]|\d+[.)])([ \t]+)", text)
            while list_indents and indentation < list_indents[-1] - 2:
                list_indents.pop()
            code_indent = (list_indents[-1] if list_indents else 0) + 4
            if indentation >= code_indent and (blank or indented_code):
                indented_code = True
                continue
            indented_code = False
            if list_match:
                content_indent = indentation + list_match.end()
                while list_indents and content_indent <= list_indents[-1]:
                    list_indents.pop()
                list_indents.append(content_indent)
            elif indentation == 0:
                list_indents.clear()
            eligible.add(index)
            blank = False
            continue
        continue
    return eligible
