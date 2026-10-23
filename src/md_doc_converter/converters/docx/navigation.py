"""Qualify headings, split Wiki pages, and preserve section destinations."""

from __future__ import annotations

import difflib
import html
import os
import re
import unicodedata
import urllib.parse
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from md_doc_converter.converters.docx import diagnostics as _diagnostics
from md_doc_converter.converters.docx import explain as _explain
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.diagnostics import TransformReport
from md_doc_converter.converters.docx.models import (
    BookmarkSection,
    ConversionError,
    HeadingChange,
    SectionTarget,
    SourceDocument,
    SourceHeading,
    WikiPage,
)
from md_doc_converter.markdown import syntax as _syntax
from md_doc_converter.markdown.headings import Heading, HeadingError, collect_headings
from md_doc_converter.pandoc import JSONValue, PandocError, ParsedMarkdown


def qualify_duplicate_headings(markdown: str) -> tuple[str, list[HeadingChange]]:
    """Qualify titles only; source levels, punctuation, and anchor IDs are not edited."""
    lines = markdown.splitlines()
    headings = _normalization.markdown_heading_lines(markdown)
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
            if _normalization.heading_reference_key(headings[parent][1])
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


def wiki_page_slug(title: str) -> str:
    visible = html.unescape(re.sub(r"<[^>]+>", "", title))
    normalized = unicodedata.normalize("NFKD", visible).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-") or "page"


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


def rewrite_page_references(
    markdown: str,
    page: Path,
    output: Path,
    anchors: dict[str, SectionTarget],
    pandoc: str,
    *,
    parsed: ParsedMarkdown | None = None,
    unresolved: list[str] | None = None,
    linked: list[int] | None = None,
) -> str:
    """Retarget links for *page*; with *unresolved*, problem targets are kept and listed.

    Bare web and e-mail addresses become autolinks; *linked*, if given, gets their number.
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
                message = f"No unique split destination for fragment {'#' + fragment!r}"
                if unresolved is None:
                    raise ConversionError(message)
                unresolved.append(message)
                return target
            destination = anchors[fragment]
            target_page = destination.page
            fragment = urllib.parse.quote(destination.fragment, safe="")
            relative = (
                ""
                if target_page == page
                else Path(os.path.relpath(target_page, page.parent)).as_posix()
            )
        elif original_path == output and not parsed.path:
            relative = (
                ""
                if page == Path(output.name)
                else Path(os.path.relpath(Path(output.name), page.parent)).as_posix()
            )
            fragment = ""
        else:
            relative = Path(
                os.path.relpath(original_path, (output.parent / page).parent)
            ).as_posix()
            fragment = parsed.fragment
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
        parsed = _reference.markdown_ast(markdown, pandoc, "Split-page reference parsing")
    try:
        data = parsed.copy_ast(markdown, _reference.DOCX_READER)
    except PandocError as error:
        raise ConversionError(str(error)) from error
    _rewrite_ast(data, rewrite)
    count = _link_bare_addresses(data)
    if linked is not None:
        linked.append(count)
    result = _reference.run_command(
        [
            pandoc,
            "--from=json",
            "--to=gfm+raw_html",
            "--wrap=none",
            "--markdown-headings=atx",
        ],
        "Split-page reference rendering",
        input_text=_reference.gfm_input(data),
    )
    result = _normalization.normalize_unordered_lists(result)[0]
    spaced, spacing = _normalization.normalize_ordered_list_spacing(result)
    if spacing:

        def structure(text: str) -> JSONValue:
            return _reference.markdown_ast(text, pandoc, "List marker spacing validation").ast

        result = spaced if structure(spaced) == structure(result) else result
    result = _normalization.trim_trailing_whitespace(result)[0]
    result = _normalization.normalize_final_newline(_normalization.compact_markdown_tables(result))
    return _normalization.simplify_identifier_escapes(result, pandoc)


def _split_sections(
    markdown: str,
    output: Path,
    split_depth: int,
    title: str | None,
    headings: dict[int, tuple[int, str]],
) -> tuple[dict[Path, str], dict[Path, list[int | None]]]:
    ordinals = {line: index for index, line in enumerate(headings)}
    if split_depth == 0:
        return (
            {Path(output.name): _normalization.normalize_final_newline(markdown)},
            {Path(output.name): list(range(len(headings)))},
        )
    lines = markdown.splitlines()
    selected = [
        (index, level, heading)
        for index, (level, heading) in headings.items()
        if level <= split_depth
    ]
    root_title = _media.escape_image_alt_text(title or output.stem)
    trailing_hashes = re.search(r"(?<!\\)(#+)$", root_title)
    if trailing_hashes is not None:
        root_title = root_title[: trailing_hashes.start()] + trailing_hashes[0].replace("#", r"\#")
    pages = [WikiPage(root_title, 1, None, Path(output.name), heading_indices=[None])]
    stack: list[int] = []
    boundaries: dict[int, int] = {}
    children: dict[int, list[int]] = defaultdict(list)
    selected_lines = {index for index, _level, _heading in selected}
    for line_index, level, heading in selected:
        while stack and pages[stack[-1]].source_level >= level:
            stack.pop()
        parent = stack[-1] if stack else 0
        page_index = len(pages)
        pages.append(WikiPage(heading, level, parent, Path()))
        children[parent].append(page_index)
        stack.append(page_index)
        boundary = line_index
        while (
            boundary > 0 and boundary - 1 not in selected_lines and not lines[boundary - 1].strip()
        ):
            boundary -= 1
        if boundary in boundaries:
            raise ConversionError("Page sections overlap")
        boundaries[boundary] = page_index

    def assign_paths(parent: int) -> None:
        siblings = children.get(parent, [])
        width = max(2, len(str(len(siblings))))
        directory = (
            Path(output.stem) if parent == 0 else pages[parent].relative_path.with_suffix("")
        )
        for ordinal, child in enumerate(siblings, 1):
            pages[child].relative_path = directory / (
                f"{ordinal:0{width}d}-{wiki_page_slug(pages[child].title)}.md"
            )
            assign_paths(child)

    assign_paths(0)
    current = 0
    for index, line in enumerate(lines):
        if index in boundaries:
            current = boundaries[index]
        if index in headings:
            pages[current].heading_indices.append(ordinals[index])
            if current:
                level, heading = headings[index]
                shifted = level - pages[current].source_level + 1
                if not 1 <= shifted <= 6:
                    raise ConversionError(
                        "A heading would lose its level relative to its page's heading"
                    )
                line = "#" * shifted + " " + heading
        pages[current].lines.append(line)
    pages[0].lines[0:0] = ["# " + pages[0].title, ""]
    raw: dict[Path, str] = {}
    identities: dict[Path, list[int | None]] = {}
    for index, page in enumerate(pages):
        content = "\n".join(page.lines).strip("\n")
        child_indexes = children.get(index, [])
        if child_indexes:
            page.heading_indices.append(None)
            content += "\n\n## Sections\n\n" + "\n".join(
                f"- [{_media.escape_image_alt_text(pages[child].title)}]("
                f"{urllib.parse.quote(pages[child].relative_path.as_posix(), safe='/')})"
                for child in child_indexes
            )
        raw[page.relative_path] = _normalization.normalize_final_newline(content)
        identities[page.relative_path] = page.heading_indices
    return raw, identities


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
            mapping.update((body[left].token, right) for left, right in gap)
        mapping.update(
            (body[block.a + offset].token, block.b + offset) for offset in range(block.size)
        )
        first, second = block.a + block.size, block.b + block.size
    return mapping


def _map_bookmarks(
    source: SourceDocument,
    rendered: list[Heading],
    section_targets: dict[int, SectionTarget],
    anchors: dict[str, SectionTarget],
    output: Path,
    report: TransformReport | None,
) -> None:
    body = [heading for heading in source.headings if not heading.in_table]
    mapping = _heading_mapping(body, rendered)
    order = {heading.token: index for index, heading in enumerate(body)}
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
    top = SectionTarget(Path(output.name))
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
                        body[index].token
                        for index in range(position - 1, -1, -1)
                        if body[index].token in mapping
                    ),
                    None,
                ),
                False,
            )
            source.bookmark_sections[name] = section
        else:
            mapped += 1
        target = top if section.heading is None else section_targets[mapping[section.heading]]
        source.bookmark_targets[name] = target
        anchors[name] = target
        if name not in used_bookmarks or section.exact:
            continue
        destination = (
            f"its containing section #{target.fragment}" if target.fragment else "the document top"
        )
        message = f"Word bookmark {name!r} now links to {destination}, not its exact position"
        if lost and report is not None:
            report.skipped(
                "bookmarks",
                "the Word heading of a linked bookmark was not converted; "
                "links go to the nearest earlier section",
                message,
                shape={"name": _explain.name_class(name)},
            )
        elif report is not None:
            report.warn(message)
        else:
            _diagnostics.warning(message, [])
    if report is not None:
        report.applied("bookmarks", count=mapped)


_TITLE_NOT_FOUND = "the Word title is not a paragraph before the first heading; kept as written"
_TITLE_TOO_DEEP = (
    "a heading is at level 6, the deepest Markdown level, so headings cannot move down; "
    "the title stays a paragraph"
)
_TITLE_CHANGES_IDS = "the title as a heading would change other heading IDs; it stays a paragraph"


def _title_line(lines: list[str], start: int, end: int, title: str, pandoc: str) -> int | None:
    """The first one-line paragraph from *start* to *end* that shows the Word title."""
    prose = _syntax.markdown_prose_lines("\n".join(lines))
    candidates = [
        index
        for index in range(start, min(end, len(lines)))
        if index in prose
        and lines[index].strip()
        and (index == 0 or not lines[index - 1].strip())
        and (index + 1 == len(lines) or not lines[index + 1].strip())
    ]
    if not candidates:
        return None
    # Pandoc's plain text drops the formatting and escapes that Word does not have.
    separator = "MDCONVTITLE" + uuid.uuid4().hex.upper()
    shown = _reference.run_command(
        [pandoc, f"--from={_reference.DOCX_READER}", "--to=plain", "--wrap=none"],
        "Word title search",
        input_text=f"\n\n{separator}\n\n".join(lines[index] for index in candidates),
    )
    texts = shown.split(separator)
    if len(texts) != len(candidates):
        return None
    wanted = _reference.plain_text(title).casefold()
    return next(
        (
            index
            for index, text in zip(candidates, texts, strict=True)
            if _reference.plain_text(text).casefold() == wanted
        ),
        None,
    )


def _title_heading(markdown: str, title: str, pandoc: str, report: TransformReport) -> str:
    """Make the Word title the page's only H1 and move every heading down one level."""
    lines = markdown.split("\n")
    headings = _normalization.markdown_heading_lines(markdown)
    line = _title_line(lines, 0, min(headings, default=len(lines)), title, pandoc)
    if line is None:
        report.skipped("title", _TITLE_NOT_FOUND, f"Word title {title!r}: {_TITLE_NOT_FOUND}")
        return markdown
    if any(level == 6 for level, _text in headings.values()):
        report.skipped("title", _TITLE_TOO_DEEP, f"Word title {title!r}: {_TITLE_TOO_DEEP}")
        return markdown
    shown = lines[line].strip()
    trailing_hashes = re.search(r"(?<!\\)(#+)$", shown)
    if trailing_hashes is not None:
        shown = shown[: trailing_hashes.start()] + trailing_hashes[0].replace("#", r"\#")
    lines[line] = "# " + shown
    for index, (level, text) in headings.items():
        lines[index] = "#" * (level + 1) + (" " + text if text else "")
    result = "\n".join(lines)

    def outline(text: str) -> list[tuple[int, str]]:
        parsed = _reference.markdown_ast(text, pandoc, "Title heading validation")
        return [(item.level, item.identifier) for item in collect_headings(parsed.blocks)]

    before, after = outline(markdown), outline(result)
    moved = [(level + 1, identifier) for level, identifier in before]
    if not after or after[0][0] != 1 or after[1:] != moved:
        report.skipped("title", _TITLE_CHANGES_IDS, f"Word title {title!r}: {_TITLE_CHANGES_IDS}")
        return markdown
    if headings:
        report.applied("title", "heading(s) moved down one level", len(headings))
    else:
        report.applied("title")
    return result


def _without_repeated_title(markdown: str, title: str, pandoc: str, report: TransformReport) -> str:
    """The root page of a split document has the title as its H1; drop its Word paragraph."""
    lines = markdown.split("\n")
    later = [index for index in _normalization.markdown_heading_lines(markdown) if index]
    line = _title_line(lines, 1, min(later, default=len(lines)), title, pandoc)
    if line is None:
        return markdown
    end = line + 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    del lines[line:end]
    report.applied("title", "repeated title paragraph(s) removed")
    return "\n".join(lines)


def split_markdown_pages(
    markdown: str,
    output: Path,
    split_depth: int,
    title: str | None,
    pandoc: str,
    *,
    source: SourceDocument | None = None,
    report: TransformReport | None = None,
) -> dict[Path, str]:
    """Split at *split_depth*; without a report every problem is an error."""
    if not 0 <= split_depth <= 6:
        raise ConversionError(f"Split depth must be between 0 and 6, got {split_depth}")
    parsed_pages: dict[str, ParsedMarkdown] = {}

    def parse_page(content: str) -> ParsedMarkdown:
        if content not in parsed_pages:
            parsed_pages[content] = _reference.markdown_ast(
                content, pandoc, "Section heading and reference parsing"
            )
        return parsed_pages[content]

    def headings_of(content: str) -> list[Heading]:
        try:
            return collect_headings(parse_page(content).blocks)
        except HeadingError as error:
            raise ConversionError(f"Cannot derive section heading identities: {error}") from error

    Pages = tuple[dict[Path, str], dict[int, SectionTarget], list[Heading]]

    def single_page() -> Pages:
        page = Path(output.name)
        content = _normalization.normalize_final_newline(markdown)
        found = headings_of(content)
        targets = {index: SectionTarget(page, item.identifier) for index, item in enumerate(found)}
        return {page: content}, targets, found

    def split_pages() -> Pages:
        line_headings = _normalization.markdown_heading_lines(markdown)
        found = headings_of(markdown)
        if len(found) != len(line_headings) or any(
            heading.level != level
            for heading, (level, _title) in zip(found, line_headings.values(), strict=True)
        ):
            raise ConversionError("Cannot align Markdown heading locations with rendered headings")
        raw, identities = _split_sections(markdown, output, split_depth, title, line_headings)
        targets: dict[int, SectionTarget] = {}
        for page, content in raw.items():
            rendered = headings_of(content)
            if len(rendered) != len(identities[page]):
                raise ConversionError(f"Section heading identities changed while splitting {page}")
            for heading, index in zip(rendered, identities[page], strict=True):
                if index is not None:
                    targets[index] = SectionTarget(page, heading.identifier)
        return raw, targets, found

    split = False
    if not split_depth:
        raw, section_targets, original = single_page()
    else:
        try:
            raw, section_targets, original = split_pages()
            split = True
        except ConversionError as error:
            if report is None:
                raise
            report.skipped(
                "pages",
                "the document could not be split into pages; it is written as one page",
                f"split at depth {split_depth}: {_diagnostics.describe(error)}; "
                "it is written as one page",
            )
            raw, section_targets, original = single_page()
    anchors = {
        heading.identifier: section_targets[index]
        for index, heading in enumerate(original)
        if heading.identifier
    }
    if source is not None:
        _map_bookmarks(source, original, section_targets, anchors, output, report)
    problems: list[str] | None = [] if report is not None else None
    linked: list[int] = []
    pages = {
        path: rewrite_page_references(
            content,
            path,
            output,
            anchors,
            pandoc,
            parsed=parse_page(content),
            unresolved=problems,
            linked=linked,
        )
        for path, content in raw.items()
    }
    if report is not None:
        for problem in problems or []:
            report.skipped(
                "pages",
                _diagnostics.category(problem) + "; kept as written",
                problem + "; kept as written",
            )
        report.applied("pages", count=len(pages))
        report.applied("links", "bare web or e-mail address(es) linked", sum(linked))
        if title:
            root = Path(output.name)
            with _diagnostics.transform(report, "title"):
                if split:
                    pages[root] = _without_repeated_title(pages[root], title, pandoc, report)
                else:
                    pages[root] = _title_heading(pages[root], title, pandoc, report)
    return pages
