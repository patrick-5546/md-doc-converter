"""Content-preserving Markdown repairs used by DOCX recovery and splitting."""

from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from functools import partial

from md_doc_converter import pandoc as _pandoc
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.markdown import preprocess as _preprocess
from md_doc_converter.markdown import syntax as _syntax
from md_doc_converter.markdown.document import READER as _RENDER_READER
from md_doc_converter.markdown.document import strip_column_widths as _strip_column_widths
from md_doc_converter.pandoc import JSONValue


def normalize_ordered_list_spacing(markdown: str) -> tuple[str, int]:
    """Write one space after each ordered list marker, as Markdown linters expect.

    Pandoc pads a marker to four columns, as in "1.  Item", and indents the item's other lines
    to its content, such as later paragraphs, nested lists and code; they move left with the
    content, keeping their own indentation.
    """
    lines = markdown.splitlines(keepends=True)
    fixes = 0
    for index in sorted(_syntax.markdown_prose_lines(markdown)):
        quote, line = _split_quote(lines[index])
        match = re.match(r"([ \t]*\d{1,9}[.)])( {2,4})(?=\S)", line)
        if match is None:
            continue
        lines[index] = quote + match.group(1) + " " + line[match.end() :]
        fixes += 1
        # The item's other lines are indented to its content, in the same block quote.
        removed, depth = len(match.group(2)) - 1, quote.count(">")
        for following in range(index + 1, len(lines)):
            prefix, rest = _split_quote(lines[following])
            if not rest.strip():
                continue
            if prefix.count(">") != depth or len(rest) - len(rest.lstrip(" ")) < match.end():
                break
            lines[following] = prefix + rest[removed:]
    return "".join(lines), fixes


_QUOTE_MARKERS = re.compile(r"(?: {0,3}>[ \t]?)*")


def _split_quote(line: str) -> tuple[str, str]:
    """The block quote markers that open *line*, each with one space after it, and the rest,
    whose indentation is the line's own."""
    prefix = _QUOTE_MARKERS.match(line)
    end = prefix.end() if prefix else 0
    return line[:end], line[end:]


def trim_trailing_whitespace(markdown: str) -> tuple[str, int]:
    """Remove the spaces and no-break spaces that end Markdown lines.

    Word text can end in either. They show nothing, and Markdown linters report them. Code,
    raw HTML, whitespace-only lines, and a line break before a continuing line are unchanged,
    as is whitespace that protects a backslash from becoming a line break.
    """
    lines = markdown.splitlines(keepends=True)
    prose = _syntax.markdown_prose_lines(markdown)
    fixes = 0
    for index in sorted(prose):
        body = lines[index].rstrip("\r\n")
        trimmed = body.rstrip(" \t\u00a0")
        if trimmed == body or not trimmed.strip():
            continue
        if (
            body.endswith("  ")
            and index + 1 in prose
            and not re.fullmatch(r"[ \t>]*(?:[-+*]|\d{1,9}[.)]|#{1,6})", trimmed)
        ):
            continue
        if (len(trimmed) - len(trimmed.rstrip("\\"))) % 2:
            continue
        lines[index] = trimmed + lines[index][len(body) :]
        fixes += 1
    return "".join(lines), fixes


def compact_markdown_tables(markdown: str) -> str:
    """Restore compact, unaligned pipe tables after Pandoc serialization."""
    lines = markdown.splitlines(keepends=True)
    previous_row = table_start = -2
    for index in sorted(_syntax.markdown_prose_lines(markdown)):
        quote, line = _syntax.container_line(lines[index])
        cells = _tables.split_table_row(line)
        if cells is None:
            continue
        if index != previous_row + 1:
            table_start = index
        previous_row = index
        indentation = line[: len(line) - len(line.lstrip(" \t"))]
        if index == table_start + 1 and all(
            re.fullmatch(r":?-+:?", cell.strip()) for cell in cells
        ):
            compact = _tables.compact_table_row("|" + "|".join(["---"] * len(cells)) + "|")
        else:
            compact = _tables.compact_table_row(line)
        lines[index] = quote + indentation + compact + ("\n" if line.endswith("\n") else "")
    return "".join(lines)


def simplify_identifier_escapes(markdown: str, pandoc: str) -> str:
    """Remove conservative escapes from identifiers without changing Markdown semantics."""
    if r"\_" not in markdown:
        return markdown

    def unescape(text: str) -> str:
        return re.sub(
            r"(?<![\w\\])\\_([A-Za-z0-9](?:[A-Za-z0-9_]*[A-Za-z0-9])?)(?![\w\\])",
            r"_\1",
            text,
        )

    eligible = _syntax.markdown_prose_lines(markdown)
    parts: list[str] = []
    prose: list[str] = []
    for index, line in enumerate(markdown.splitlines(keepends=True)):
        if index in eligible:
            prose.append(line)
            continue
        if prose:
            parts.append(_syntax.transform_outside_inline_code("".join(prose), unescape))
            prose.clear()
        parts.append(line)
    if prose:
        parts.append(_syntax.transform_outside_inline_code("".join(prose), unescape))
    candidate = "".join(parts)
    if candidate == markdown:
        return markdown

    def structure(text: str) -> JSONValue:
        return _reference.markdown_ast(text, pandoc, "Identifier escape validation").ast

    # An underscore can pair with a later delimiter outside the identifier.
    unescaped, escaped = _pandoc.run_all(
        [partial(structure, candidate), partial(structure, markdown)]
    )
    return candidate if unescaped == escaped else markdown


_ESCAPE_SEPARATOR = "<!-- md-doc-converter escape check -->"
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CELL_NUMBER = re.compile(r"(?:^\s*|(?<!\\))\|\s*\d{1,9}(\\)[.)]")
_DELIMITER_CELL = re.compile(r"\s*:?-+:?\s*")
_LINE_START = re.compile(r"\s*(?:(?:[-+*]|\d{1,9}[.)])\s+|>\s*)*")
EscapeGroup = tuple[int, ...]


class _Unaligned(Exception):
    """A text in a batched check ran into the separator after it."""


def _escape_groups(line: str) -> list[EscapeGroup]:
    """Offsets of the escapes in *line* that could go, in groups that go or stay together.

    Pandoc's writer escapes every [ and ], and a number that starts a table cell. An escaped
    bracket pair is one group, left out after ")" (a reversed link, MD011), "!" or "]", and
    before "(" or "[" (a link or a reference, MD052), where markdownlint could misread it. It
    is also left out at the start of a line before ":", where it could define a link for the
    whole page.
    """
    escaped: list[int] = []
    start = 0
    for code_start, code_end in [*_syntax.inline_code_spans(line), (len(line), len(line))]:
        escaped += [
            start + match.start()
            for match in re.finditer(r"\\(.)", line[start:code_start])
            if match.group(1) in "[]"
        ]
        start = code_end
    groups: list[EscapeGroup] = []
    opened: list[int] = []
    for position in escaped:
        if line[position + 1] == "[":
            opened.append(position)
        elif not opened:
            groups.append((position,))
        else:
            first = opened.pop()
            before = line[first - 1 : first]
            after = line[position + 2 : position + 3]
            definition = after == ":" and _LINE_START.fullmatch(line[:first]) is not None
            if before not in {")", "!", "]"} and after not in {"(", "["} and not definition:
                groups.append((first, position))
    groups += [(position,) for position in opened]
    if _tables.split_table_row(line) is not None:
        groups += [(match.start(1),) for match in _CELL_NUMBER.finditer(line)]
    return groups


def _without(line: str, groups: Iterable[EscapeGroup]) -> str:
    drop = {position for group in groups for position in group}
    return "".join(character for offset, character in enumerate(line) if offset not in drop)


def _escape_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges split at blank lines before an unindented line, outside code and divs.

    md-to-html's reader makes a <div> and everything up to its </div> one block.
    """
    spans: list[tuple[int, int]] = []
    start = depth = 0
    fence = ""
    for index, line in enumerate(lines):
        match = _CODE_FENCE.match(line)
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = "" if not match.group(2).strip() else fence
            continue
        if match:
            fence = match.group(1)
            continue
        depth = max(0, depth + len(re.findall(r"<div\b", line)) - len(re.findall(r"</div>", line)))
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if not depth and not line.strip() and following.strip() and not following[0].isspace():
            spans.append((start, index + 1))
            start = index + 1
    spans.append((start, len(lines)))
    return [span for span in spans if span[0] < span[1]]


def _table_rows(lines: list[str], span: tuple[int, int]) -> dict[int, tuple[int, ...]]:
    """For each pipe table row in *span*, the lines of a table with just that row."""
    rows: dict[int, tuple[int, ...]] = {}
    header = span[0]
    while header + 1 < span[1]:
        delimiter = _tables.split_table_row(lines[header + 1])
        if (
            _tables.split_table_row(lines[header]) is None
            or delimiter is None
            or not all(_DELIMITER_CELL.fullmatch(cell) for cell in delimiter)
        ):
            header += 1
            continue
        rows[header] = (header, header + 1)
        row = header + 2
        while row < span[1] and _tables.split_table_row(lines[row]) is not None:
            rows[row] = (header, header + 1, row)
            row += 1
        header = row
    return rows


# A line that defines a link reference or a footnote, such as "[x]: https://example.com".
_DEFINITION = re.compile(r" {0,3}\[[^\]\n]+\]:")


def drop_unneeded_escapes(markdown: str, pandoc: str) -> tuple[str, int]:
    """Remove escapes that Pandoc's writer adds where nothing could be read differently.

    A block loses its escapes when both md-doc-converter's reader and md-to-html's reader parse
    it the same without them. In a block that would parse differently, each group is tried alone
    (a table row in a table of its own) and the groups that pass must then pass together. The
    page must still parse the same, or it is returned as it was. Return the page and how
    many escapes went.
    """
    lines = markdown.split("\n")
    groups = {
        index: found
        for index in sorted(_syntax.markdown_prose_lines(markdown))
        if (found := _escape_groups(lines[index]))
    }
    if not groups:
        return markdown, 0
    readers = (_reference.DOCX_READER, _RENDER_READER)
    joiner = "\n\n" + _ESCAPE_SEPARATOR + "\n\n"

    def parse(text: str, reader: str) -> list[JSONValue]:
        if reader == _RENDER_READER:
            # As md-to-html does, keep bit notation such as "[1:0]:" in an HTML table cell
            # from defining a link for every "[1:0]" on the page.
            text = _preprocess.escape_bit_ranges(text, None, Counter())
        blocks = _pandoc.parse_markdown(
            text, reader=reader, pandoc=pandoc, description="Escape validation"
        ).blocks
        # Pandoc derives column widths from the length of a table's lines, but neither the
        # Wiki nor md-to-html uses them.
        _strip_column_widths(blocks)
        return blocks

    def text(indices: Iterable[int], chosen: Mapping[int, Iterable[EscapeGroup]]) -> str:
        return "\n".join(_without(lines[index], chosen.get(index, ())) for index in indices)

    # md-to-html's reader links "[Heading text]" to a heading anywhere on the page, and both
    # readers read "[x]" or "[^x]" by a definition anywhere on it.
    context = [(lines[index], lines[index]) for index in markdown_heading_lines(markdown)]
    context += [
        (lines[index], lines[index])
        for index in sorted(_syntax.markdown_prose_lines(markdown))
        if _DEFINITION.match(lines[index])
    ]

    def same(pairs: list[tuple[str, str]]) -> list[bool]:
        """For each (escaped, unescaped) text, whether both readers parse the two the same."""
        if not pairs:
            return []
        checked = len(pairs)
        pairs = pairs + context
        result = [True] * len(pairs)
        texts = ([old for old, _new in pairs], [new for _old, new in pairs])
        # Each reader parses the escaped and the unescaped texts, all four at once.
        parses = _pandoc.run_all(
            [partial(parse, joiner.join(side), reader) for reader in readers for side in texts]
        )
        for position, reader in enumerate(readers):
            parsed = []
            for blocks in parses[2 * position : 2 * position + 2]:
                pieces: list[list[JSONValue]] = [[]]
                for block in blocks:
                    if [block] == separators[reader]:
                        pieces.append([])
                    else:
                        pieces[-1].append(block)
                if len(pieces) != len(pairs):
                    raise _Unaligned
                parsed.append(pieces)
            result = [ok and a == b for ok, a, b in zip(result, *parsed, strict=True)]
        return result[:checked]

    try:
        separators = dict(
            zip(
                readers,
                _pandoc.run_all([partial(parse, _ESCAPE_SEPARATOR, reader) for reader in readers]),
                strict=True,
            )
        )
        spans = [
            span for span in _escape_blocks(lines) if any(index in groups for index in range(*span))
        ]
        accepted: dict[int, list[EscapeGroup]] = {}
        failing: list[tuple[int, int]] = []
        verdicts = same([(text(range(*span), {}), text(range(*span), groups)) for span in spans])
        for span, ok in zip(spans, verdicts, strict=True):
            if ok:
                accepted.update((index, groups[index]) for index in range(*span) if index in groups)
            else:
                failing.append(span)
        keys: list[tuple[int, EscapeGroup]] = []
        pairs: list[tuple[str, str]] = []
        for span in failing:
            rows = _table_rows(lines, span)
            for index in range(*span):
                scope = rows.get(index, range(*span))
                for group in groups.get(index, ()):
                    keys.append((index, group))
                    pairs.append((text(scope, {}), text(scope, {index: [group]})))
        passed: dict[int, list[EscapeGroup]] = {}
        for (index, group), ok in zip(keys, same(pairs), strict=True):
            if ok:
                passed.setdefault(index, []).append(group)
        retry = [span for span in failing if any(index in passed for index in range(*span))]
        together = same([(text(range(*span), {}), text(range(*span), passed)) for span in retry])
        for span, ok in zip(retry, together, strict=True):
            if ok:
                accepted.update((index, passed[index]) for index in range(*span) if index in passed)
        result = text(range(len(lines)), accepted)
        # Each block was checked by both readers with the page's headings and definitions;
        # the page as a whole is checked again by docx-to-md's reader, which, unlike
        # md-to-html's, reads a page of large HTML tables quickly.
        unescaped, escaped = _pandoc.run_all(
            [
                partial(parse, result, _reference.DOCX_READER),
                partial(parse, markdown, _reference.DOCX_READER),
            ]
        )
        if unescaped != escaped:
            return markdown, 0
    except (_pandoc.PandocError, _Unaligned):
        return markdown, 0
    return result, sum(len(group) for found in accepted.values() for group in found)


def normalize_final_newline(markdown: str) -> str:
    return markdown.rstrip("\n") + "\n"


def markdown_heading_lines(markdown: str) -> dict[int, tuple[int, str]]:
    lines = markdown.splitlines()
    result: dict[int, tuple[int, str]] = {}
    for index in sorted(_syntax.markdown_prose_lines(markdown)):
        # As in CommonMark, "##" alone is an empty heading.
        match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$", lines[index])
        if match:
            result[index] = len(match.group(1)), match.group(2) or ""
    return result


def heading_reference_key(value: str) -> str:
    visible = html.unescape(re.sub(r"<[^>]+>", "", value))
    visible = re.sub(r"!?\[((?:\\.|[^\]\\])*)\]\([^)]*\)", r"\1", visible)
    for pattern in (
        r"(?<!\\)(\*\*|__|~~|`+)(.+?)(?<!\\)\1",
        r"(?<!\\)([*_])(.+?)(?<!\\)\1",
    ):
        visible = re.sub(pattern, r"\2", visible)
    visible = re.sub(r"\\([\\`*_\[\]{}()#+.!|>~-])", r"\1", visible)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", visible)).strip().casefold()
