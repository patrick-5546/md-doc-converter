"""Recover Word table geometry and emit faithful Markdown or structural HTML."""

from __future__ import annotations

import copy
import html
import re
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.models import (
    ConversionError,
    TableRendering,
    WordCell,
    WordRow,
)
from md_doc_converter.converters.docx.ooxml import WORD
from md_doc_converter.markdown import syntax as _syntax


def canonicalize_horizontal_merges(root: ET.Element, *, strict: bool = True) -> list[str]:
    """Rewrite legacy hMerge cells as gridSpan; leniently, a row that cannot be is kept."""
    problems: list[str] = []
    # Inner rows first, so replacing an outer row's children keeps their canonical form.
    for row in reversed(list(root.iter(WORD + "tr"))):
        if row.find(f"./{WORD}tc/{WORD}tcPr/{WORD}hMerge") is None:
            continue
        if strict:
            _canonicalize_row(row)
            continue
        trial = copy.deepcopy(row)
        try:
            _canonicalize_row(trial)
        except ConversionError as error:
            problems.append(str(error))
            continue
        row[:] = list(trial)
    return problems


def complete_table_grids(root: ET.Element) -> int:
    """Widen each table grid to its widest row; return how many grids were widened.

    Pandoc drops the cells of a row beyond the table's w:tblGrid, which Word tolerates.
    """
    widened = 0
    for table in root.iter(WORD + "tbl"):
        grid = table.find(WORD + "tblGrid")
        columns: list[ET.Element] = [] if grid is None else grid.findall(WORD + "gridCol")
        rows = table.findall(WORD + "tr")
        width = max((_row_columns(row, after=True) for row in rows), default=0)
        if width <= len(columns):
            continue
        if grid is None:
            grid = ET.Element(WORD + "tblGrid")
            properties = table.find(WORD + "tblPr")
            table.insert(0 if properties is None else list(table).index(properties) + 1, grid)
        column = columns[-1].get(WORD + "w") if columns else None
        for _extra in range(width - len(columns)):
            ET.SubElement(grid, WORD + "gridCol", {} if column is None else {WORD + "w": column})
        widened += 1
    return widened


def _vertically_merged(row: ET.Element) -> bool:
    return any(
        cell.find(f"./{WORD}tcPr/{WORD}vMerge") is not None for cell in row.findall(WORD + "tc")
    )


_BOOKMARK_TAGS = frozenset({WORD + "bookmarkStart", WORD + "bookmarkEnd"})


def _bookmark_holder(row: ET.Element) -> ET.Element | None:
    """The first paragraph of *row* outside a vertical-merge continuation, which holds nothing."""
    for cell in row.findall(WORD + "tc"):
        merge = cell.find(f"./{WORD}tcPr/{WORD}vMerge")
        if merge is not None and merge.get(WORD + "val", "continue") == "continue":
            continue
        paragraph = cell.find(WORD + "p")
        return paragraph if paragraph is not None else ET.SubElement(cell, WORD + "p")
    return None


def drop_empty_rows(root: ET.Element) -> int:
    """Remove the table rows that show nothing; return how many.

    A row shows nothing when no cell holds text, a symbol, a picture, or an equation. A row
    in a vertical merge stays, as does a table's only row and, when a first row is followed
    only by empty rows, as in a template's fill-in table, the first of them. The bookmarks
    of a removed row move to the nearest row kept in the same table, so links to them still
    resolve; a row whose bookmarks have nowhere to go stays.
    """
    removed = 0
    for table in list(root.iter(WORD + "tbl")):
        rows = table.findall(WORD + "tr")
        empty = [
            index
            for index, row in enumerate(rows)
            if not _ooxml.shows_content(row) and not _vertically_merged(row)
        ]
        if all(index in empty for index in range(1, len(rows))):
            empty = empty[1:]
        kept = [index for index in range(len(rows)) if index not in empty]
        for index in empty:
            row = rows[index]
            marks = [node for node in row.iter() if node.tag in _BOOKMARK_TAGS]
            if marks:
                holder = None
                # The nearest kept row before this one, or else after it.
                before = [other for other in reversed(kept) if other < index]
                for other in before + [other for other in kept if other > index]:
                    holder = _bookmark_holder(rows[other])
                    if holder is not None:
                        break
                if holder is None:
                    continue
                holder.extend(marks)
            table.remove(row)
            removed += 1
    return removed


def _row_columns(row: ET.Element, *, after: bool) -> int:
    """The grid columns through a row's cells, from gridBefore, and its gridAfter if *after*."""
    total = 0
    for name in ("gridBefore", "gridAfter") if after else ("gridBefore",):
        value = row.find(f"./{WORD}trPr/{WORD}{name}")
        text = "" if value is None else value.get(WORD + "val", "")
        total += int(text) if text.isdigit() else 0
    for cell in row.findall(WORD + "tc"):
        span = cell.find(f"./{WORD}tcPr/{WORD}gridSpan")
        text = "1" if span is None else span.get(WORD + "val", "1")
        total += int(text) if text.isdigit() and int(text) > 0 else 1
    return total


def _first_body_row(table: ET.Element) -> ET.Element | None:
    """The first row Pandoc does not read as a header row."""
    rows = table.findall(WORD + "tr")
    look = table.find(f"./{WORD}tblPr/{WORD}tblLook")
    first = False
    if look is not None:
        flag, mask = look.get(WORD + "firstRow"), look.get(WORD + "val", "")
        if flag is not None:
            first = flag == "1"
        elif re.fullmatch(r"[0-9A-Fa-f]+", mask):
            first = bool(int(mask, 16) & 0x20)
    previous_header = first
    for row in rows[1:] if first else rows:
        header = row.find(f"./{WORD}trPr/{WORD}tblHeader")
        continued = any(
            merge.get(WORD + "val", "continue") == "continue"
            for merge in row.findall(f"./{WORD}tc/{WORD}tcPr/{WORD}vMerge")
        )
        if (header is not None and header.get(WORD + "val") != "0") or (
            previous_header and continued
        ):
            previous_header = True
            continue
        return row
    return None


def pad_first_body_rows(root: ET.Element) -> int:
    """Pad each table's first body row with empty cells to the table width; return how many.

    Pandoc takes a table's columns from its first body row and drops the cells beyond them
    in every row, header rows included. Pad after alignment: the Word rows docx-to-md
    expects keep their own cells, and repair_source_table drops Pandoc's empty padding.
    """
    padded = 0
    for table in root.iter(WORD + "tbl"):
        rows = table.findall(WORD + "tr")
        body = _first_body_row(table)
        if body is None:
            continue
        width = max(_row_columns(row, after=True) for row in rows)
        missing = width - _row_columns(body, after=False)
        for _cell in range(missing):
            ET.SubElement(ET.SubElement(body, WORD + "tc"), WORD + "p")
        if missing > 0:
            padded += 1
    return padded


def _canonicalize_row(row: ET.Element) -> None:
    leader: ET.Element | None = None
    width = 0
    for cell in list(row.findall(WORD + "tc")):
        merge = cell.find(f"./{WORD}tcPr/{WORD}hMerge")
        if merge is None:
            leader = None
            continue
        kind = merge.get(WORD + "val", "continue")
        span = _positive_word_int(cell.find(f"./{WORD}tcPr/{WORD}gridSpan"), 1, "gridSpan")
        properties = cell.find(WORD + "tcPr")
        if properties is None:
            raise ConversionError("Horizontal merge has no cell properties")
        properties.remove(merge)
        if kind == "restart":
            leader, width = cell, span
        elif kind == "continue" and leader is not None:
            if (
                _reference.plain_text(_ooxml.word_text(cell))
                or cell.find(f".//{WORD}drawing") is not None
                or cell.find(f".//{WORD}pict") is not None
                or cell.find(f".//{WORD}tbl") is not None
                or cell.find(f".//{WORD}bookmarkStart") is not None
            ):
                raise ConversionError("Horizontal merge continuation contains retained content")
            previous_merge = leader.find(f"./{WORD}tcPr/{WORD}vMerge")
            current_merge = cell.find(f"./{WORD}tcPr/{WORD}vMerge")
            previous_kind = (
                previous_merge.get(WORD + "val", "continue") if previous_merge is not None else None
            )
            current_kind = (
                current_merge.get(WORD + "val", "continue") if current_merge is not None else None
            )
            if previous_kind != current_kind:
                raise ConversionError("Horizontal merge has incompatible vertical spans")
            width += span
            leader_properties = leader.find(WORD + "tcPr")
            if leader_properties is None:
                raise ConversionError("Horizontal merge start has no cell properties")
            grid_span = leader_properties.find(WORD + "gridSpan")
            if grid_span is None:
                grid_span = ET.SubElement(leader_properties, WORD + "gridSpan")
            grid_span.set(WORD + "val", str(width))
            row.remove(cell)
        else:
            raise ConversionError("Horizontal merge continuation has no matching start")


def _positive_word_int(
    node: ET.Element | None,
    default: int,
    label: str,
    *,
    minimum: int = 1,
) -> int:
    if node is None:
        return default
    value = node.get(WORD + "val", "")
    if not value.isdigit() or int(value) < minimum:
        raise ConversionError(f"Invalid Word {label}: {value!r}")
    return int(value)


def word_table_rows(table: ET.Element) -> list[WordRow]:
    result: list[WordRow] = []
    active: dict[tuple[int, int], WordCell] = {}
    for row in table.findall(WORD + "tr"):
        if row.find(f"./{WORD}trPr/{WORD}cellDel") is not None:
            raise ConversionError("Cannot reconcile tracked cell deletion in a Word table")
        cells: list[WordCell] = []
        before_node = row.find(f"./{WORD}trPr/{WORD}gridBefore")
        before = _positive_word_int(before_node, 0, "gridBefore", minimum=0)
        column = before
        if before:
            cells.append(WordCell(None, before))
        continued: dict[tuple[int, int], WordCell] = {}
        for cell in row.findall(WORD + "tc"):
            if cell.find(f"./{WORD}tcPr/{WORD}hMerge") is not None:
                raise ConversionError("Legacy hMerge table cells cannot be aligned unambiguously")
            width = _positive_word_int(cell.find(f"./{WORD}tcPr/{WORD}gridSpan"), 1, "gridSpan")
            merge = cell.find(f"./{WORD}tcPr/{WORD}vMerge")
            key = (column, width)
            if merge is not None and merge.get(WORD + "val", "continue") == "continue":
                if key not in active:
                    raise ConversionError("Word vertical merge continuation has no matching start")
                if (
                    _reference.plain_text(_ooxml.word_text(cell))
                    or cell.find(f".//{WORD}drawing") is not None
                    or cell.find(f".//{WORD}pict") is not None
                    or cell.find(f".//{WORD}bookmarkStart") is not None
                    or cell.find(f".//{WORD}tbl") is not None
                ):
                    raise ConversionError(
                        "Word vertical merge continuation contains retained content"
                    )
                active[key].rowspan += 1
                continued[key] = active[key]
            else:
                parsed = WordCell(cell, width)
                cells.append(parsed)
                if merge is not None:
                    if merge.get(WORD + "val") != "restart":
                        raise ConversionError("Invalid Word vertical merge value")
                    continued[key] = parsed
            column += width
        after_node = row.find(f"./{WORD}trPr/{WORD}gridAfter")
        if after_node is not None:
            after = _positive_word_int(after_node, 0, "gridAfter", minimum=0)
            if after:
                cells.append(WordCell(None, after))
        active = continued
        result.append(WordRow(_ooxml.word_flag(row.find(f"./{WORD}trPr/{WORD}tblHeader")), cells))
    return result


def html_rows(table: ET.Element) -> list[ET.Element]:
    rows: list[ET.Element] = []
    for child in table:
        if child.tag == "tr":
            rows.append(child)
        elif child.tag in {"thead", "tbody", "tfoot"}:
            rows.extend(child.findall("tr"))
    return rows


def _grid_cells(
    row: ET.Element, cells: list[ET.Element], expected: list[WordCell]
) -> list[ET.Element]:
    """Fold Pandoc's grid padding into the Word row's cells, if that is what it is.

    For gridBefore and gridAfter Pandoc writes one empty cell per grid column, where the
    Word row has one placeholder cell, and it pads a short row with empty cells.
    """
    if len(cells) == len(expected):
        return cells

    def blank(node: ET.Element) -> bool:
        spans = node.get("colspan", "1"), node.get("rowspan", "1")
        return len(node) == 0 and not (node.text or "").strip() and spans == ("1", "1")

    width = sum(1 if cell.source is not None else cell.colspan for cell in expected)
    runs: list[list[ET.Element]] = []
    index = 0
    for cell in expected:
        size = 1 if cell.source is not None else cell.colspan
        runs.append(cells[index : index + size])
        index += size
    placeholders = [run for run, cell in zip(runs, expected, strict=True) if cell.source is None]
    if len(cells) < width or not all(blank(node) for run in placeholders for node in run):
        return cells
    if not all(blank(node) for node in cells[width:]):
        return cells
    for run, cell in zip(runs, expected, strict=True):
        if cell.source is None and cell.colspan > 1:
            run[0].set("colspan", str(cell.colspan))
            for node in run[1:]:
                row.remove(node)
    for node in cells[width:]:
        row.remove(node)
    return [run[0] for run in runs]


def repair_source_table(source: ET.Element, reference: ET.Element | None) -> ET.Element:
    """Preserve source rows/headers; repair only differences proved by Word XML."""
    expected = word_table_rows(source)
    if reference is None:
        if any(row.cells for row in expected):
            raise ConversionError("Pandoc omitted a Word table with cells")
        reference = ET.Element("table")
    actual = html_rows(reference)
    matched: list[ET.Element] = []
    position = 0
    for ordinal, row in enumerate(expected, 1):
        if not row.cells:
            if position < len(actual) and len(actual[position]) == 0:
                selected = actual[position]
                position += 1
            else:
                selected = ET.Element("tr")
            matched.append(selected)
            continue
        if position >= len(actual):
            raise ConversionError(f"Table row {ordinal}: Pandoc omitted a Word row")
        selected = actual[position]
        position += 1
        cells = _grid_cells(
            selected, [node for node in selected if node.tag in {"td", "th"}], row.cells
        )
        if len(cells) != len(row.cells):
            raise ConversionError(
                f"Table row {ordinal}: Word has {len(row.cells)} cells, "
                f"Pandoc has {len(cells)}; refusing ambiguous alignment"
            )
        for column, (cell, expected_cell) in enumerate(zip(cells, row.cells, strict=True), 1):
            try:
                spans = (int(cell.get("colspan", "1")), int(cell.get("rowspan", "1")))
            except ValueError as error:
                raise ConversionError("Invalid Pandoc table span") from error
            if spans != (expected_cell.colspan, expected_cell.rowspan):
                raise ConversionError(
                    f"Table row {ordinal}, cell {column}: Word/Pandoc merge mismatch"
                )
            source_text = (
                _ooxml.word_text(expected_cell.source) if expected_cell.source is not None else ""
            )
            if _reference.plain_text(source_text) != _reference.plain_text(
                _reference.visible_reference_text(cell)
            ):
                raise ConversionError(
                    f"Table row {ordinal}, cell {column}: Word/Pandoc content does not align"
                )
            cell.tag = "th" if row.header else "td"
        matched.append(selected)
    if position != len(actual):
        raise ConversionError("Pandoc introduced table rows not unambiguously aligned with Word")
    for child in list(reference):
        if child.tag in {"tr", "thead", "tbody", "tfoot"}:
            reference.remove(child)
    group: ET.Element | None = None
    in_header = True
    for row, rendered in zip(expected, matched, strict=True):
        tag = "thead" if in_header and row.header else "tbody"
        if tag == "tbody":
            in_header = False
        if group is None or group.tag != tag:
            group = ET.SubElement(reference, tag)
        group.append(rendered)
    caption = source.find(f"./{WORD}tblPr/{WORD}tblCaption")
    if caption is not None and reference.find("caption") is None:
        value = caption.get(WORD + "val", "")
        if value:
            element = ET.Element("caption")
            element.text = value
            reference.insert(0, element)
    return reference


def split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:-1]:
        if character == "|" and not escaped:
            cells.append("".join(current))
            current = []
        else:
            current.append(character)
        escaped = not escaped if character == "\\" else False
    cells.append("".join(current))
    return cells


def compact_table_row(line: str) -> str:
    cells = split_table_row(line)
    if cells is None:
        return line
    return "|" + "|".join(f" {cell.strip()} " if cell.strip() else " " for cell in cells) + "|"


def _table_text(value: str | None) -> str:
    text = re.sub(r"\s+", " ", value or "")
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", html.escape(text, quote=False))


def table_inline(node: ET.Element, *, table_cell: bool = True, header: bool = False) -> str | None:
    """Markdown for *node*, or None when it needs HTML; in a *header* cell, a line break,
    which Word authors use to narrow a column, is a space, so that the renderer wraps it."""
    if node.tag == "br":
        if header:
            return " "
        # A pipe-table cell needs HTML for a line break; elsewhere, such as in link text
        # or a caption, a space keeps the text on one line.
        return "<br>" if table_cell else " "
    if node.tag == "img":
        if set(node.attrib) - {"src", "alt"}:
            return None
        return f"![{_media.escape_image_alt_text(node.get('alt', ''))}]({node.get('src', '')})"
    if node.tag == "a" and set(node.attrib) - {"href", "title"}:
        return None
    if node.tag == "code":
        if node.attrib or len(node):
            return None
        text = node.text or ""
        if "\n" in text or "\r" in text:
            return None
        return _reference.code_span(text, table_cell=table_cell) if text else ""
    parts = [_table_text(node.text)]
    for child in node:
        rendered = table_inline(child, table_cell=table_cell, header=header)
        if rendered is None:
            return None
        tail = _table_text(child.tail)
        # Pandoc writes a new line after a line break, which is not a space in Word.
        parts.extend((rendered, tail.lstrip(" ") if child.tag == "br" else tail))
    content = "".join(parts)
    wrappers = {"strong": "**", "b": "**", "em": "*", "i": "*", "del": "~~"}
    if node.tag in wrappers:
        if node.attrib:
            return None
        marker = wrappers[node.tag]
        return f"{marker}{content}{marker}"
    if node.tag == "a":
        target = html.escape(_reference.encoded_uri(node.get("href", "")), quote=False)
        title = node.get("title")
        suffix = f' "{html.escape(title, quote=True)}"' if title else ""
        return f"[{content}]({target}{suffix})"
    if node.tag in {"sub", "sup", "mark", "u"}:
        # Markdown has no syntax for these, so they stay HTML tags around Markdown text, as
        # Pandoc writes them in prose; every Markdown renderer reads them in a table cell.
        return None if node.attrib else f"<{node.tag}>{content}</{node.tag}>"
    # Pandoc renders a link nested in another link as an attribute-less span.
    if node.tag in {"p", "td", "th", "caption", "span"} and not node.attrib:
        return content
    return None


def _unwrap_html_child(parent: ET.Element, child: ET.Element) -> None:
    position = list(parent).index(child)
    text = child.text or ""
    if position:
        previous = parent[position - 1]
        previous.tail = (previous.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
    replacements = list(child)
    parent[position : position + 1] = replacements
    if replacements:
        replacements[-1].tail = (replacements[-1].tail or "") + (child.tail or "")
    elif position:
        previous = parent[position - 1]
        previous.tail = (previous.tail or "") + (child.tail or "")
    else:
        parent.text = (parent.text or "") + (child.tail or "")


def _table_presentation(table: ET.Element) -> ET.Element:
    root = copy.deepcopy(table)
    for node in root.iter():
        for attribute in (
            "style",
            "class",
            "width",
            "height",
            "border",
            "cellpadding",
            "cellspacing",
            "valign",
            "bgcolor",
            "align",
            "id",
        ):
            node.attrib.pop(attribute, None)
        if node.tag == "a":
            node.attrib.pop("name", None)
    for parent in reversed(list(root.iter())):
        for child in list(parent):
            if child.tag == "colgroup":
                parent.remove(child)
            elif (child.tag == "a" and "href" not in child.attrib) or (
                child.tag == "span" and not child.attrib
            ):
                _unwrap_html_child(parent, child)
    return root


_CELL_BULLET = "\u2022"


def _cell_lines(cell: ET.Element) -> tuple[str | None, str | None]:
    """Render a cell of paragraphs and lists as lines joined with <br>, or give the reason not.

    A pipe-table cell cannot hold a list, so each list item becomes a line that starts with
    a bullet or its number (Word's letters and Roman numerals become numbers, as elsewhere),
    and each further paragraph of the item a line without one. Only one-level lists
    qualify; nested lists keep the table HTML.
    """
    if (cell.text or "").strip() or any((child.tail or "").strip() for child in cell):
        return None, "mixed block and inline cell content"
    lines: list[str] = []
    for child in cell:
        if child.tag == "p":
            rendered = table_inline(child)
            if rendered is None:
                return None, "unsupported cell content"
            lines.append(rendered.strip())
            continue
        if child.tag not in {"ul", "ol"} or set(child.attrib) - {"start", "type"}:
            return None, "unsupported block or inline cell content"
        try:
            number = int(child.get("start", "1"))
        except ValueError:
            return None, "unsupported block or inline cell content"
        for item in child:
            if item.tag != "li" or item.attrib or (item.tail or "").strip():
                return None, "unsupported block or inline cell content"
            if any(node.tag in {"ul", "ol"} for node in item.iter() if node is not item):
                return None, "nested list in a cell"
            paragraphs = [node for node in item if node.tag == "p"]
            if paragraphs and (
                len(paragraphs) != len(item)
                or (item.text or "").strip()
                or any((node.tail or "").strip() for node in paragraphs)
            ):
                return None, "unsupported block or inline cell content"
            marker = _CELL_BULLET if child.tag == "ul" else f"{number}."
            number += 1
            for position, content in enumerate(paragraphs or [item]):
                inline = copy.deepcopy(content)
                inline.tag = "span"
                inline.attrib.clear()
                rendered = table_inline(inline)
                if rendered is None:
                    return None, "unsupported cell content"
                prefix = "" if position else f"{marker} "
                lines.append(f"{prefix}{rendered.strip()}".rstrip())
    return "<br>".join(lines), None


def _without_quotes(cell: ET.Element) -> ET.Element:
    """A copy of *cell* in which each block quote of paragraphs is replaced by them.

    Pandoc reads an indented Word paragraph as a block quote, which a pipe-table cell cannot
    hold; in a cell, the indent is all that it adds.
    """
    result = copy.deepcopy(cell)
    while True:
        quote = next(
            (
                (parent, child)
                for parent in result.iter()
                for child in parent
                if child.tag == "blockquote"
                and not child.attrib
                and len(child)
                and not (child.text or "").strip()
                and all(
                    node.tag in {"p", "blockquote"} and not (node.tail or "").strip()
                    for node in child
                )
            ),
            None,
        )
        if quote is None:
            return result
        _unwrap_html_child(*quote)


def _cell_markdown(rendered: str, *, header: bool) -> str:
    """A cell without the empty lines that empty Word paragraphs or a trailing line break
    leave at its start or end; a header cell's spaces are single, outside code."""
    rendered = re.sub(r"^(?:\s*<br>)+|(?:<br>\s*)+$", "", rendered.strip()).strip()
    if header:
        rendered = _syntax.transform_outside_inline_code(
            rendered, lambda text: re.sub(r" {2,}", " ", text)
        )
    return rendered


def _pipe_table(root: ET.Element) -> tuple[str | None, str | None]:
    if len(list(root.iter("table"))) != 1:
        return None, "nested table"
    source_rows = html_rows(root)
    if not source_rows:
        return None, "empty table"
    expected = len(source_rows[0])
    if not expected:
        return None, "zero-cell row"
    rendered_rows: list[list[str]] = []
    for row in source_rows:
        # The first row is the pipe table's header.
        header = not rendered_rows
        if row.attrib or len(row) != expected:
            return None, "unequal or structurally annotated rows"
        rendered_cells: list[str] = []
        for source_cell in row:
            if source_cell.tag not in {"th", "td"} or any(
                source_cell.get(attribute, "1") != "1" for attribute in ("rowspan", "colspan")
            ):
                return None, "merged cells"
            if set(source_cell.attrib) - {"rowspan", "colspan"}:
                return None, "semantic cell attributes"
            cell = _without_quotes(source_cell)
            if len(cell) and all(child.tag == "p" for child in cell):
                if (cell.text or "").strip() or any((child.tail or "").strip() for child in cell):
                    return None, "mixed block and inline cell content"
                paragraphs = [table_inline(child, header=header) for child in cell]
                if any(value is None for value in paragraphs):
                    return None, "unsupported cell content"
                lines = [value.strip() for value in paragraphs if value is not None]
                rendered = " ".join(filter(None, lines)) if header else "<br>".join(lines)
            elif any(child.tag in {"ul", "ol"} for child in cell):
                rendered, reason = _cell_lines(cell)
                if rendered is None:
                    return None, reason
            else:
                temporary = copy.deepcopy(cell)
                temporary.attrib.clear()
                rendered = table_inline(temporary, header=header)
            if rendered is None or "\n" in rendered:
                return None, "unsupported block or inline cell content"
            rendered_cells.append(_cell_markdown(rendered, header=header))
        rendered_rows.append(rendered_cells)
    if not any(rendered_rows[0]):
        # GFM readers discard an entirely blank pipe-table header.
        return None, "blank first row cannot be retained as a GFM header"
    captions: list[str] = []
    for caption in root.findall("caption"):
        text = table_inline(caption, table_cell=False)
        if text is None:
            return None, "unsupported caption content"
        text = re.sub(r"^( {0,3})(#{1,6}|[-+])(?=\s|$)", r"\1\\\2", text)
        text = re.sub(r"^( {0,3}\d{1,9})([.)])(?=\s)", r"\1\\\2", text)
        if re.fullmatch(r" {0,3}(?:-\s*){3,}", text):
            text = text.replace("-", r"\-", 1)
        captions.append(text)
    rows = [rendered_rows[0], ["---"] * expected, *rendered_rows[1:]]
    rendered = "\n".join(compact_table_row("|" + "|".join(row) + "|") for row in rows) + "\n"
    if captions:
        rendered = "\n\n".join(captions) + "\n\n" + rendered
    return rendered, None


def render_source_table(table: ET.Element) -> TableRendering:
    cleaned = _table_presentation(table)
    omitted = _reference.omit_empty_list_items(cleaned)
    markdown, reason = _pipe_table(cleaned)
    if markdown is not None:
        return TableRendering(markdown, True, omitted=omitted)
    return TableRendering(_reference.html_element(cleaned) + "\n", False, reason, omitted)


def html_table_to_pipe(table: ET.Element | str) -> str | None:
    root = _reference.parse_html(table).find("table") if isinstance(table, str) else table
    if root is None:
        raise ConversionError("Reference contains no HTML table")
    rendered = render_source_table(root)
    return rendered.text if rendered.markdown else None
