"""Check the pages before they are published: anchors, headings, images and links."""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    DocumentNavigation,
    DocumentReferences,
)

Issue = tuple[str, str]
"""A generic reason for the transform summary, and the finding that names the item."""


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


def validate_outputs(
    pages: dict[Path, str],
    paths: ConversionPaths,
    staged_images: Path,
    pandoc: str,
    source: DocumentNavigation | None = None,
) -> list[Issue]:
    """Return every problem found in *pages*; *source* gives the Word bookmarks to check."""
    issues: list[Issue] = []

    def issue(reason: str, detail: str) -> None:
        issues.append((reason, detail))

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
    if source is not None:
        for page, content in pages.items():
            if source.bookmarks & _reference.declared_html_ids(content, pandoc):
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
                continue
            destination, fragment = local
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
                )
    return issues
