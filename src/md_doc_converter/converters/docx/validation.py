"""Check published-page content and references against the source document."""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import explain as _explain
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    DocumentReferences,
    SourceDocument,
    SourceTable,
)

Issue = tuple[str, str]
"""A generic reason for the transform summary, and the finding that names the item."""


SOURCE_CHECKS = frozenset({"bookmarks", "tables", "images", "links"})


def collect_references(root: ET.Element) -> DocumentReferences:
    references = DocumentReferences()

    def visit(node: ET.Element, *, in_table: bool = False) -> None:
        if node.tag in {"pre", "code", "script", "style", "textarea"}:
            return
        anchor = node.get("id") or (node.get("name") if node.tag == "a" else None)
        if anchor:
            references.anchors.append(anchor)
        if node.tag == "a" and node.get("href") is not None:
            references.links.append(node.get("href", ""))
        if node.tag in {"img", "audio", "video", "source", "embed"}:
            if node.get("src") is None:
                references.sourceless.append(str(node.tag))
            else:
                references.images.append(node.get("src", ""))
        if node.tag == "object":
            references.images.append(node.get("data", ""))
        if node.get("poster"):
            references.images.append(node.get("poster", ""))
        if not in_table and isinstance(node.tag, str) and re.fullmatch(r"h[1-6]", node.tag):
            references.headings.append(int(node.tag[1]))
        if node.tag == "table":
            references.tables.append(node)
        for child in node:
            visit(child, in_table=in_table or node.tag == "table")

    visit(root)
    return references


def _local_target(target: str, page: Path, paths: ConversionPaths) -> tuple[Path, str] | None:
    parsed = _reference.split_uri(target)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return None
    if parsed.netloc:
        if parsed.scheme.casefold() == "file":
            raise ConversionError(f"Cannot validate remote file link: {target}")
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if parsed.scheme.casefold() == "file" and not Path(decoded).is_absolute():
        raise ConversionError(f"File URL is not absolute: {target}")
    destination = (
        paths.output.parent / page if not decoded else paths.output.parent / page.parent / decoded
    ).resolve()
    return destination, urllib.parse.unquote(parsed.fragment)


def _svg_ids(path: Path) -> set[str]:
    return {node.get("id", "") for node in ET.parse(path).getroot().iter()}


def _file_anchors(destination: Path, target: str, pandoc: str) -> set[str]:
    suffix = destination.suffix.casefold()
    if suffix == ".md":
        parsed = _reference.pandoc_html(destination.read_text(encoding="utf-8"), pandoc)
    elif suffix in {".html", ".htm"}:
        parsed = _reference.parse_html(destination.read_text(encoding="utf-8"))
    else:
        raise ConversionError(f"Cannot validate retained file fragment: {target}")
    return set(collect_references(parsed).anchors)


def _table_problem(
    descriptor: SourceTable, rendered: ET.Element, source: SourceDocument
) -> str | None:
    if (
        not descriptor.nested
        and descriptor.token not in source.markdown_tables
        and _tables.render_source_table(rendered).markdown
    ):
        return "uses HTML without a structural limitation"
    expected_rows = _tables.word_table_rows(descriptor.source)
    actual_rows = _tables.html_rows(rendered)
    listed = descriptor.listed if descriptor.token in source.markdown_tables else frozenset()
    if len(expected_rows) != len(actual_rows):
        return "lost or invented a Word row"
    for row_index, (expected_row, actual_row) in enumerate(
        zip(expected_rows, actual_rows, strict=True)
    ):
        header = (
            row_index == 0 if descriptor.token in source.markdown_tables else expected_row.header
        )
        cells = [node for node in actual_row if node.tag in {"th", "td"}]
        if len(expected_row.cells) != len(cells):
            return "lost or invented a Word cell"
        for expected_cell, actual_cell in zip(expected_row.cells, cells, strict=True):
            if (
                actual_cell.get("colspan", "1") != str(expected_cell.colspan)
                or actual_cell.get("rowspan", "1") != str(expected_cell.rowspan)
                or actual_cell.tag != ("th" if header else "td")
            ):
                return "changed Word header/merge structure"
            actual = _reference.plain_text(_reference.visible_reference_text(actual_cell))
            if not _cell_text_matches(expected_cell.source, listed, actual):
                return "changed Word cell content"
    return None


# A Markdown table writes each item of a list in a cell as a line that starts with a bullet or
# its number, which Word's text does not have; see tables._cell_lines.
_CELL_LIST_MARKER = r"(?:\u2022|\d+\.)?"


def _cell_text_matches(cell: ET.Element | None, listed: frozenset[ET.Element], actual: str) -> bool:
    """Whether *actual*, without whitespace, is the text of the Word *cell*, where each list
    item in *listed* may start with its marker."""
    segments = [""]

    def visit(node: ET.Element) -> None:
        if node in listed:
            segments.append("")
        if node.tag == _ooxml.WORD + "t":
            segments[-1] += node.text or ""
        for child in node:
            visit(child)

    if cell is not None:
        visit(cell)
    pattern = _CELL_LIST_MARKER.join(
        _cell_pattern(_reference.plain_text(text)) for text in segments
    )
    return re.fullmatch(pattern, actual) is not None


# Angle brackets typed around a web or e-mail address, which its autolink replaces.
_BRACKETED_ADDRESS = re.compile("<(" + _reference.ADDRESS + ")>", re.IGNORECASE)


def _cell_pattern(text: str) -> str:
    """A pattern for the Word cell *text*, in which the angle brackets typed around an
    address may be gone: the address became an autolink, which shows without them."""
    parts: list[str] = []
    position = 0
    for match in _BRACKETED_ADDRESS.finditer(text):
        parts.extend(
            (re.escape(text[position : match.start()]), f"<?{re.escape(match.group(1))}>?")
        )
        position = match.end()
    parts.append(re.escape(text[position:]))
    return "".join(parts)


def validate_outputs(
    pages: dict[Path, str],
    paths: ConversionPaths,
    staged_images: Path,
    pandoc: str,
    source: SourceDocument | None = None,
    *,
    compare: frozenset[str] = SOURCE_CHECKS,
    shapes: list[dict[str, str]] | None = None,
) -> list[Issue]:
    """Return every problem found; *compare* names the meaningful source comparisons.

    *shapes*, if given, receives one dict per issue for --explain: content-free features of
    the item, or an empty dict.
    """
    issues: list[Issue] = []
    word_targets = set(source.link_targets) if source is not None else set[str]()

    def issue(reason: str, detail: str, shape: dict[str, str] | None = None) -> None:
        issues.append((reason, detail))
        if shapes is not None:
            shapes.append(shape or {})

    references = {
        (paths.output.parent / page).resolve(): collect_references(
            _reference.pandoc_html(content, pandoc)
        )
        for page, content in pages.items()
    }
    for page in pages:
        information = references[(paths.output.parent / page).resolve()]
        duplicates = [anchor for anchor, count in Counter(information.anchors).items() if count > 1]
        if duplicates:
            issue(
                "duplicate anchors on a page",
                f"Duplicate anchors in {page}: {', '.join(duplicates)}",
            )
        if paths.split_depth and len(pages) > 1 and information.headings.count(1) != 1:
            issue(
                "a split page does not contain exactly one H1",
                f"Split page {page} does not contain exactly one H1",
            )
        for tag in information.sourceless:
            issue("retained media has no source", f"Retained <{tag}> has no source in {page}")
    if source is not None and "bookmarks" in compare:
        word_names = set(source.anchors.values())
        for page, content in pages.items():
            if word_names & _reference.declared_html_ids(content, pandoc):
                issue(
                    "Word bookmark markup remains",
                    f"Explicit Word bookmark markup remains in {page}",
                )
        for name, target in source.bookmark_targets.items():
            absolute = (paths.output.parent / target.page).resolve()
            if absolute not in references or (
                target.fragment and target.fragment not in references[absolute].anchors
            ):
                issue(
                    "a Word bookmark has no valid section destination",
                    f"Word bookmark {name!r} has no valid section destination",
                )
    actual_images: Counter[Path] = Counter()
    actual_links: Counter[tuple[str, str]] = Counter()
    external_cache: dict[Path, set[str]] = {}
    for page in pages:
        info = references[(paths.output.parent / page).resolve()]
        for target in info.images:
            try:
                local = _local_target(target, page, paths)
            except ConversionError as error:
                issue("an image source cannot be checked", f"Image in {page}: {error}")
                continue
            if local is None:
                issue(
                    "an image is remote or was not recovered",
                    f"Unrecovered/remote image in {page}: {target}",
                )
                continue
            destination, fragment = local
            if not destination.is_relative_to(paths.images):
                issue(
                    "an image is outside the image directory",
                    f"Image outside the images folder in {page}: {target}",
                )
                continue
            staged = staged_images / destination.relative_to(paths.images)
            if not staged.is_file() or staged.is_symlink() or not staged.stat().st_size:
                issue("an image file is missing", f"Missing retained image in {page}: {target}")
                continue
            actual_images[destination] += 1
            if not fragment:
                continue
            try:
                found = staged.suffix.casefold() == ".svg" and fragment in _svg_ids(staged)
            except ET.ParseError:
                found = False
            if not found:
                issue(
                    "an image fragment does not resolve",
                    f"Missing SVG fragment in {page}: {target}",
                )
        for target in info.links:
            try:
                local = _local_target(target, page, paths)
            except ConversionError as error:
                issue("a link target cannot be checked", f"Link in {page}: {error}")
                continue
            if local is None:
                actual_links[_reference.encoded_uri(target), ""] += 1
                continue
            destination, fragment = local
            actual_links[str(destination), fragment] += 1
            anchors: set[str]
            if destination in references:
                anchors = set(references[destination].anchors)
            elif paths.split_depth and destination.is_relative_to(paths.pages):
                issue(
                    "a link into the page tree has no prepared page",
                    f"Link into the replaced page tree has no prepared page in {page}: {target}",
                )
                continue
            elif destination.is_relative_to(paths.images):
                staged = staged_images / destination.relative_to(paths.images)
                if not staged.is_file():
                    issue(
                        "a linked image file is missing",
                        f"Missing linked asset in {page}: {target}",
                    )
                    continue
                if not fragment:
                    continue
                try:
                    anchors = _svg_ids(staged) if staged.suffix.casefold() == ".svg" else set()
                except ET.ParseError:
                    anchors = set()
            else:
                if not destination.is_file():
                    issue(
                        "a local link target does not exist",
                        f"Broken retained local link in {page}: {target}",
                        None
                        if shapes is None
                        else _explain.local_link_shape(target, destination, target in word_targets),
                    )
                    continue
                if not fragment:
                    continue
                if destination not in external_cache:
                    try:
                        external_cache[destination] = _file_anchors(destination, target, pandoc)
                    except (ConversionError, OSError, UnicodeError, ET.ParseError) as error:
                        issue(
                            "a link fragment into a local file cannot be checked",
                            f"Link in {page}: {error}",
                        )
                        continue
                anchors = external_cache[destination]
            if fragment and fragment not in anchors:
                issue(
                    "a link fragment does not resolve",
                    f"Unresolved retained fragment in {page}: {target}",
                    _explain.fragment_shape(
                        fragment, destination == (paths.output.parent / page).resolve()
                    ),
                )
    if source is None:
        return issues
    if "tables" in compare:
        final_tables = [table for info in references.values() for table in info.tables]
        if len(final_tables) != len(source.tables):
            issue(
                "the table count differs from the Word document",
                f"Final table count {len(final_tables)} does not match the Word source "
                f"({len(source.tables)})",
            )
        else:
            for index, (descriptor, rendered) in enumerate(
                zip(source.tables, final_tables, strict=True), 1
            ):
                problem = _table_problem(descriptor, rendered, source)
                if problem is not None:
                    issue(f"a final table {problem}", f"Final table {index} {problem}")
    if "images" in compare:
        expected_images = Counter(
            paths.images.joinpath(*image.published_name.parts) for image in source.images
        )
        if actual_images != expected_images:
            issue(
                "image occurrences differ from the Word document",
                "Final image occurrences do not match the Word source",
            )
    if "links" in compare:
        expected_links: Counter[tuple[str, str]] = Counter()
        for target in source.link_targets:
            try:
                local = _local_target(target, Path(paths.output.name), paths)
            except ConversionError:
                continue
            if local is None:
                expected_links[_reference.encoded_uri(target), ""] += 1
                continue
            destination, fragment = local
            if destination == paths.output and fragment in source.bookmark_targets:
                section = source.bookmark_targets[fragment]
                destination = (paths.output.parent / section.page).resolve()
                fragment = section.fragment
            expected_links[str(destination), fragment] += 1
        missing = sum((expected_links - actual_links).values())
        if missing:
            issue(
                "a recovered Word hyperlink is missing from the output",
                f"Final links do not preserve every recovered Word hyperlink ({missing} missing)",
            )
    return issues
