"""Qualify headings, and lead links to the final headings and Word bookmarks to their sections."""

from __future__ import annotations

import difflib
import html
import os
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from functools import partial
from pathlib import Path

from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import transforms as _transforms
from md_doc_converter.converters.docx import tree as _tree
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    BookmarkSection,
    ConversionError,
    DocumentNavigation,
    HeadingChange,
    SourceHeading,
)
from md_doc_converter.converters.docx.tree import MathSyntax
from md_doc_converter.markdown.headings import Heading, HeadingError, collect_headings
from md_doc_converter.pandoc import JSONValue, PandocError, ParsedMarkdown, run_all


def qualify_duplicate_headings(markdown: str) -> tuple[str, list[HeadingChange]]:
    """Qualify titles only; source levels, punctuation, and anchor IDs are not edited."""
    lines = markdown.splitlines()
    headings = _normalization.markdown_heading_lines(markdown)
    order = list(headings)
    # A heading that holds every other heading, such as the title, tells none of them apart.
    top = (
        order[0]
        if order and all(headings[index][0] > headings[order[0]][0] for index in order[1:])
        else None
    )
    ancestry: dict[int, tuple[str, ...]] = {}
    stack: list[int] = []
    groups: dict[str, list[int]] = defaultdict(list)
    for index, (level, title) in headings.items():
        while stack and headings[stack[-1]][0] >= level:
            stack.pop()
        # An empty heading still ends the sections above it, but has no title to qualify
        # or to add to the titles below it.
        ancestry[index] = tuple(
            headings[parent][1]
            for parent in stack
            if parent != top and _normalization.heading_reference_key(headings[parent][1])
        )
        stack.append(index)
        if _normalization.heading_reference_key(title):
            groups[_normalization.heading_reference_key(title)].append(index)
    occupied = {key for key, indexes in groups.items() if len(indexes) == 1}
    changes: list[HeadingChange] = []
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        candidates: dict[int, str] = {}
        for depth in range(1, max(len(ancestry[index]) for index in indexes) + 1):
            candidates = {
                index: " - ".join((*ancestry[index][-depth:], headings[index][1]))
                for index in indexes
            }
            keys = {_normalization.heading_reference_key(value) for value in candidates.values()}
            if len(keys) == len(indexes) and not keys & occupied:
                break
        else:
            candidates = {}
            ordinal = 1
            for index in indexes:
                prefix = " - ".join((*ancestry[index], headings[index][1]))
                title = f"{prefix} - occurrence {ordinal}"
                while _normalization.heading_reference_key(title) in occupied:
                    ordinal += 1
                    title = f"{prefix} - occurrence {ordinal}"
                candidates[index] = title
                occupied.add(_normalization.heading_reference_key(title))
                ordinal += 1
        for index, title in candidates.items():
            level, original = headings[index]
            lines[index] = "#" * level + " " + title
            occupied.add(_normalization.heading_reference_key(title))
            changes.append(HeadingChange(index + 1, original, title, ancestry[index]))
    ending = "\n" if markdown.endswith("\n") else ""
    return "\n".join(lines) + ending, changes


def _rewrite_ast(value: JSONValue, rewrite: Callable[[str], str]) -> None:
    if isinstance(value, list):
        for item in value:
            _rewrite_ast(item, rewrite)
    elif isinstance(value, dict):
        kind = value.get("t")
        if kind in {"Link", "Image"}:
            content = _reference.json_list(value.get("c"))
            if len(content) != 3:
                raise ConversionError("Unexpected Pandoc link structure")
            target = _reference.json_list(content[2])
            if len(target) != 2:
                raise ConversionError("Unexpected Pandoc link destination structure")
            # Pandoc's GFM writer does not escape entity-like ampersands in URLs.
            target[0] = html.escape(rewrite(_reference.json_string(target[0])), quote=False)
        elif kind in {"RawInline", "RawBlock"}:
            content = _reference.json_list(value.get("c"))
            if len(content) == 2 and content[0] == "html":
                content[1] = _reference.rewrite_html_targets(
                    _reference.json_string(content[1]), rewrite
                )
        for item in value.values():
            _rewrite_ast(item, rewrite)


# A web or e-mail address in Word text that is not a link, which markdownlint's MD034
# reports; trailing punctuation stays outside, as in GFM's extended autolinks.
_BARE_ADDRESS = re.compile(r"(?<![\w./:@%+-])" + _reference.ADDRESS, re.IGNORECASE)
_LINK_CONTEXTS = frozenset({"Link", "Image", "Code", "CodeBlock", "RawInline", "RawBlock", "Math"})


def _address_without_trailing_punctuation(address: str) -> str:
    """*address* without the punctuation GFM leaves outside an extended autolink."""
    while address:
        last = address[-1]
        if (
            last in ".,:;!?*_~'\""
            or (last == ")" and address.count(")") > address.count("("))
            or (last == "]" and address.count("]") > address.count("["))
        ):
            address = address[:-1]
        else:
            break
    return address


def _linked_addresses(text: str) -> list[JSONValue] | None:
    """*text* as Str and autolink Link inlines, or None without a bare address in it."""
    pieces: list[JSONValue] = []
    position = 0
    for match in _BARE_ADDRESS.finditer(text):
        url = match.group("url") is not None
        address = _address_without_trailing_punctuation(match.group(0))
        if url and not re.fullmatch(r"[A-Za-z]+://\S+", address):
            continue
        start, end = match.start(), match.start() + len(address)
        # Angle brackets typed around an address in Word are what an autolink writes.
        if start > position and text[start - 1] == "<" and text[end : end + 1] == ">":
            start, end = start - 1, end + 1
        if start > position:
            pieces.append({"t": "Str", "c": text[position:start]})
        pieces.append(
            {
                "t": "Link",
                "c": [
                    ["", ["uri" if url else "email"], []],
                    [{"t": "Str", "c": address}],
                    [address if url else "mailto:" + address, ""],
                ],
            }
        )
        position = end
    if not pieces:
        return None
    if position < len(text):
        pieces.append({"t": "Str", "c": text[position:]})
    return pieces


def _link_bare_addresses(value: JSONValue) -> int:
    """Make each bare web or e-mail address in prose an autolink; return how many."""
    linked = 0
    if isinstance(value, list):
        index = 0
        while index < len(value):
            item = value[index]
            if isinstance(item, dict) and item.get("t") == "Str":
                pieces = _linked_addresses(_reference.json_string(item.get("c")))
                if pieces is not None:
                    value[index : index + 1] = pieces
                    linked += sum(
                        isinstance(piece, dict) and piece.get("t") == "Link" for piece in pieces
                    )
                    index += len(pieces)
                    continue
            linked += _link_bare_addresses(item)
            index += 1
    elif isinstance(value, dict) and value.get("t") not in _LINK_CONTEXTS:
        for item in value.values():
            linked += _link_bare_addresses(item)
    return linked


def rewrite_references(
    markdown: str,
    output: Path,
    anchors: dict[str, str],
    pandoc: str,
    *,
    parsed: ParsedMarkdown | None = None,
    unresolved: list[str] | None = None,
    linked: list[int] | None = None,
    math: MathSyntax = "github",
) -> str:
    """Retarget the links of *markdown*, written to *output*: a fragment of the document's own
    to the one that *anchors* gives for it, and a local file relative to *output*'s folder.
    With *unresolved*, problem targets are kept and listed.

    Bare web and e-mail addresses become autolinks; *linked*, if given, gets their number.
    Equations are written in the *math* syntax.
    """

    def rewrite(target: str) -> str:
        try:
            parsed = _reference.split_uri(target)
        except ConversionError as error:
            if unresolved is None:
                raise
            unresolved.append(str(error))
            return target
        if parsed.scheme or parsed.netloc:
            return target
        fragment = urllib.parse.unquote(parsed.fragment)
        original_path = (
            output
            if not parsed.path
            else (output.parent / urllib.parse.unquote(parsed.path)).resolve()
        )
        if original_path == output and fragment:
            if fragment not in anchors:
                message = f"No heading or Word bookmark for fragment {'#' + fragment!r}"
                if unresolved is None:
                    raise ConversionError(message)
                unresolved.append(message)
                return target
            relative = ""
            fragment = urllib.parse.quote(anchors[fragment], safe="")
        elif original_path == output and not parsed.path:
            relative = ""
        elif original_path.exists():
            relative = Path(os.path.relpath(original_path, output.parent)).as_posix()
            fragment = parsed.fragment
        else:
            # A missing file keeps its target as written: resolved, it would depend on this
            # machine's folders, and decoded, an escaped character such as %2F would change it.
            return target
        return (
            urllib.parse.urlunsplit(
                (
                    "",
                    "",
                    urllib.parse.quote(relative, safe="/"),
                    parsed.query,
                    fragment,
                )
            )
            or "#"
        )

    if parsed is None:
        parsed = _reference.markdown_ast(markdown, pandoc, "Reference parsing")
    try:
        data = parsed.copy_ast(markdown, _reference.DOCX_READER)
    except PandocError as error:
        raise ConversionError(str(error)) from error
    _rewrite_ast(data, rewrite)
    count = _link_bare_addresses(data)
    if linked is not None:
        linked.append(count)
    if isinstance(data, dict):
        # The Markdown is written by Pandoc again, which needs the same help as the first time.
        blocks = _reference.json_list(data.get("blocks"))
        _transforms.escape_rules_and_underlines(blocks)
        _transforms.short_rules(blocks)
    result = _reference.run_command(
        [
            pandoc,
            "--from=json",
            f"--to={_tree.gfm_writer(math)}+raw_html",
            "--wrap=none",
            "--markdown-headings=atx",
        ],
        "Reference rendering",
        input_text=_reference.gfm_input(data),
    )
    spaced, spacing = _normalization.normalize_ordered_list_spacing(result)
    if spacing:

        def structure(text: str) -> JSONValue:
            return _reference.markdown_ast(text, pandoc, "List marker spacing validation").ast

        spaced_structure, structure_before = run_all(
            [partial(structure, spaced), partial(structure, result)]
        )
        result = spaced if spaced_structure == structure_before else result
    result = _normalization.trim_trailing_whitespace(result)[0]
    result = _normalization.normalize_final_newline(_normalization.compact_markdown_tables(result))
    return _normalization.simplify_identifier_escapes(result, pandoc)


def _heading_mapping(body: list[SourceHeading], rendered: list[Heading]) -> dict[str, int]:
    """Match Word body headings to converted headings, by position where the outlines agree."""
    matcher = difflib.SequenceMatcher(
        None,
        [(heading.level, _reference.plain_text(heading.title).casefold()) for heading in body],
        [(heading.level, _reference.plain_text(heading.text).casefold()) for heading in rendered],
        autojunk=False,
    )
    mapping: dict[str, int] = {}
    first = second = 0
    for block in matcher.get_matching_blocks():
        # Unmatched runs of equal length and levels are the same headings with edited text.
        gap = list(zip(range(first, block.a), range(second, block.b), strict=False))
        if block.a - first == block.b - second and all(
            body[left].level == rendered[right].level for left, right in gap
        ):
            mapping.update((body[left].key, right) for left, right in gap)
        mapping.update(
            (body[block.a + offset].key, block.b + offset) for offset in range(block.size)
        )
        first, second = block.a + block.size, block.b + block.size
    return mapping


def _map_bookmarks(
    source: DocumentNavigation,
    rendered: list[Heading],
    anchors: dict[str, str],
    output: Path,
    report: TransformReport | None,
) -> None:
    """Lead each Word bookmark to the ID of the *rendered* heading of its section, or to the
    document top, in *source* and *anchors*."""
    body = [heading for heading in source.headings if not heading.in_table]
    mapping = _heading_mapping(body, rendered)
    order = {heading.key: index for index, heading in enumerate(body)}
    used_bookmarks: set[str] = set()
    for link in source.link_targets:
        try:
            parsed = _reference.split_uri(link)
        except ConversionError:
            continue
        if (
            not parsed.scheme
            and not parsed.netloc
            and (
                not parsed.path
                or (output.parent / urllib.parse.unquote(parsed.path)).resolve() == output
            )
        ):
            used_bookmarks.add(urllib.parse.unquote(parsed.fragment))
    mapped = 0
    for name, section in list(source.bookmark_sections.items()):
        lost = section.heading is not None and section.heading not in mapping
        if lost:
            if report is None:
                raise ConversionError(f"Word bookmark {name!r} lost its containing section")
            position = order.get(section.heading or "", 0)
            section = BookmarkSection(
                next(
                    (
                        body[index].key
                        for index in range(position - 1, -1, -1)
                        if body[index].key in mapping
                    ),
                    None,
                ),
                False,
            )
            source.bookmark_sections[name] = section
        elif name in source.bookmarks:
            mapped += 1
        target = "" if section.heading is None else rendered[mapping[section.heading]].identifier
        source.bookmark_targets[name] = target
        anchors[name] = target
        if name not in used_bookmarks or section.exact:
            continue
        destination = f"its containing section #{target}" if target else "the document top"
        message = f"Word bookmark {name!r} now links to {destination}, not its exact position"
        if lost and report is not None:
            report.skipped(
                "bookmarks",
                "the Word heading of a linked bookmark was not converted; "
                "links go to the nearest earlier section",
                message,
                section="markdown",
            )
        elif report is not None:
            report.warn(message, kind=_SECTION_LINK, section="markdown")
        else:
            _diagnostics.warning(message, [], kind=_SECTION_LINK)
    if report is not None:
        report.applied("bookmarks", count=mapped)


# A linked bookmark inside a section, which Markdown can link only as a whole.
_SECTION_LINK = "Word bookmark now links to its containing section, not its exact position"


def resolve_links(
    markdown: str,
    output: Path,
    pandoc: str,
    *,
    source: DocumentNavigation | None = None,
    report: TransformReport | None = None,
    math: MathSyntax = "github",
) -> str:
    """Lead the links of *markdown*, written to *output*, to the final heading IDs, and Word
    bookmarks to their sections.

    Without a report, every problem is an error.
    """
    markdown = _normalization.normalize_final_newline(markdown)
    parsed = _reference.markdown_ast(markdown, pandoc, "Section heading and reference parsing")
    try:
        headings = collect_headings(parsed.blocks)
    except HeadingError as error:
        raise ConversionError(f"Cannot derive section heading identities: {error}") from error
    anchors = {heading.identifier: heading.identifier for heading in headings if heading.identifier}
    if source is not None:
        _map_bookmarks(source, headings, anchors, output, report)
    problems: list[str] | None = [] if report is not None else None
    linked: list[int] = []
    result = rewrite_references(
        markdown,
        output,
        anchors,
        pandoc,
        parsed=parsed,
        unresolved=problems,
        linked=linked,
        math=math,
    )
    if report is None:
        return result
    for problem in problems or []:
        report.skipped(
            "links",
            _diagnostics.category(problem) + "; kept as written",
            problem + "; kept as written",
        )
    report.applied("links", "bare web or e-mail address(es) linked", sum(linked))
    return result
