"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import unittest
from collections import Counter
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx.models import ConversionError
from tests.support.docx import (
    R,
    W,
    boundary_hyperlink_field,
    cell,
    code_run,
    formatted,
    hyperlink,
    numbered,
    page_reference,
    paragraph,
    row,
    table,
    text_box,
    toc_entry,
    word_style,
)


class HyperlinkFieldTests(unittest.TestCase):
    def test_redundant_page_reference_is_dropped_with_its_leader(self) -> None:
        for simple in (False, True):
            with self.subTest(simple=simple):
                content = hyperlink("Entry ", anchor="_Entry")
                link = content.find(W + "hyperlink")
                assert link is not None
                leader = ET.SubElement(link, W + "r")
                ET.SubElement(ET.SubElement(leader, W + "rPr"), W + "webHidden")
                ET.SubElement(leader, W + "tab")
                link.extend(page_reference("_Entry", "7", simple=simple))
                link.append(_ooxml.word_run(" suffix"))

                problems, dropped = _ooxml.normalize_hyperlink_fields(
                    content, ET.Element(_ooxml.PACKAGE_REL + "Relationships")
                )

                assert (problems, dropped) == ([], 1)
                assert _ooxml.word_text(content) == "Entry suffix"
                assert link.get(W + "anchor") == "_Entry"
                assert len(list(content.iter(W + "hyperlink"))) == 1
                assert not list(content.iter(W + "tab"))
                assert not list(content.iter(W + "fldChar"))
                assert not list(content.iter(W + "fldSimple"))
                assert not list(content.iter(W + "instrText"))

    def test_page_leader_trimming_keeps_the_entry_number_tab(self) -> None:
        content = toc_entry("_Toc1", "1", "Introduction", "3", begin=True, end=True)
        link = content.find(W + "hyperlink")
        assert link is not None

        _problems, dropped = _ooxml.normalize_hyperlink_fields(
            content, ET.Element(_ooxml.PACKAGE_REL + "Relationships")
        )

        assert dropped == 1
        assert [node.tag for run in link for node in run if node.tag != W + "rPr"] == [
            W + "t",
            W + "tab",
            W + "t",
        ]
        assert _ooxml.word_text(link) == "1Introduction"
        assert [node.get(W + "fldCharType") for node in content.iter(W + "fldChar")] == [
            "begin",
            "separate",
            "end",
        ]

    def test_different_or_external_page_reference_is_not_flattened(self) -> None:
        for simple in (False, True):
            for external in (False, True):
                with self.subTest(simple=simple, external=external):
                    content = hyperlink(
                        "Entry ",
                        anchor="_Entry" if external else "_Other",
                        relation="outside" if external else None,
                    )
                    link = content.find(W + "hyperlink")
                    assert link is not None
                    link.extend(page_reference("_Entry", simple=simple))
                    relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")
                    ET.SubElement(
                        relationships,
                        _ooxml.PACKAGE_REL + "Relationship",
                        {"Id": "outside", "Target": "https://example.test/doc#_Entry"},
                    )

                    _ooxml.normalize_hyperlink_fields(content, relationships)

                    assert list(content.iter(W + ("fldSimple" if simple else "instrText")))
                    assert _ooxml.word_text(content) == "Entry 1"

    def test_local_relationship_page_reference_keeps_the_outer_relationship(self) -> None:
        content = hyperlink("Entry ", relation="local")
        link = content.find(W + "hyperlink")
        assert link is not None
        link.extend(page_reference("_Entry"))
        relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")
        ET.SubElement(
            relationships,
            _ooxml.PACKAGE_REL + "Relationship",
            {"Id": "local", "Target": "#_Entry"},
        )

        _ooxml.normalize_hyperlink_fields(content, relationships)

        assert link.get(R + "id") == "local"
        assert not list(content.iter(W + "fldChar"))
        assert _ooxml.word_text(content) == "Entry"

    def test_standalone_page_reference_is_not_removed(self) -> None:
        content = ET.Element(W + "p")
        content.extend(page_reference("_Entry"))

        _ooxml.normalize_hyperlink_fields(content, ET.Element(_ooxml.PACKAGE_REL + "Relationships"))

        assert list(content.iter(W + "instrText"))
        assert _ooxml.word_text(content) == "1"

    def test_boundary_runs_preserve_text_formatting_and_nontext_children(self) -> None:
        content = boundary_hyperlink_field()
        first, last = content[0], content[-1]
        for run, tag, identifier in ((first, "b", "00000001"), (last, "i", "00000002")):
            run.set(W + "rsidR", identifier)
            properties = ET.Element(W + "rPr")
            ET.SubElement(properties, W + tag)
            run.insert(0, properties)
        first.insert(2, ET.Element(W + "tab"))
        last.insert(2, ET.Element(W + "br", {W + "type": "textWrapping"}))
        relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")

        _ooxml.normalize_hyperlink_fields(content, relationships)

        assert _ooxml.word_text(content) == "Before Cached label After"
        assert len(content.findall(W + "hyperlink")) == 1
        assert not list(content.iter(W + "fldChar"))
        assert not list(content.iter(W + "instrText"))
        assert [
            child.tag for run in content.findall(W + "r") for child in run if child.tag != W + "rPr"
        ] == [W + "t", W + "tab", W + "br", W + "t"]
        for run in content.findall(W + "r"):
            identifier = run.get(W + "rsidR")
            expected = "b" if identifier == "00000001" else "i"
            assert identifier in {"00000001", "00000002"}
            assert run.find(f"./{W}rPr/{W}{expected}") is not None
        assert relationships[0].get("Target") == "https://example.test/plain"

    def test_single_run_field_preserves_prefix_suffix_and_cached_content(self) -> None:
        content = boundary_hyperlink_field()
        combined = ET.Element(W + "r", {W + "rsidR": "12345678"})
        ET.SubElement(ET.SubElement(combined, W + "rPr"), W + "b")
        for run in list(content):
            combined.extend(list(run))
            content.remove(run)
        content.append(combined)

        _ooxml.normalize_hyperlink_fields(content, ET.Element(_ooxml.PACKAGE_REL + "Relationships"))

        assert _ooxml.word_text(content) == "Before Cached label After"
        link = content.find(W + "hyperlink")
        assert link is not None
        assert _ooxml.word_text(link) == "Cached label"
        for run in content.iter(W + "r"):
            assert run.get(W + "rsidR") == "12345678"
            assert run.find(f"./{W}rPr/{W}b") is not None

    def test_adjacent_fields_share_a_run_without_losing_intervening_text(self) -> None:
        content = ET.Element(W + "p")
        combined = ET.SubElement(content, W + "r")
        for index in range(2):
            field = boundary_hyperlink_field()
            for run in field:
                for node in run:
                    if node.tag == W + "instrText":
                        node.text = f' HYPERLINK "https://example.test/{index}" '
                    combined.append(node)
        relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")

        _ooxml.normalize_hyperlink_fields(content, relationships)

        assert _ooxml.word_text(content) == "Before Cached label After" * 2
        assert [_ooxml.word_text(link) for link in content.findall(W + "hyperlink")] == [
            "Cached label",
            "Cached label",
        ]
        assert [relation.get("Target") for relation in relationships] == [
            "https://example.test/0",
            "https://example.test/1",
        ]

    def test_unrelated_field_retains_its_run_children(self) -> None:
        content = boundary_hyperlink_field()
        instruction = content.find(f".//{W}instrText")
        assert instruction is not None
        instruction.text = " DATE "
        before = [ET.tostring(node) for run in content for node in run]
        relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")

        _ooxml.normalize_hyperlink_fields(content, relationships)

        assert [ET.tostring(node) for run in content for node in run] == before
        assert not list(relationships)


def _numbering(*definitions: str) -> ET.Element:
    content = "".join(definitions)
    return ET.fromstring(f'<w:numbering xmlns:w="{_ooxml.WORD_NS}">{content}</w:numbering>')


def _abstract(identity: str, content: str = '<w:lvl w:ilvl="0"/>') -> str:
    return f'<w:abstractNum w:abstractNumId="{identity}">{content}</w:abstractNum>'


def _linked(identity: str, style: str) -> str:
    return _abstract(identity, f'<w:numStyleLink w:val="{style}"/>')


def _instance(identity: str, abstract: str, extra: str = "") -> str:
    return f'<w:num w:numId="{identity}"><w:abstractNumId w:val="{abstract}"/>{extra}</w:num>'


def _targets(numbering: ET.Element) -> dict[str, str]:
    targets: dict[str, str] = {}
    for node in numbering.findall(W + "num"):
        reference = node.find(W + "abstractNumId")
        assert reference is not None
        targets[node.get(W + "numId", "")] = reference.get(W + "val", "")
    return targets


class CrossReferenceTests(unittest.TestCase):
    def test_hyperlinked_references_to_missing_bookmarks_keep_their_cached_text(self) -> None:
        for simple in (False, True):
            with self.subTest(simple=simple):
                content = ET.Element(W + "p")
                content.extend(page_reference("_Missing", "3", simple=simple))
                content.extend(page_reference("_Known", "4", simple=simple))
                unlinked = ET.SubElement(content, W + "fldSimple", {W + "instr": " REF _Gone "})
                unlinked.append(_ooxml.word_run("5"))

                missing = _ooxml.unlink_missing_references(content, {"_Known"})

                assert missing == ["_Missing"]
                assert _ooxml.word_text(content) == "345"
                instructions = [
                    *(node.text or "" for node in content.iter(W + "instrText")),
                    *(node.get(W + "instr", "") for node in content.iter(W + "fldSimple")),
                ]
                assert len(instructions) == 2
                assert not [text for text in instructions if "_Missing" in text]

    def test_reference_kinds_and_nested_simple_fields(self) -> None:
        for instruction in (" REF _Gone \\h ", " NOTEREF _Gone \\f \\h ", ' ref "_Gone" \\h '):
            with self.subTest(instruction=instruction):
                content = ET.Element(W + "p")
                outer = ET.SubElement(content, W + "fldSimple", {W + "instr": " REF _Gone \\h "})
                inner = ET.SubElement(outer, W + "fldSimple", {W + "instr": instruction})
                inner.append(_ooxml.word_run("Table 1"))

                assert _ooxml.unlink_missing_references(content, set()) == ["_Gone", "_Gone"]
                assert not list(content.iter(W + "fldSimple"))
                assert _ooxml.word_text(content) == "Table 1"


class SimpleFieldTests(unittest.TestCase):
    def test_simple_fields_become_the_equivalent_complex_fields(self) -> None:
        content = paragraph("Table ")
        outer = ET.SubElement(content, W + "fldSimple", {W + "instr": " SEQ Table \\s 1 "})
        ET.SubElement(outer, W + "fldData").text = "AAAA"
        inner = ET.SubElement(outer, W + "fldSimple", {W + "instr": " STYLEREF 1 \\s "})
        inner.append(_ooxml.word_run("2"))
        outer.append(_ooxml.word_run("-1"))
        content.append(_ooxml.word_run(": Registers"))

        assert _ooxml.expand_simple_fields(content) == 2

        assert not list(content.iter(W + "fldSimple"))
        assert _ooxml.word_text(content) == "Table 2-1: Registers"
        labels: list[str] = []
        for run in content:
            marker = run.find(W + "fldChar")
            code = run.find(W + "instrText")
            if marker is not None:
                labels.append(marker.get(W + "fldCharType", ""))
            elif code is not None:
                assert code.get(_ooxml.XML_SPACE) == "preserve"
                labels.append(f"code:{code.text}")
            else:
                labels.append(_ooxml.word_text(run))
        assert labels == [
            "Table ",
            "begin",
            "code: SEQ Table \\s 1 ",
            "separate",
            "begin",
            "code: STYLEREF 1 \\s ",
            "separate",
            "2",
            "end",
            "-1",
            "end",
            ": Registers",
        ]
        data = content.find(f"{W}r/{W}fldChar/{W}fldData")
        assert data is not None
        assert data.text == "AAAA"

    def test_content_without_simple_fields_is_unchanged(self) -> None:
        content = paragraph("Plain")
        content.extend(page_reference("_Known", "4"))
        before = ET.tostring(content)

        assert _ooxml.expand_simple_fields(content) == 0
        assert ET.tostring(content) == before


_FIELD_MARKS = {"{": "begin", "|": "separate", "}": "end"}


def _line(*parts: str) -> ET.Element:
    """A paragraph of one-child runs: "{", "|", "}" are field characters; "@..." instructions."""
    result = ET.Element(W + "p")
    for part in parts:
        run = ET.SubElement(result, W + "r")
        if part in _FIELD_MARKS:
            ET.SubElement(run, W + "fldChar", {W + "fldCharType": _FIELD_MARKS[part]})
        elif part.startswith("@"):
            ET.SubElement(run, W + "instrText").text = part[1:]
        else:
            ET.SubElement(run, W + "t").text = part
    return result


def _parts(paragraph: ET.Element) -> list[str]:
    """The paragraph in _line notation."""
    kinds = {kind: mark for mark, kind in _FIELD_MARKS.items()}
    result: list[str] = []
    for run in paragraph:
        for child in run:
            if child.tag == W + "fldChar":
                result.append(kinds[child.get(W + "fldCharType", "")])
            elif child.tag == W + "instrText":
                result.append("@" + (child.text or ""))
            elif child.tag == W + "t":
                result.append(child.text or "")
    return result


class FieldRepairTests(unittest.TestCase):
    def test_fields_pandoc_cannot_close_are_reduced_to_their_text(self) -> None:
        lines = [
            ("A ", "{", "|", "x", "}"),
            ("{", "@ IF ", "{", "@ REF _a ", "|", "1", "}", '@ = 1 "y" ', "|", "y", "}"),
            ("B", "}", "|"),
            ("{", '@ TOC \\o "1-3" ', "|", "Entry"),
            ("Entry 2",),
            ("}",),
            ("{", '@ HYPERLINK "https://example.test" ', "|", "Site"),
            ("Site 2", "}"),
            ("C ", "{", "@ PAGEREF _a ", "|", "3"),
        ]
        body = ET.Element(W + "body")
        body.extend(_line(*parts) for parts in lines)

        counts = _ooxml.repair_fields(body)

        assert counts == Counter(
            {
                "field(s) unwrapped": 3,
                "stray field mark(s) removed": 2,
                "field(s) in an instruction removed": 1,
            }
        )
        assert [_parts(line) for line in body] == [
            ["A ", "x"],
            ["{", "@ IF ", '@ = 1 "y" ', "|", "y", "}"],
            ["B"],
            ["Entry"],
            ["Entry 2"],
            [],
            ["{", '@ HYPERLINK "https://example.test" ', "|", "Site"],
            ["Site 2", "}"],
            ["C ", "3"],
        ]
        assert not [run for line in body for run in line if not len(run)]

    def test_well_formed_and_deleted_fields_are_unchanged(self) -> None:
        content = _line("Table ", "{", "@ SEQ Table ", "|", "{", "@ STYLEREF 1 ", "|", "2", "}")
        content.extend(_line("-1", "}", ": Registers", "{", "@ PAGE ", "}"))
        content.extend(page_reference("_Known", "4"))
        ET.SubElement(content, W + "del").extend(_line("}", "|"))
        alternative = ET.SubElement(content, _ooxml.MC + "AlternateContent")
        ET.SubElement(alternative, _ooxml.MC + "Choice").extend(_line("{"))
        ET.SubElement(alternative, _ooxml.MC + "Fallback").extend(_line("Fallback"))
        before = ET.tostring(content)

        assert _ooxml.repair_fields(content) == Counter()
        assert ET.tostring(content) == before


class ListNumberingTests(unittest.TestCase):
    def test_list_style_links_resolve_through_chained_list_styles(self) -> None:
        numbering = _numbering(
            _abstract("1"),
            _linked("2", "Outline"),
            _linked("3", "Nested"),
            *(_instance(identity, identity) for identity in "123"),
        )
        styles = {
            "Outline": word_style("Outline", "numbering", number="1"),
            "Nested": word_style("Nested", "numbering", number="2"),
        }

        assert _ooxml.link_numbering_styles(numbering, styles)
        assert _targets(numbering) == {"1": "1", "2": "1", "3": "1"}
        assert _ooxml.numbered_lists(numbering) == {"1", "2", "3"}

    def test_missing_or_cyclic_list_style_links_are_left_unnumbered(self) -> None:
        numbering = _numbering(
            _abstract("1"),
            _linked("2", "Missing"),
            _linked("3", "Loop"),
            *(_instance(identity, identity) for identity in "123"),
        )
        styles = {"Loop": word_style("Loop", "numbering", number="3")}

        assert not _ooxml.link_numbering_styles(numbering, styles)
        assert _targets(numbering) == {"1": "1", "2": "2", "3": "3"}
        assert _ooxml.numbered_lists(numbering) == {"1"}

    def test_numbered_lists_need_levels(self) -> None:
        numbering = _numbering(
            _abstract("1", ""),
            _instance("1", "1"),
            _instance("2", "9"),
            _instance("3", "9", '<w:lvlOverride w:ilvl="0"><w:lvl w:ilvl="0"/></w:lvlOverride>'),
        )

        assert _ooxml.numbered_lists(numbering) == {"3"}

    def test_list_items_need_a_defined_nonzero_list(self) -> None:
        styles = {
            "ListBullet": word_style("ListBullet", number="1"),
            "Point": word_style("Point", based_on="ListBullet"),
        }
        cases = {
            "defined list": (numbered("Item"), True),
            "undefined list": (numbered("Item", number="9"), False),
            "numbering removed": (numbered("Item", number="0"), False),
            "inherited style numbering": (paragraph("Item", "Point"), True),
            "plain paragraph": (paragraph("Item"), False),
        }
        for name, (node, expected) in cases.items():
            with self.subTest(name):
                assert _ooxml.is_list_item(node, styles, {"0", "1"}) is expected
        with pytest.raises(ConversionError):
            _ooxml.is_list_item(numbered("Item", number="x"), styles, {"1"})

    def test_numbering_references_include_removed_and_undefined_lists(self) -> None:
        styles = {
            "ListBullet": word_style("ListBullet", number="4"),
            "Point": word_style("Point", based_on="ListBullet"),
        }
        cases = {
            "defined list": (numbered("Item"), "1"),
            "undefined list": (numbered("Item", number="9"), "9"),
            "numbering removed": (numbered("Item", number="0"), "0"),
            "inherited style numbering": (paragraph("Item", "Point"), "4"),
            "plain paragraph": (paragraph("Item"), None),
        }
        for name, (node, expected) in cases.items():
            with self.subTest(name):
                assert _ooxml.numbering_reference(node, styles) == expected

    def test_pandoc_headings_are_named_heading_n_in_the_style_chain(self) -> None:
        def style(identity: str, name: str, based_on: str | None = None) -> ET.Element:
            result = word_style(identity, based_on=based_on)
            label = result.find(W + "name")
            assert label is not None
            label.set(W + "val", name)
            return result

        styles = {
            "Heading1": style("Heading1", "heading 1"),
            "TOCHeading": style("TOCHeading", "TOC Heading", "Heading1"),
            "Deep": style("Deep", "Heading 10"),
            "Zero": style("Zero", "heading 0"),
            "Suffixed": style("Suffixed", "heading 1 char"),
            "Loop": style("Loop", "Loop", "Loop"),
            "Unnamed": word_style("heading 2"),
        }
        cases = {
            "heading style": ("Heading1", True),
            "based on a heading": ("TOCHeading", True),
            "any positive level": ("Deep", True),
            "level zero": ("Zero", False),
            "trailing text": ("Suffixed", False),
            "cyclic chain": ("Loop", False),
            "unknown style": ("Missing", False),
        }
        for name, (identity, expected) in cases.items():
            with self.subTest(name):
                assert _ooxml.pandoc_heading(paragraph("Text", identity), styles) is expected
        unnamed = styles.pop("Unnamed")
        label = unnamed.find(W + "name")
        assert label is not None
        unnamed.remove(label)
        styles["heading 2"] = unnamed
        assert _ooxml.pandoc_heading(paragraph("Text", "heading 2"), styles)
        assert not _ooxml.pandoc_heading(paragraph("Text"), styles)


class SymbolFontTests(unittest.TestCase):
    def test_symbol_font_text_and_symbol_characters_become_unicode(self) -> None:
        def run(text: str, font: str | None = None) -> ET.Element:
            result = _ooxml.word_run(text)
            if font is not None:
                properties = ET.Element(W + "rPr")
                ET.SubElement(properties, W + "rFonts", {W + "ascii": font, W + "hAnsi": font})
                result.insert(0, properties)
            return result

        def symbol(font: str, char: str) -> ET.Element:
            result = ET.Element(W + "r")
            ET.SubElement(result, W + "sym", {W + "font": font, W + "char": char})
            return result

        content = ET.Element(W + "p")
        content.extend(
            [
                run("ü", "Wingdings"),
                run("\uf0fc", "Wingdings"),
                run("a\u00ae ", "Symbol"),
                run("plain ü", "Calibri"),
                run("ü"),
                symbol("Wingdings", "F0FC"),
                symbol("Wingdings", "00FF"),
                symbol("Marlett", "0061"),
            ]
        )

        # Every character of a symbol-font run is a symbol, including the space.
        assert _ooxml.map_symbol_fonts(content) == (6, 1)

        assert _ooxml.word_text(content) == "✓✓\u03b1→ plain üü✓ÿ"
        fonts = [node.attrib for node in content.iter(W + "rFonts")]
        assert fonts[:3] == [{}, {}, {}]
        assert fonts[3] == {W + "ascii": "Calibri", W + "hAnsi": "Calibri"}
        assert len(list(content.iter(W + "sym"))) == 1


def _deleted_mark(node: ET.Element) -> ET.Element:
    """Mark the paragraph mark of *node* as deleted with Track Changes."""
    properties = node.find(W + "pPr")
    if properties is None:
        properties = ET.Element(W + "pPr")
        node.insert(0, properties)
    ET.SubElement(ET.SubElement(properties, W + "rPr"), W + "del", {W + "id": "1"})
    return node


def _deleted_item() -> ET.Element:
    """A list item whose text and paragraph mark were deleted with Track Changes."""
    item = numbered("")
    run = item.find(W + "r")
    assert run is not None
    item.remove(run)
    removed = ET.SubElement(item, W + "del", {W + "id": "2"})
    ET.SubElement(ET.SubElement(removed, W + "r"), W + "delText").text = "Removed"
    return _deleted_mark(item)


def _deleted_row(text: str) -> ET.Element:
    result = row(cell(text))
    properties = ET.Element(W + "trPr")
    ET.SubElement(properties, W + "del", {W + "id": "3"})
    result.insert(0, properties)
    return result


def _accepted(*children: ET.Element) -> ET.Element:
    body = ET.Element(W + "body")
    body.extend(children)
    _ooxml.accept_view(body)
    return body


def _paragraph_texts(body: ET.Element) -> list[str]:
    return [_ooxml.word_text(node) for node in body if node.tag == W + "p"]


class AcceptViewTests(unittest.TestCase):
    def test_a_deleted_paragraph_mark_joins_the_next_paragraph(self) -> None:
        body = _accepted(
            _deleted_mark(paragraph("Kept ")), paragraph("text", "Heading2"), paragraph("After")
        )
        assert _paragraph_texts(body) == ["Kept text", "After"]
        # Word keeps the properties of the paragraph whose mark remains.
        style = body[0].find(f"./{W}pPr/{W}pStyle")
        assert style is not None
        assert style.get(W + "val") == "Heading2"

    def test_consecutive_deleted_marks_join_into_one_paragraph(self) -> None:
        body = _accepted(
            _deleted_mark(paragraph("A")), _deleted_mark(paragraph("B")), paragraph("C")
        )
        assert _paragraph_texts(body) == ["ABC"]

    def test_a_deleted_list_item_leaves_no_empty_item(self) -> None:
        body = _accepted(numbered("one"), _deleted_item(), numbered("two"))
        assert _paragraph_texts(body) == ["one", "two"]
        numbers = [_ooxml.numbering_reference(node, {}) for node in body]
        assert numbers == ["1", "1"]

    def test_an_empty_item_with_nothing_to_join_loses_its_numbering(self) -> None:
        body = _accepted(numbered("one"), _deleted_item(), table(row(cell("x")), columns=1))
        assert _paragraph_texts(body) == ["one", ""]
        assert _ooxml.numbering_reference(body[1], {}) == "0"
        # Text with nothing to join keeps its numbering.
        body = _accepted(_deleted_mark(numbered("kept")), table(row(cell("x")), columns=1))
        assert _ooxml.numbering_reference(body[0], {}) == "1"

    def test_tables_whose_rows_were_all_deleted_are_removed(self) -> None:
        nested = cell(table(_deleted_row("inner"), columns=1))
        body = _accepted(
            paragraph("Before"),
            table(_deleted_row("a"), _deleted_row("b"), columns=1),
            table(row(cell("kept")), _deleted_row("c"), columns=1),
            table(row(nested), columns=1),
            table(columns=1),
            paragraph("After"),
        )
        tables = [node for node in body if node.tag == W + "tbl"]
        # A table without rows in Word stays as it was.
        assert [len(node.findall(W + "tr")) for node in tables] == [1, 1, 0]
        assert _ooxml.word_text(tables[0]) == "kept"
        # A cell keeps a paragraph when its only table goes.
        inner = tables[1].find(f"./{W}tr/{W}tc")
        assert inner is not None
        assert inner.find(W + "tbl") is None
        assert inner.find(W + "p") is not None


def _run(text: str, *, hidden: str | None = None, style: str | None = None) -> ET.Element:
    """A run; *hidden* is its w:vanish value ("" for none), *style* its character style."""
    result = _ooxml.word_run(text)
    properties = ET.Element(W + "rPr")
    if style is not None:
        ET.SubElement(properties, W + "rStyle", {W + "val": style})
    if hidden is not None:
        ET.SubElement(properties, W + "vanish", {W + "val": hidden} if hidden else {})
    if len(properties):
        result.insert(0, properties)
    return result


def _styled(style: str | None, *runs: ET.Element) -> ET.Element:
    result = ET.Element(W + "p")
    if style is not None:
        ET.SubElement(ET.SubElement(result, W + "pPr"), W + "pStyle", {W + "val": style})
    result.extend(runs)
    return result


def _hiding_styles(*, by_default: bool = False) -> ET.Element:
    styles = ET.Element(W + "styles")
    if by_default:
        defaults = ET.SubElement(ET.SubElement(styles, W + "docDefaults"), W + "rPrDefault")
        ET.SubElement(ET.SubElement(defaults, W + "rPr"), W + "vanish")
    note = word_style("Note", "character")
    ET.SubElement(ET.SubElement(note, W + "rPr"), W + "vanish")
    instructions = word_style("Instructions")
    ET.SubElement(ET.SubElement(instructions, W + "rPr"), W + "vanish")
    normal = word_style("Normal")
    normal.set(W + "default", "1")
    styles.extend(
        [note, word_style("SideNote", "character", based_on="Note"), instructions, normal]
    )
    return styles


class HiddenTextTests(unittest.TestCase):
    def test_the_nearest_setting_hides_a_run(self) -> None:
        body = ET.Element(W + "body")
        body.extend(
            [
                _styled(None, _run("Shown "), _run("direct", hidden=""), _run(" kept", hidden="0")),
                _styled(
                    None,
                    _run("by style", style="Note"),
                    _run(" based on it", style="SideNote"),
                    _run(" overridden", style="Note", hidden="false"),
                ),
                _styled("Instructions", _run("whole paragraph")),
                _styled("Instructions", _run("shown again", hidden="0")),
            ]
        )
        assert _ooxml.drop_hidden_text(body, _hiding_styles()) == 4
        assert [_ooxml.word_text(node) for node in body] == [
            "Shown  kept",
            " overridden",
            "",
            "shown again",
        ]
        # A run left with nothing but its properties is removed.
        assert len(body[0].findall(W + "r")) == 2

    def test_document_defaults_apply_and_field_characters_stay(self) -> None:
        field = ET.Element(W + "r")
        ET.SubElement(ET.SubElement(field, W + "rPr"), W + "vanish")
        ET.SubElement(field, W + "fldChar", {W + "fldCharType": "begin"})
        body = ET.Element(W + "body")
        body.extend(
            [_styled(None, _run("hidden"), _run("visible", hidden="0")), _styled(None, field)]
        )
        assert _ooxml.drop_hidden_text(body, _hiding_styles(by_default=True)) == 1
        assert _ooxml.word_text(body) == "visible"
        assert next(body.iter(W + "fldChar"), None) is not None


class HeadingPunctuationTests(unittest.TestCase):
    def test_trailing_punctuation_goes_across_runs(self) -> None:
        cases = {
            ("Overview", ":"): "Overview",
            ("Notes", " .", ": "): "Notes",
            ("Why?",): "Why?",
            ("<Mode> &copy;", "."): "<Mode> &copy;",
            ("A &#169;;",): "A &#169;",
            ("\u6982\u8981\uff1a",): "\u6982\u8981",
            ("...",): "...",
        }
        for parts, expected in cases.items():
            with self.subTest(parts=parts):
                node = ET.Element(W + "p")
                node.extend(_ooxml.word_run(part) for part in parts)
                changed = _ooxml.trim_trailing_punctuation(node)
                assert _ooxml.word_text(node) == expected
                assert changed == (expected != "".join(parts))


def _fonts(**attributes: str) -> ET.Element:
    """Run properties with a w:rFonts of *attributes*, such as ascii="Consolas"."""
    properties = ET.Element(W + "rPr")
    fonts = {W + name: value for name, value in attributes.items()}
    ET.SubElement(properties, W + "rFonts", fonts)
    return properties


class CodeTextTests(unittest.TestCase):
    def test_the_nearest_latin_font_decides_whether_text_is_monospace(self) -> None:
        code = word_style("Code")
        code.append(_fonts(ascii="Consolas"))
        verbatim = word_style("Verbatim", "character")
        verbatim.append(_fonts(hAnsi="Courier New"))
        normal = word_style("Normal")
        normal.set(W + "default", "1")
        normal.append(_fonts(asciiTheme="minorHAnsi"))
        styles = ET.Element(W + "styles")
        styles.extend([code, word_style("Listing", based_on="Code"), verbatim, normal])
        properties = _ooxml.RunProperties(styles)
        plain, listing = _styled(None), _styled("Listing")
        cases = {
            "own font": (_fonts(ascii="Courier New"), plain, True),
            "own font over the paragraph style": (_fonts(ascii="Arial"), listing, False),
            "East Asian font only": (_fonts(eastAsia="MS Gothic"), listing, True),
            "paragraph style it is based on": (None, listing, True),
            "theme font of the default style": (None, plain, False),
            "character style": (_run("x", style="Verbatim").find(W + "rPr"), plain, True),
            "theme font over a named one": (
                _fonts(ascii="Consolas", asciiTheme="minorHAnsi"),
                plain,
                False,
            ),
        }
        for name, (own, paragraph_node, expected) in cases.items():
            with self.subTest(name):
                assert properties.monospace(own, paragraph_node) == expected
        defaults = ET.Element(W + "styles")
        ET.SubElement(ET.SubElement(defaults, W + "docDefaults"), W + "rPrDefault").append(
            _fonts(ascii="Lucida Console")
        )
        assert _ooxml.RunProperties(defaults).monospace(None, plain)

    def test_code_text_keeps_layout_and_loses_word_typography(self) -> None:
        run = _ooxml.word_run("if (a == \u201cb\u201d) {")
        ET.SubElement(run, W + "br")
        ET.SubElement(run, W + "tab")
        ET.SubElement(run, W + "t").text = "x\u2013\u2014y\u2026"
        ET.SubElement(run, W + "noBreakHyphen")
        text = _ooxml.code_text(_styled(None, run))
        assert text == "if (a == “b”) {\n    x\u2013\u2014y…-"
        assert _ooxml.straighten_code(text) == ('if (a == "b") {\n    x---y...-', 5)


class UnicodeSpaceTests(unittest.TestCase):
    def spaces(self, *paragraphs: ET.Element) -> tuple[int, list[str]]:
        body = ET.Element(W + "body")
        body.extend(paragraphs)
        styles = ET.Element(W + "styles")
        styles.append(word_style("Heading1"))
        changed = _ooxml.plain_spaces(body, styles)
        return changed, [_ooxml.word_text(node) for node in body]

    def test_unicode_spaces_become_spaces_and_zero_width_spaces_go(self) -> None:
        body = ET.Element(W + "body")
        instruction = ET.SubElement(ET.SubElement(body, W + "r"), W + "instrText")
        instruction.text = "\u00a0PAGE"
        body.append(_styled(None, _run("100\u00a0MHz"), _run("\u00a0\u2002end\u200b.")))
        assert _ooxml.plain_spaces(body, ET.Element(W + "styles")) == 4
        assert _ooxml.word_text(body) == "100 MHz  end."
        edge = body[1][1].find(W + "t")
        assert edge is not None
        assert edge.get(_ooxml.XML_SPACE) == "preserve"
        assert instruction.text == "\xa0PAGE"

    def test_spaces_that_indent_a_line_become_no_break_spaces(self) -> None:
        tabbed = _styled(None, _run(""))
        run = tabbed[0]
        run.insert(0, ET.Element(W + "tab"))
        text = run.find(W + "t")
        assert text is not None
        text.text = "\u00a0after a tab"
        broken = _styled(None, _run("line one"))
        ET.SubElement(broken[0], W + "br")
        ET.SubElement(broken[0], W + "t").text = "\u00a0 \u2002line two"
        changed, texts = self.spaces(
            _styled(None, _run("\u00a0 "), _run("\u00a0nested {")),
            _styled(None, _run("\u2002\u2002en indent")),
            _styled(None, _run("\u00a0one is not an indent")),
            _styled(None, _run("   plain spaces are not kept")),
            _styled("Heading1", _run("\u00a0\u00a0Heading")),
            tabbed,
            broken,
            _styled(None, _run("\u00a0\u00a0")),
        )
        assert texts == [
            "\xa0\xa0\xa0nested {",
            "\xa0\xa0en indent",
            " one is not an indent",
            "   plain spaces are not kept",
            "  Heading",
            "\xa0after a tab",
            "line one\xa0\xa0\xa0line two",
            "  ",
        ]
        assert changed == 10

    def test_code_text_writes_unicode_spaces_as_spaces(self) -> None:
        code = _styled(None, _run("\u00a0\u00a0\u2002\u202fx\u00a0=\u20021;"))
        assert _ooxml.code_text(code) == "    x = 1;"


def _first_run(node: ET.Element) -> ET.Element:
    return next(run for run in node.iter(W + "r") if run.find(W + "t") is not None)


class UnderlineTests(unittest.TestCase):
    def test_underline_is_dropped_only_where_markdown_shows_it_twice_or_as_text(self) -> None:
        styles = ET.Element(W + "styles")
        link_style = word_style("Hyperlink", "character")
        ET.SubElement(ET.SubElement(link_style, W + "rPr"), W + "u", {W + "val": "single"})
        styles.append(link_style)
        body = paragraph("Body ")
        emphasis = formatted(_ooxml.word_run("kept"), "u", "double")
        body.append(emphasis)
        styled = hyperlink("Styled")
        styled_run = _first_run(styled)
        properties = ET.Element(W + "rPr")
        ET.SubElement(properties, W + "rStyle", {W + "val": "Hyperlink"})
        styled_run.insert(0, properties)
        direct = hyperlink("Direct")
        direct_run = formatted(_first_run(direct), "u", "single")
        valueless = hyperlink("Valueless")
        valueless_run = formatted(_first_run(valueless), "u")
        heading = paragraph("Heading", "Heading2")
        heading_run = formatted(_first_run(heading), "u", "single")
        boxed = text_box("In a box")
        box_run = formatted(_first_run(boxed), "u", "single")
        heading.append(boxed[0])
        code = formatted(code_run("init()"), "u", "single")
        code_paragraph = ET.Element(W + "p")
        code_paragraph.append(code)
        root = ET.Element(W + "document")
        ET.SubElement(root, W + "body").extend(
            [body, styled, direct, valueless, heading, code_paragraph]
        )

        assert _ooxml.drop_redundant_underline(root, styles) == 4
        values = {
            name: run.find(f"./{W}rPr/{W}u")
            for name, run in {
                "emphasis": emphasis,
                "styled": styled_run,
                "direct": direct_run,
                "valueless": valueless_run,
                "heading": heading_run,
                "box": box_run,
                "code": code,
            }.items()
        }
        assert {
            name: None if node is None else node.get(W + "val") for name, node in values.items()
        } == {
            "emphasis": "double",
            "styled": "none",
            "direct": "none",
            "valueless": None,
            "heading": "none",
            # A text box in a heading is a paragraph of its own.
            "box": "single",
            "code": "none",
        }
        assert values["valueless"] is not None
        assert _ooxml.drop_redundant_underline(root, styles) == 0
