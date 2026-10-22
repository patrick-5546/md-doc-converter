"""Layer Word structure onto the MarkItDown baseline; anything unaligned stays as converted."""

from __future__ import annotations

import copy
import dataclasses
import html
import json
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import explain as _explain
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import normalization as _normalization
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx import tables as _tables
from md_doc_converter.converters.docx.diagnostics import (
    FATAL_ERRORS,
    TransformReport,
    marker_pattern,
    transform,
)
from md_doc_converter.converters.docx.media import ImageStager
from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    SourceDocument,
    SourceHeading,
    SourceListItem,
)
from md_doc_converter.pandoc import JSONValue

_KEPT = "; kept as MarkItDown converted it"
_EMPTY_ITEMS = "empty Word list item(s) omitted"
_CAPTIONED = "image(s) described by their caption"
_HEADED = "image(s) described by the heading above"
_LISTED_HEADING = "Word numbers this outline-level heading as a list item; kept as a list item"


class _Mismatch(ConversionError):
    """MarkItDown and Pandoc disagree; *shape* describes how, for --explain, without text."""

    def __init__(self, message: str, shape: dict[str, str]) -> None:
        super().__init__(message)
        self.shape = shape


def _shape(error: BaseException) -> dict[str, str] | None:
    return error.shape if isinstance(error, _Mismatch) else None


def _region_shape(markdown: str, reference: str, token: str, kind: str) -> dict[str, str]:
    """For --explain: each converter's markers for an item, and the links or images between."""
    shape = {
        "MarkItDown markers": _explain.cap(markdown.count(token + "BEGIN")),
        "Pandoc markers": _explain.cap(reference.count(token + "BEGIN")),
    }
    try:
        content, fragment = _region(markdown, token)[2], _region(reference, token)[2]
    except ConversionError:
        return shape
    written, rendered = ("](", "<a ") if kind == "links" else ("![", "<img")
    shape[f"MarkItDown {kind}"] = _explain.cap(content.count(written))
    shape[f"Pandoc {kind}"] = _explain.cap(fragment.count(rendered))
    return shape


_HEADING_CONTAINERS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6"})


def _region(text: str, token: str) -> tuple[int, int, str]:
    start_marker, end_marker = token + "BEGIN", token + "END"
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise ConversionError(f"Missing or ambiguous source/reference alignment for {token}")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return start, end + len(end_marker), text[start + len(start_marker) : end]


def _replace_region(text: str, token: str, replacement: str) -> str:
    start, end, _old = _region(text, token)
    return text[:start] + replacement + text[end:]


def _strip_pair(text: str, token: str) -> str:
    return text.replace(token + "BEGIN", "").replace(token + "END", "")


def _mark_alt_text(markdown: str, token: str) -> str:
    """Label MarkItDown's images of *token* with its alt text marker, for its Word alt text.

    MarkItDown writes a VML picture without alt text: Mammoth does not read its o:title.
    """
    try:
        start, end, fragment = _region(markdown, token)
    except ConversionError:
        return markdown
    marked = _MARKDOWN_IMAGE.sub(
        lambda match: f"![{token}ALT]({match.group(2)}{match.group(3)})", fragment
    )
    return f"{markdown[:start]}{token}BEGIN{marked}{token}END{markdown[end:]}"


def _replace_block_region(text: str, token: str, replacement: str) -> str:
    start, end, _old = _region(text, token)
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    if text[line_start:start].strip("*_~` \t") or text[end:line_end].strip("*_~` \t"):
        raise ConversionError(f"Table boundary overlaps non-table content for {token}")
    return text[:line_start] + replacement.rstrip("\n") + "\n" + text[line_end:]


def _edit_lines(
    markdown: str, pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str]
) -> str:
    """Substitute per line; drop lines that held only removed text, without doubling blanks."""
    output: list[str] = []
    dropped = False
    for line in markdown.split("\n"):
        edited = pattern.sub(replace, line)
        if edited != line:
            if not edited.strip():
                dropped = True
                continue
            if not line[-1:].isspace():
                edited = edited.rstrip()
        if not edited.strip():
            if dropped and (not output or not output[-1].strip()):
                continue
        else:
            dropped = False
        output.append(edited)
    return "\n".join(output)


def _token_owners(root: ET.Element, pattern: re.Pattern[str]) -> dict[str, list[ET.Element]]:
    """The elements whose own text contains each marker, found in one pass."""
    owners: dict[str, list[ET.Element]] = defaultdict(list)
    for parent in root.iter():
        for match in pattern.finditer(parent.text or ""):
            owners[match.group(0)].append(parent)
        for child in parent:
            for match in pattern.finditer(child.tail or ""):
                owners[match.group(0)].append(parent)
    return owners


def _remove_tokens(root: ET.Element, pattern: re.Pattern[str]) -> None:
    for node in root.iter():
        if node.text:
            node.text = pattern.sub("", node.text)
        if node.tail:
            node.tail = pattern.sub("", node.tail)


def _recover_images(
    markdown: str,
    reference: str,
    source: SourceDocument,
    paths: ConversionPaths,
    work: Path,
    report: TransformReport,
    stager: ImageStager,
) -> tuple[str, str]:
    """Publish each aligned Word image at its Pandoc position with its Word alt text."""
    media_root = (work / "pandoc-media").resolve()
    units: dict[str, list[int]] = defaultdict(list)
    for index, image in enumerate(source.images):
        units[image.token].append(index)
    for token, indexes in units.items():
        description = source.images[indexes[0]].description
        where = f"image at paragraph {description.paragraph_index + 1}"
        try:
            _region(markdown, token)
            _start, _end, fragment = _region(reference, token)
            parsed = _reference.parse_html(fragment)
            nodes = list(parsed.iter("img"))
            if not nodes and len(indexes) == 1:
                nodes = [ET.SubElement(parsed, "img")]
            if len(nodes) != len(indexes):
                raise ConversionError(
                    f"Pandoc shows {len(nodes)} image(s) where Word has {len(indexes)}"
                )
            for node, index in zip(nodes, indexes, strict=True):
                target = node.get("src")
                if target is None:
                    continue
                extracted = Path(urllib.parse.unquote(target)).resolve()
                if not extracted.is_relative_to(media_root) or not extracted.is_file():
                    raise ConversionError(f"Pandoc did not extract referenced media: {target!r}")
                name = source.images[index].package_name
                if extracted.read_bytes() != source.media[name]:
                    report.warn(f"Word/Pandoc image content differs for {name}; Word's is used")
            urls: list[str] = []
            for node, index in zip(nodes, indexes, strict=True):
                image = source.images[index]
                published = stager.publish(
                    "part:" + image.package_name,
                    source.media[image.package_name],
                    image.published_name,
                    report,
                )
                source.images[index] = dataclasses.replace(image, published_name=published)
                urls.append(_media.image_url(published, paths))
                # Markdown image syntax has no size, so Word's display size is dropped.
                for attribute in set(node.attrib) - {"src", "alt"}:
                    del node.attrib[attribute]
                node.set("src", urls[-1])
                node.set("alt", description.alt_text or "")
                if description.alt_text is None:
                    report.warn(
                        f"Unresolved alt text: {image.package_name} at source paragraph "
                        f"{description.paragraph_index + 1}; leaving alt text empty"
                    )
            content = _reference.inner_html(parsed)
            loose = [parsed.text or "", *(child.tail or "" for child in parsed)]
            plain = (
                " ".join(_tables.table_inline(child) or "" for child in parsed)
                if len(parsed)
                and all(child.tag == "img" for child in parsed)
                and not "".join(loose).strip()
                else None
            )
            reference = _replace_region(reference, token, content)
            markdown = _replace_region(markdown, token, plain if plain is not None else content)
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the image stays where MarkItDown put it
            report.skipped_error(
                "images",
                error,
                where,
                "; left where MarkItDown put it",
                items=(token,),
                shape=_region_shape(markdown, reference, token, "images")
                if report.explaining
                else None,
            )
            markdown = _mark_alt_text(markdown, token)
            markdown, reference = _strip_pair(markdown, token), _strip_pair(reference, token)
            continue
        source.published_urls.update(urls)
        source.recovered_images.add(token)
        report.applied("images", count=len(indexes))
        if description.from_caption:
            report.applied("images", _CAPTIONED, len(indexes))
        if description.from_heading:
            report.applied("images", _HEADED, len(indexes))
    return markdown, reference


def _recover_links(
    markdown: str, reference: str, source: SourceDocument, report: TransformReport
) -> tuple[str, str]:
    """Use Pandoc's rendering of each Word hyperlink, including its exact target."""
    unrecovered = re.compile(re.escape(source.prefix) + r"D\d+DATA")
    for number, token in enumerate(source.links, 1):
        try:
            _start, _end, content = _region(markdown, token)
            _start, _end, fragment = _region(reference, token)
            parsed = _reference.parse_html(fragment)
            if unrecovered.search(content) or any(
                node.get("src") not in source.published_urls for node in parsed.iter("img")
            ):
                raise ConversionError("Word hyperlink contains an image that was not recovered")
            links = [node for node in parsed.iter("a") if node.get("href") is not None]
            if not links:
                raise ConversionError(
                    "Pandoc rendered this Word hyperlink as plain text"
                    if "".join(parsed.itertext()).strip()
                    else "Pandoc rendered nothing for this Word hyperlink"
                )
            if len(links) > 1:
                raise ConversionError(f"Pandoc rendered {len(links)} links for one Word hyperlink")
            node = links[0]
            target = node.get("href", "")
            inline = (
                _tables.table_inline(node, table_cell=False)
                if len(parsed) == 1 and parsed[0] is node and not (parsed.text or "").strip()
                else None
            )
            rendered = _reference.inner_html(parsed)
            reference = _replace_region(reference, token, rendered)
            markdown = _replace_region(markdown, token, inline if inline is not None else rendered)
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the link stays as MarkItDown converted it
            report.skipped_error(
                "links",
                error,
                f"hyperlink {number}",
                _KEPT,
                items=(token,),
                shape=_region_shape(markdown, reference, token, "links")
                if report.explaining
                else None,
            )
            markdown, reference = _strip_pair(markdown, token), _strip_pair(reference, token)
            continue
        source.link_targets.append(target)
        report.applied("links")
    return markdown, reference


def _publish_reference_media(
    root: ET.Element,
    source: SourceDocument,
    paths: ConversionPaths,
    work: Path,
    report: TransformReport,
    stager: ImageStager,
) -> None:
    """Publish Pandoc media inside lists and tables, which are rendered from the reference."""
    media_root = (work / "pandoc-media").resolve()

    def visit(node: ET.Element, *, inside: bool) -> None:
        if inside and node.tag == "img":
            extracted = Path(urllib.parse.unquote(node.get("src", ""))).resolve()
            if extracted.is_relative_to(media_root) and extracted.is_file():
                name = stager.publish_data("", extracted.read_bytes(), report, extracted.suffix)
                url = _media.image_url(name, paths)
                node.set("src", url)
                source.published_urls.add(url)
        for child in node:
            visit(child, inside=inside or child.tag in {"table", "ol", "ul"})

    visit(root, inside=False)


def _restore_empty_rows(root: ET.Element, source: SourceDocument, report: TransformReport) -> None:
    if not source.empty_row_tokens:
        return
    parents = {child: parent for parent in root.iter() for child in parent}
    owners = _token_owners(root, re.compile(re.escape(source.prefix) + r"E\d+EMPTYROW"))
    for token in source.empty_row_tokens:
        found = owners.get(token, [])
        current: ET.Element | None = found[0] if len(found) == 1 else None
        while current is not None and current.tag != "tr":
            current = parents.get(current)
        if current is None or _reference.plain_text("".join(current.itertext())) != token:
            report.skipped(
                "tables",
                "Word row without cells could not be aligned; kept as an empty cell",
                f"Word row without cells ({len(found)} Pandoc match(es)); kept as an empty cell",
            )
            continue
        for child in list(current):
            current.remove(child)
        current.text = None


def _single_paragraph(item: ET.Element) -> bool:
    """Whether list *item* holds one paragraph, without a line break, and any nested lists."""
    children = list(item)
    return (
        bool(children)
        and children[0].tag == "p"
        and next(children[0].iter("br"), None) is None
        and all(child.tag in {"ol", "ul"} for child in children[1:])
        and not (item.text or "").strip()
        and not any((child.tail or "").strip() for child in children)
    )


def _tighten_lists(root: ET.Element) -> bool:
    """Unwrap the paragraph of each item of a list whose items are all one paragraph.

    Word list items are paragraphs, which Markdown writes with a blank line between items, and
    renderers then space them as paragraphs. Without the paragraph, the list is written tight. A
    list with an item of several paragraphs, or a line break, which starts one, stays loose.
    Return whether any list changed.
    """
    changed = False
    for container in root.iter():
        items = [child for child in container if child.tag == "li"]
        if container.tag not in {"ol", "ul"} or not items:
            continue
        if not all(_single_paragraph(item) for item in items):
            continue
        for item in items:
            paragraph = item[0]
            item.remove(paragraph)
            item.text = paragraph.text
            for index, node in enumerate(paragraph):
                item.insert(index, node)
        changed = True
    return changed


def _list_shape(root: ET.Element) -> list[tuple[str, str, int, int]]:
    shape: list[tuple[str, str, int, int]] = []
    for node in root.iter():
        if node.tag not in {"ol", "ul"}:
            continue
        try:
            start = int(node.get("start", "1"))
        except ValueError as error:
            raise ConversionError("Reference list has an invalid start number") from error
        shape.append((str(node.tag), node.get("type", "1"), start, len(node.findall("li"))))
    return shape


def _render_list(
    markdown: str,
    node: ET.Element,
    items: list[SourceListItem],
    tokens: re.Pattern[str],
    pandoc: str,
) -> tuple[str, int]:
    first, last = items[0].token, items[-1].token
    start, _end, _content = _region(markdown, first)
    _start, end, _content = _region(markdown, last)
    line_start = markdown.rfind("\n", 0, start) + 1
    line_end = markdown.find("\n", end)
    if line_end < 0:
        line_end = len(markdown)
    # MarkItDown may give Word list items as plain paragraphs (Mammoth does not follow every
    # list-style encoding), and gives a list that starts at a nested level one marker per
    # level, such as "* 1."; the visible-text check below still guards the replacement.
    opening, closing = markdown[line_start:start], markdown[end:line_end]
    markers = re.fullmatch(r"\s*(?:(?:[-+*]|\d+[.)])\s+)*[*_~`]*", opening)
    if markers is None or closing.strip("*_~` \t"):
        raise _Mismatch(
            "Cannot align Word list boundaries with MarkItDown output",
            {
                "before the list": _explain.text_shape(opening),
                "after the list": _explain.text_shape(closing),
            },
        )
    primary = tokens.sub("", markdown[line_start:line_end])
    # MarkItDown nests a Word list under the list before it when both are at a nested level,
    # where Pandoc keeps them apart; read it at its own indentation, which Markdown would
    # otherwise make a code block.
    indent = len(primary) - len(primary.lstrip(" \t"))
    if indent:
        primary = "\n".join(
            line[min(indent, len(line) - len(line.lstrip(" \t"))) :] for line in primary.split("\n")
        )
    cleaned = copy.deepcopy(node)
    _remove_tokens(cleaned, tokens)

    def text(root: ET.Element) -> str:
        # MarkItDown emphasis that Markdown cannot close, such as "**Note:**next", shows
        # its delimiters; the replacement comes from Pandoc, which keeps the emphasis.
        return re.sub(r"[*_~]", "", _reference.plain_text(_reference.visible_reference_text(root)))

    theirs, ours = text(cleaned), text(_reference.pandoc_html(primary, pandoc))
    if theirs != ours:
        unmarked = sum(
            1 for paragraph in node.iter("p") if not tokens.search("".join(paragraph.itertext()))
        )
        raise _Mismatch(
            "Word/Pandoc/MarkItDown list content cannot be aligned",
            {
                "difference": _explain.text_difference(theirs, ours),
                "around the difference": _explain.difference_window(theirs, ours),
                "first line starts with": _explain.text_shape(opening),
                "first line indent": _explain.indent_range(opening),
                "Pandoc paragraphs without a marker": _explain.cap(unmarked),
            },
        )
    # A line break at the end of a Word list item shows nothing; Markdown cannot end with one.
    for block in cleaned.iter():
        while (
            block.tag in {"p", "li"}
            and len(block)
            and block[-1].tag == "br"
            and not (block[-1].tail or "").strip()
        ):
            block.remove(block[-1])
    # Markdown numbers lists only with decimals, so Word's letters and Roman numerals become
    # numbers rather than an HTML list.
    for ordered in cleaned.iter("ol"):
        ordered.attrib.pop("type", None)
    omitted = _reference.omit_empty_list_items(cleaned)
    if not any(child.tag == "li" for child in cleaned):
        return markdown[:line_start] + markdown[line_end:].lstrip("\n"), omitted
    shape = _list_shape(cleaned)
    rendered = None
    if all(start >= 0 for _tag, _kind, start, _count in shape):
        tight = copy.deepcopy(cleaned)
        # A tight list cannot always say the same, such as a nested list that is numbered from
        # other than 1 after a paragraph; the loose list can.
        for candidate in (tight, cleaned) if _tighten_lists(tight) else (cleaned,):
            rendered = _markdown_list(candidate, shape, pandoc)
            if rendered is not None:
                break
    if rendered is None:
        rendered = _reference.html_element(cleaned)
    return markdown[:line_start] + rendered.rstrip("\n") + "\n" + markdown[line_end:], omitted


def _markdown_list(
    cleaned: ET.Element, shape: list[tuple[str, str, int, int]], pandoc: str
) -> str | None:
    """The Markdown of the HTML list *cleaned*, or None when it does not read back the same."""
    markup, reader = _reference.html_element(cleaned), "--from=html"
    if "\\" in markup or "<br" in markup:
        tree = json.loads(
            _reference.run_command(
                [pandoc, "--from=html", "--to=json"],
                "Faithful Word list rendering",
                input_text=markup,
            )
        )
        # A line break in a list item starts a new paragraph in it, as elsewhere.
        tree = _reference.split_paragraphs_at_line_breaks(tree)
        markup = _reference.gfm_input(tree)
        reader = "--from=json"
    rendered = _reference.run_command(
        [pandoc, reader, "--to=gfm+raw_html", "--wrap=none"],
        "Faithful Word list rendering",
        input_text=markup,
    )
    rendered = _normalization.normalize_unordered_lists(rendered)[0]
    round_trip = _reference.pandoc_html(rendered, pandoc)
    if _list_shape(round_trip) != shape or _reference.plain_text(
        _reference.visible_reference_text(round_trip)
    ) != _reference.plain_text(_reference.visible_reference_text(cleaned)):
        return None
    return rendered


def _recover_lists(
    markdown: str,
    reference: ET.Element,
    source: SourceDocument,
    pandoc: str,
    report: TransformReport,
) -> str:
    """Render each Word list from Pandoc's structure, which keeps numbering and nesting."""
    tokens = re.compile(re.escape(source.prefix) + r"N\d+(?:BEGIN|END)")
    parents = {child: parent for parent in reference.iter() for child in parent}
    owners = _token_owners(reference, tokens)
    groups: dict[ET.Element, list[SourceListItem]] = {}
    for item in source.list_items:
        if item.in_table:
            continue
        found = owners.get(item.token + "BEGIN", [])
        owner: ET.Element | None = found[0] if len(found) == 1 else None
        while owner is not None and owner.tag != "li":
            owner = parents.get(owner)
        if owner is None:
            if not item.continuation:
                report.skipped(
                    "lists",
                    "Pandoc did not keep a Word list item as a list item" + _KEPT,
                    f"list item ({len(found)} Pandoc match(es)){_KEPT}",
                    items=(item.token,),
                    shape={"Pandoc markers": _explain.cap(len(found))},
                )
            continue
        outer = parents[owner]
        current: ET.Element | None = outer
        while current is not None:
            if current.tag in {"ol", "ul"}:
                outer = current
            current = parents.get(current)
        groups.setdefault(outer, []).append(item)
    for number, (node, items) in enumerate(groups.items(), 1):
        try:
            markdown, omitted = _render_list(markdown, node, items, tokens, pandoc)
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the list stays as MarkItDown converted it
            report.skipped_error(
                "lists",
                error,
                f"list {number}",
                _KEPT,
                items=[item.token for item in items],
                shape=_shape(error),
            )
            continue
        report.applied("lists")
        report.applied("lists", _EMPTY_ITEMS, omitted)
    _remove_tokens(reference, tokens)
    return tokens.sub("", markdown)


def _reference_headings(root: ET.Element, source: SourceDocument, report: TransformReport) -> None:
    """Restore Word heading levels in the reference; tables are rendered from it."""
    tokens = re.compile(re.escape(source.prefix) + r"H\d+(?:BEGIN|END)")
    containers: dict[str, list[ET.Element]] = defaultdict(list)
    for node in root.iter():
        if node.tag not in _HEADING_CONTAINERS:
            continue
        text = "".join(node.itertext())
        for match in tokens.finditer(text):
            if match.group(0).endswith("BEGIN"):
                token = match.group(0)[: -len("BEGIN")]
                if token + "END" in text:
                    containers[token].append(node)
    for heading in source.headings:
        found = containers.get(heading.token, [])
        if len(found) == 1:
            found[0].tag = f"h{heading.level}"
            found[0].attrib.pop("id", None)
            if heading.in_table:
                report.applied("headings")
        elif heading.in_table:
            report.skipped(
                "headings",
                "Word heading in a table could not be aligned; kept as text",
                f"heading {heading.title!r} in a table ({len(found)} Pandoc match(es)); "
                "kept as text",
                items=(heading.token,),
                shape={"Pandoc matches": _explain.cap(len(found))},
            )
    _remove_tokens(root, tokens)


def _repair_reference_tables(
    root: ET.Element, source: SourceDocument, report: TransformReport
) -> dict[str, ET.Element]:
    """Rebuild each table from Word rows, spans, and headers; innermost tables first."""
    markers: dict[str, list[ET.Element]] = defaultdict(list)
    boundary = re.compile(re.escape(source.prefix) + r"T\d+(?:BEGIN|END)")
    for node in root.iter("p"):
        text = "".join(node.itertext()).strip()
        if boundary.fullmatch(text):
            markers[text].append(node)
    parents = {child: parent for parent in root.iter() for child in parent}
    rendered: dict[str, ET.Element] = {}
    for number, descriptor in reversed(list(enumerate(source.tables, 1))):
        start_nodes = markers.get(descriptor.token + "BEGIN", [])
        end_nodes = markers.get(descriptor.token + "END", [])
        try:
            if len(start_nodes) != 1 or len(end_nodes) != 1:
                raise ConversionError("Missing or ambiguous Word/Pandoc table boundary")
            start, end = start_nodes[0], end_nodes[0]
            parent = parents[start]
            if parents[end] is not parent:
                raise ConversionError("Word/Pandoc table boundaries have different containers")
            children = list(parent)
            start_index, end_index = children.index(start), children.index(end)
            between = children[start_index + 1 : end_index]
            if len(between) > 1 or (between and between[0].tag != "table"):
                raise ConversionError("Word/Pandoc table stream has irreconcilable extra content")
            repaired = _tables.repair_source_table(
                descriptor.source, copy.deepcopy(between[0]) if between else None
            )
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the table stays as MarkItDown converted it
            for node in (*start_nodes, *end_nodes):
                owner = parents.get(node)
                if owner is not None and node in list(owner):
                    owner.remove(node)
            report.skipped_error(
                "tables", error, f"table {number}", _KEPT, items=(descriptor.token,)
            )
            continue
        for child in children[start_index : end_index + 1]:
            parent.remove(child)
        parent.insert(start_index, repaired)
        rendered[descriptor.token] = repaired
    _remove_tokens(root, boundary)
    return rendered


def _primary_heading(markdown: str, heading: SourceHeading) -> str:
    start, end, title = _region(markdown, heading.token)
    line_start = markdown.rfind("\n", 0, start) + 1
    line_end = markdown.find("\n", end)
    if line_end < 0:
        line_end = len(markdown)
    prefix, suffix = markdown[line_start:start], markdown[end:line_end]
    formatting = re.fullmatch(r" {0,3}(?:#{1,6}\s*)?([*_~`]*)", prefix)
    if formatting is None or suffix.strip() != formatting.group(1)[::-1]:
        raise _Mismatch(
            "Source heading overlaps unexpected MarkItDown formatting",
            {
                "before the heading": _explain.text_shape(prefix),
                "after the heading": _explain.text_shape(suffix),
                "heading lines": "1" if "\n" not in title.strip() else "2+",
            },
        )
    title = title.strip().replace("\n", "<br>")
    title = formatting.group(1) + title + formatting.group(1)[::-1]

    def escape_hashes(match: re.Match[str]) -> str:
        return match.group(1).replace("#", r"\#")

    title = re.sub(r"(?<!\\)(#+)$", escape_hashes, title)
    rendered = "#" * heading.level + " " + title
    return markdown[:line_start] + rendered + markdown[line_end:]


_MARKDOWN_IMAGE = re.compile(r"!\[((?:\\.|[^\\\]\n])*)\]\(([^()\s]*)((?:\s+\"[^\"\n]*\")?)\)")


def resolve_data_images(
    markdown: str,
    source: SourceDocument,
    paths: ConversionPaths,
    report: TransformReport,
    stager: ImageStager,
) -> str:
    """Publish the images left where MarkItDown put them, with their Word alt text."""
    prefix = re.escape(source.prefix)
    unit_alt = re.compile(prefix + r"(I\d+)ALT")
    data_token = re.compile(prefix + r"D\d+DATA")
    alts: dict[str, str] = {}
    for image in source.images:
        alts.setdefault(image.token, image.description.alt_text or "")
    urls: dict[str, str] = {}

    def publish(token: str) -> str:
        if token not in urls:
            mime, data = source.data_images[token]
            urls[token] = _media.image_url(stager.publish_data(mime, data, report), paths)
        report.baseline_images += 1
        return urls[token]

    def image(match: re.Match[str]) -> str:
        alt, target, title = match.groups()
        unit = unit_alt.fullmatch(alt)
        if unit is not None:
            alt = _media.escape_image_alt_text(alts.get(source.prefix + unit.group(1), ""))
        elif target not in source.data_images:
            return match.group(0)
        if target in source.data_images:
            target = publish(target)
        return f"![{alt}]({target}{title})"

    def bare_alt(match: re.Match[str]) -> str:
        return html.escape(alts.get(source.prefix + match.group(1), ""), quote=True)

    def leftover(match: re.Match[str]) -> str:
        return publish(match.group(0)) if match.group(0) in source.data_images else ""

    markdown = _edit_lines(markdown, _MARKDOWN_IMAGE, image)
    markdown = _edit_lines(markdown, unit_alt, bare_alt)
    return _edit_lines(markdown, data_token, leftover)


def strip_markers(markdown: str, source: SourceDocument, report: TransformReport) -> str:
    """Remove every remaining alignment marker; Word text is never removed with it."""
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        literal = source.literal_tokens.get(match.group(0))
        if literal is not None:
            return literal
        removed += 1
        return ""

    markdown = _edit_lines(markdown, marker_pattern(source.prefix), replace)
    residual = re.compile(re.escape(source.prefix) + r"[A-Za-z0-9]*", re.IGNORECASE)
    markdown = _edit_lines(markdown, residual, replace)
    report.applied("cleanup", "leftover marker(s) removed", removed)
    return markdown


def restore_literal_text(markdown: str, source: SourceDocument, pandoc: str) -> str:
    if not source.literal_tokens:
        return markdown
    data: JSONValue = _reference.markdown_ast(markdown, pandoc, "Literal Word text recovery").ast
    breaking = source.prefix + "LITERALBREAK"

    def pieces(item: JSONValue) -> list[JSONValue]:
        """A Str holding Word line breaks, split around Markdown hard breaks."""
        text = item.get("c") if isinstance(item, dict) and item.get("t") == "Str" else None
        if not isinstance(text, str) or breaking not in text:
            return [replace(item)]
        result: list[JSONValue] = []
        for index, part in enumerate(text.split(breaking)):
            if index:
                result.append({"t": "LineBreak"})
            if part:
                result.append({"t": "Str", "c": replace(part)})
        return result

    def replace(value: JSONValue, *, html_content: bool = False) -> JSONValue:
        if isinstance(value, str):
            for token, character in source.literal_tokens.items():
                value = value.replace(
                    token, html.escape(character, quote=False) if html_content else character
                )
        elif isinstance(value, list):
            if html_content:
                value = [replace(item, html_content=True) for item in value]
            else:
                value = [piece for item in value for piece in pieces(item)]
        elif isinstance(value, dict):
            if value.get("t") in ("RawInline", "RawBlock"):
                content = _reference.json_list(value.get("c"))
                if len(content) == 2 and content[0] == "html":
                    value["c"] = replace(content, html_content=True)
                    return value
            value = {key: replace(item, html_content=html_content) for key, item in value.items()}
        return value

    markers = marker_pattern(source.prefix)

    def filler(item: JSONValue) -> bool:
        """Spaces and text that is only alignment markers, which are removed later."""
        if not isinstance(item, dict):
            return False
        text = item.get("c")
        if item.get("t") == "Str" and isinstance(text, str):
            return not markers.sub("", text)
        return item.get("t") in ("Space", "SoftBreak")

    def settle(value: JSONValue, container: str = "") -> JSONValue:
        """Keep line breaks in paragraphs; Markdown headings and table cells have none."""
        if isinstance(value, list):
            return [settle(item, container) for item in value]
        if not isinstance(value, dict):
            return value
        kind = value.get("t")
        if kind == "LineBreak":
            if container == "Header":
                source.heading_breaks += 1
            if container:
                return {"t": "Space"}
            source.restored_breaks += 1
            return value
        inner = container or (str(kind) if kind in ("Header", "Table") else "")
        result = {key: settle(item, inner) for key, item in value.items()}
        content = result.get("c")
        if kind in ("Para", "Plain") and isinstance(content, list):
            # A break at a block edge, even before alignment markers, would leave a backslash.
            for direction in (-1, 1):
                index = len(content) - 1 if direction < 0 else 0
                while 0 <= index < len(content):
                    item = content[index]
                    if isinstance(item, dict) and item.get("t") == "LineBreak":
                        content.pop(index)
                        source.restored_breaks -= 1
                        index -= 1 if direction < 0 else 0
                        continue
                    if not filler(item):
                        break
                    index += direction
        return result

    # A Word line break starts a new paragraph, rather than a hard break, which Azure DevOps
    # Wikis do not document; a piece that holds only markers stays with its neighbour.
    settled = _reference.split_paragraphs_at_line_breaks(settle(replace(data)), filler)
    return _reference.run_command(
        [pandoc, "--from=json", "--to=gfm+raw_html", "--wrap=none", "--markdown-headings=atx"],
        "Literal Word text recovery",
        input_text=_reference.gfm_input(settled),
    )


def _code_fence(text: str) -> str:
    """A fenced ``text`` block of *text*, with a fence that no line of it could close."""
    return _reference.code_fence(text, "text")


def _recover_code_blocks(
    markdown: str, reference: str, source: SourceDocument, report: TransformReport
) -> tuple[str, str]:
    """Replace each code block's region with its Word text in a fenced block."""
    for token, text in source.code_blocks.items():
        try:
            markdown = _replace_block_region(markdown, token, _code_fence(text))
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the lines stay as MarkItDown converted them
            report.skipped_error("code", error, "code block", _KEPT, items=(token,))
            markdown, reference = _strip_pair(markdown, token), _strip_pair(reference, token)
            continue
        region = re.compile(
            rf"<p>\s*{re.escape(token)}BEGIN\s*</p>.*?<p>\s*{re.escape(token)}END\s*</p>", re.DOTALL
        )
        found = region.search(reference)
        reference = (
            reference[: found.start()]
            + f"<pre><code>{html.escape(text, quote=False)}</code></pre>"
            + reference[found.end() :]
            if found
            else _strip_pair(reference, token)
        )
        report.applied("code", "code block(s)")
    return markdown, reference


def recover_source_structure(
    markdown: str,
    reference: str,
    source: SourceDocument,
    paths: ConversionPaths,
    work: Path,
    report: TransformReport,
    pandoc: str,
    stager: ImageStager,
) -> tuple[str, dict[str, int]]:
    """Apply each Word layer that aligns; the rest of the MarkItDown baseline is kept."""
    markdown, reference = _recover_images(markdown, reference, source, paths, work, report, stager)
    markdown, reference = _recover_links(markdown, reference, source, report)
    for token, name in source.anchors.items():
        counts = markdown.count(token), reference.count(token)
        if counts != (1, 1):
            report.skipped(
                "checks",
                "a converter omitted or repeated a Word bookmark position",
                f"Word bookmark {name!r}: MarkItDown has {counts[0]} and Pandoc {counts[1]} "
                "position marker(s)",
                items=(token,),
                shape={
                    "MarkItDown markers": _explain.cap(counts[0]),
                    "Pandoc markers": _explain.cap(counts[1]),
                },
            )
    positions = re.compile(re.escape(source.prefix) + r"(?:A\d+ANCHOR|P\d+PARAGRAPH)")
    markdown = _edit_lines(markdown, positions, lambda _match: "")
    reference = positions.sub("", reference)
    with transform(report, "code"):
        markdown, reference = _recover_code_blocks(markdown, reference, source, report)
    stats = {"tables": len(source.tables), "html_tables": 0, "multiline_tables": 0}
    tree: ET.Element | None = None
    try:
        tree = _reference.parse_html(reference)
    except FATAL_ERRORS:
        raise
    except Exception as error:  # noqa: BLE001 - lists and tables stay as MarkItDown converted them
        for name in ("lists", "tables"):
            report.failed(name, error)
    if tree is not None:
        _publish_reference_media(tree, source, paths, work, report, stager)
        markdown = _recover_lists(markdown, tree, source, pandoc, report)
        _restore_empty_rows(tree, source, report)
        _reference_headings(tree, source, report)
        tables = _repair_reference_tables(tree, source, report)
        for number, descriptor in enumerate(source.tables, 1):
            table = tables.get(descriptor.token)
            if descriptor.nested or table is None:
                continue
            try:
                rendered = _tables.render_source_table(table)
                markdown = _replace_block_region(markdown, descriptor.token, rendered.text)
            except FATAL_ERRORS:
                raise
            except Exception as error:  # noqa: BLE001 - the table stays as MarkItDown converted it
                report.skipped_error(
                    "tables", error, f"table {number}", _KEPT, items=(descriptor.token,)
                )
                continue
            stats["html_tables"] += not rendered.markdown
            stats["multiline_tables"] += rendered.markdown and "<br>" in rendered.text
            report.applied("lists", _EMPTY_ITEMS, rendered.omitted)
            if rendered.markdown:
                source.markdown_tables.add(descriptor.token)
                report.applied("tables", "Markdown")
            else:
                report.warn(f"Table {number} remains HTML: {rendered.reason}")
                report.applied("tables", "HTML")
    for heading in source.headings:
        if heading.in_table:
            continue
        try:
            markdown = _primary_heading(markdown, heading)
        except FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - the heading stays as MarkItDown converted it
            if heading.token in source.listed_headings:
                markdown = _strip_pair(markdown, heading.token)
                report.skipped(
                    "headings",
                    _LISTED_HEADING,
                    f"heading {heading.title!r}: {_LISTED_HEADING}",
                    items=(heading.token,),
                )
                continue
            report.skipped_error(
                "headings",
                error,
                f"heading {heading.title!r}",
                _KEPT,
                items=(heading.token,),
                shape=_shape(error),
            )
            continue
        report.applied("headings")
    markdown = resolve_data_images(markdown, source, paths, report, stager)
    markdown = strip_markers(markdown, source, report)
    (work / "reconciled-reference.html").write_text(
        _reference.inner_html(tree) if tree is not None else reference, encoding="utf-8"
    )
    return markdown, stats
