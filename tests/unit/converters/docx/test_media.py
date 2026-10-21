"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from tests.support.docx import (
    SVG,
    W,
    cell,
    image,
    paragraph,
    row,
    synthetic_emf,
    table,
)

# LibreOffice exports an EMF as a whole Letter page with the drawing in the middle.
LIBREOFFICE_PAGE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg version="1.2" width="215.9mm" height="279.4mm" viewBox="0 0 21590 27940" '
    'stroke-width="28.222" xmlns="http://www.w3.org/2000/svg">\n'
    '<g class="Page"><g class="Graphic"><g id="id3">'
    '<rect class="BoundingBox" stroke="none" fill="none" x="5937" y="8142" width="9716" '
    'height="11655"/><path d="M 6000 8200 L 15000 19000"/></g></g></g>\n</svg>\n'
)
CAIRO_PAGE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="612pt" height="792pt" viewBox="0 0 612 792">'
    '\n<path d="M 300 390 L 310 400"/>\n</svg>\n'
)
ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"'
)


def _write_odg(path: Path) -> None:
    """An ODG export of a 1 cm drawing on a Letter page, beside an unused A4 page layout."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "content.xml",
            f"<office:document-content {ODF}><office:body><office:drawing>"
            '<draw:page draw:name="page1" draw:master-page-name="Default">'
            '<draw:frame svg:width="1cm" svg:height="1cm" svg:x="10.294cm" svg:y="13.469cm">'
            "<draw:image/></draw:frame></draw:page></office:drawing></office:body>"
            "</office:document-content>",
        )
        archive.writestr(
            "styles.xml",
            f"<office:document-styles {ODF}><office:automatic-styles>"
            '<style:page-layout style:name="PM0">'
            '<style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm"/>'
            '</style:page-layout><style:page-layout style:name="PM1">'
            '<style:page-layout-properties fo:page-width="21.59cm" fo:page-height="27.94cm"/>'
            "</style:page-layout></office:automatic-styles><office:master-styles>"
            '<style:master-page style:name="Default" style:page-layout-name="PM1"/>'
            "</office:master-styles></office:document-styles>",
        )


def _convert_with(
    outputs: dict[str, str | None], *, odg: bool = True
) -> tuple[str, list[str], list[list[str]]]:
    """Run _convert_emf with LibreOffice and pdftocairo mocked: *outputs* maps "svg" (the
    direct export) and "cairo" to the SVG each writes, or None for none. Return the result,
    the findings, and the commands."""
    calls: list[list[str]] = []
    findings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="docx-emf-crop-") as directory:
        root = Path(directory)
        slot = root / "slot"

        def command(args: list[str], description: str) -> str:
            calls.append(args)
            written = {
                "svg": (slot / "source.svg", outputs.get("svg")),
                "pdf": (slot / "source.pdf", "synthetic mocked PDF"),
                "-svg": (slot / "source.svg", outputs.get("cairo")),
            }
            kind = next((name for name in ("-svg", "svg", "pdf", "odg") if name in args), "")
            if kind == "odg":
                if odg:
                    _write_odg(slot / "source.odg")
            elif written[kind][1] is not None:
                written[kind][0].write_text(str(written[kind][1]), encoding="utf-8")
            return ""

        def which(name: str) -> str:
            return "/test/" + name

        with (
            patch.object(shutil, "which", which),
            patch.object(_reference, "run_command", command),
        ):
            _media._convert_emf(synthetic_emf(), root / "out.svg", slot, findings)
        return (root / "out.svg").read_text(encoding="utf-8"), findings, calls


class EmfCropTests(unittest.TestCase):
    def test_libreoffice_page_is_cropped_to_the_drawing(self) -> None:
        result, findings, calls = _convert_with({"svg": LIBREOFFICE_PAGE})
        self.assertEqual(
            result,
            LIBREOFFICE_PAGE.replace(
                'width="215.9mm" height="279.4mm" viewBox="0 0 21590 27940"',
                'width="97.16mm" height="116.55mm" viewBox="5937 8142 9716 11655"',
            ),
        )
        self.assertEqual(findings, [])
        self.assertEqual(len(calls), 1)

    def test_fallback_page_is_cropped_to_the_frame_of_an_odg_export(self) -> None:
        result, findings, calls = _convert_with({"svg": None, "cairo": CAIRO_PAGE})
        # The frame is at 10.294 cm, 13.469 cm on a 21.59 cm by 27.94 cm page of 612 by 792 pt.
        self.assertEqual(
            result,
            CAIRO_PAGE.replace(
                'width="612pt" height="792pt" viewBox="0 0 612 792"',
                'width="28.3465pt" height="28.3465pt" viewBox="291.7984 381.7984 28.3465 28.3465"',
            ),
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("trying the PDF/pdftocairo vector fallback", findings[0])
        self.assertEqual(
            [call[call.index("--convert-to") + 1] for call in calls[::3]], ["svg", "odg"]
        )

    def test_page_is_kept_whole_when_the_drawing_frame_is_unknown(self) -> None:
        two = LIBREOFFICE_PAGE.replace(
            "<path", '<rect class="BoundingBox" x="1" y="1" width="1" height="1"/><path'
        )
        off_page = LIBREOFFICE_PAGE.replace('x="5937"', 'x="21000"')
        cases: dict[str, tuple[dict[str, str | None], bool, str]] = {
            "LibreOffice did not mark one drawing on it": ({"svg": two}, True, two),
            "the drawing's frame is not on the page": ({"svg": off_page}, True, off_page),
            "LibreOffice did not report the drawing's frame": (
                {"svg": None, "cairo": CAIRO_PAGE},
                False,
                CAIRO_PAGE,
            ),
        }
        for reason, (outputs, odg, expected) in cases.items():
            with self.subTest(reason=reason):
                result, findings, _calls = _convert_with(outputs, odg=odg)
                self.assertEqual(result, expected)
                self.assertEqual(
                    findings[-1], f"kept LibreOffice's whole page around the drawing: {reason}"
                )


class MediaFailureTests(unittest.TestCase):
    def test_emf_pdf_fallback_is_used_only_when_svg_export_needs_it(self) -> None:
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory(prefix="docx-emf-fallback-") as directory:
            root = Path(directory)
            slot = root / "slot"
            destination = root / "output.svg"

            def command(args: list[str], description: str) -> str:
                calls.append(args)
                if "pdf" in args:
                    (slot / "source.pdf").write_bytes(b"synthetic mocked PDF")
                if "-svg" in args:
                    (slot / "source.svg").write_bytes(SVG)
                return ""

            def which(name: str) -> str | None:
                return "/test/" + name

            with (
                patch.object(shutil, "which", which),
                patch.object(_reference, "run_command", command),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                _media._convert_emf(synthetic_emf(), destination, slot, [])
            self.assertEqual(destination.read_bytes(), SVG)
        self.assertEqual(len(calls), 4)
        self.assertIn("svg", calls[0])
        self.assertIn("pdf", calls[1])
        self.assertIn("-svg", calls[2])
        self.assertIn("odg", calls[3])

    def test_missing_office_is_a_clear_conditional_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-emf-missing-") as directory:
            root = Path(directory)
            with (
                patch.object(shutil, "which", return_value=None),
                self.assertRaisesRegex(_models.ConversionError, "requires LibreOffice"),
            ):
                _media._convert_emf(synthetic_emf(), root / "out.svg", root / "slot", [])
            self.assertFalse((root / "out.svg").exists())

    def test_pdftocairo_is_not_required_after_successful_svg_export(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-emf-direct-") as directory:
            root = Path(directory)
            slot = root / "slot"

            def which(name: str) -> str | None:
                self.assertNotEqual(name, "pdftocairo")
                return "/test/office"

            def command(args: list[str], description: str) -> str:
                self.assertIn("svg", args)
                (slot / "source.svg").write_bytes(SVG)
                return ""

            with (
                patch.object(shutil, "which", which),
                patch.object(_reference, "run_command", command),
            ):
                _media._convert_emf(synthetic_emf(), root / "out.svg", slot, [])
            self.assertEqual((root / "out.svg").read_bytes(), SVG)

    def test_missing_cairo_is_reported_for_only_the_fallback_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-cairo-missing-") as directory:
            root = Path(directory)
            slot = root / "slot"

            def which(name: str) -> str | None:
                return None if name == "pdftocairo" else "/test/office"

            def command(args: list[str], description: str) -> str:
                if "pdf" in args:
                    (slot / "source.pdf").write_bytes(b"synthetic mocked PDF")
                return ""

            with (
                patch.object(shutil, "which", which),
                patch.object(_reference, "run_command", command),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaisesRegex(_models.ConversionError, "requires pdftocairo"),
            ):
                _media._convert_emf(synthetic_emf(), root / "out.svg", slot, [])
            self.assertFalse((root / "out.svg").exists())

    def test_svg_css_resource_fragments_must_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-svg-fragment-") as directory:
            path = Path(directory) / "source.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><path clip-path="url(#missing)"/></svg>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(_models.ConversionError, "Unresolved SVG resource"):
                _media.validate_svg(path)
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><defs><clipPath id="clip"/></defs>'
                '<path clip-path="url(#clip)"/></svg>',
                encoding="utf-8",
            )
            _media.validate_svg(path)


def _caption(*parts: str | tuple[str, str], style: str | None = "Caption") -> ET.Element:
    """A paragraph of text; an (instruction, result) pair is a Word field."""
    result = ET.Element(W + "p")
    if style is not None:
        ET.SubElement(ET.SubElement(result, W + "pPr"), W + "pStyle", {W + "val": style})
    for part in parts:
        if isinstance(part, tuple):
            for kind, text in (("begin", None), (None, part[0]), ("separate", None)):
                run = ET.SubElement(result, W + "r")
                if kind is None:
                    ET.SubElement(run, W + "instrText").text = text
                else:
                    ET.SubElement(run, W + "fldChar", {W + "fldCharType": kind})
            result.append(_ooxml.word_run(part[1]))
            ET.SubElement(ET.SubElement(result, W + "r"), W + "fldChar", {W + "fldCharType": "end"})
        elif part in {"\u2011", "\t"}:
            tag = "noBreakHyphen" if part == "\u2011" else "tab"
            ET.SubElement(ET.SubElement(result, W + "r"), W + tag)
        else:
            result.append(_ooxml.word_run(part))
    return result


def _figure(title: str) -> ET.Element:
    return _caption("Figure ", (" SEQ Figure \\* ARABIC ", "1"), title)


def _described(*content: ET.Element) -> list[tuple[str | None, bool]]:
    root = ET.Element(W + "document")
    ET.SubElement(root, W + "body").extend(content)
    return [
        (description.alt_text, description.from_caption)
        for description in _media.image_descriptions(root)
    ]


class ImageDescriptionTests(unittest.TestCase):
    def test_caption_title_wins_over_word_alt_text(self) -> None:
        caption = _caption(
            "Figure ",
            (" STYLEREF 1 \\s ", "14"),
            "\u2011",
            (" SEQ Figure \\* ARABIC \\s 1 ", "13"),
            ": Bus Interface",
        )
        generated = "A diagram of a computer. AI-generated content may be incorrect."
        self.assertEqual(_described(image(alt=generated), caption), [("Bus Interface", True)])

    def test_title_follows_the_number_with_any_separator(self) -> None:
        captions = {
            "Data flow": _caption("Figure 3", "\t", "Data flow"),
            "Pipeline": _caption("Figure ", (" SEQ Figure ", "6"), " : Pipeline", style=None),
            "USB\u2011to\u2011LAN bridge": _caption(
                "Figure 9", "\u2011", "13 \u2013 USB", "\u2011", "to", "\u2011", "LAN bridge"
            ),
            "Request flow": _caption("Figure 2-1: Request flow"),
            "Figure 5": _caption("Figure ", (" SEQ Figure ", "5")),
        }
        for title, caption in captions.items():
            with self.subTest(title=title):
                self.assertEqual(_described(image(alt=None), caption), [(title, True)])

    def test_an_image_between_two_captions_takes_the_side_most_captions_are_on(self) -> None:
        below = [image(alt=None), _figure(": One"), image(alt=None), _figure(": Two")]
        above = [_figure(": One"), image(alt=None), _figure(": Two"), image(alt=None)]
        expected = [("One", True), ("Two", True)]
        self.assertEqual(_described(*below), expected)
        self.assertEqual(_described(*above), expected)

    def test_a_caption_on_the_less_common_side_is_still_used(self) -> None:
        content = [
            image(alt=None),
            _figure(": One"),
            image(alt=None),
            _figure(": Two"),
            paragraph("Text"),
            _figure(": Three"),
            image(alt=None),
        ]
        self.assertEqual(_described(*content), [("One", True), ("Two", True), ("Three", True)])

    def test_a_caption_describes_one_image(self) -> None:
        self.assertEqual(
            _described(image(alt=None), _figure(": Shared"), image(alt="Word")),
            [("Shared", True), ("Word", False)],
        )

    def test_table_and_equation_captions_do_not_describe_images(self) -> None:
        for caption in (
            _caption("Table ", (" SEQ Table \\* ARABIC ", "2"), ": Registers"),
            _caption("Equation ", (" SEQ Equation ", "1"), style=None),
            _caption("Table 3: Registers"),
        ):
            with self.subTest(text=_ooxml.word_text(caption)):
                self.assertEqual(_described(image(), caption), [("Diagram", False)])

    def test_without_a_caption_word_alt_text_is_kept(self) -> None:
        self.assertEqual(
            _described(image(), paragraph("Body text"), image(alt=None)),
            [("Diagram", False), (None, False)],
        )


def _headed(*content: ET.Element) -> list[tuple[str | None, bool]]:
    """Alt text, and whether a heading gave it, with each Heading1 paragraph a heading."""
    root = ET.Element(W + "document")
    ET.SubElement(root, W + "body").extend(content)
    headings = {
        node
        for node in root.iter(W + "p")
        if any(style.get(W + "val") == "Heading1" for style in node.iter(W + "pStyle"))
    }
    return [
        (description.alt_text, description.from_heading)
        for description in _media.image_descriptions(root, headings=headings)
    ]


class HeadingAltTextTests(unittest.TestCase):
    def test_a_heading_describes_the_images_directly_below_it(self) -> None:
        pair = image(alt=None)
        pair.extend(image(alt=None).findall(W + "r"))
        self.assertEqual(
            _headed(
                _caption("1.2", "\t", "Block", "\u2011", "diagram", style="Heading1"),
                paragraph(""),
                image(alt=None),
                paragraph("Data flow", "Heading1"),
                pair,
            ),
            [("1.2 Block\u2011diagram", True), ("Data flow", True), ("Data flow", True)],
        )

    def test_only_a_heading_directly_above_in_the_same_container_counts(self) -> None:
        beside = image(alt=None)
        beside.append(_ooxml.word_run("See the diagram."))
        cases = {
            "text between": [paragraph("Body text"), image(alt=None)],
            "table between": [table(row(cell("x")), columns=1), image(alt=None)],
            "text beside the image": [beside],
            "image in a table": [table(row(cell(image(alt=None))), columns=1)],
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    _headed(paragraph("Heading", "Heading1"), *content)[-1], (None, False)
                )

    def test_a_caption_or_word_alt_text_wins_over_the_heading(self) -> None:
        self.assertEqual(
            _headed(
                paragraph("Heading", "Heading1"),
                image(alt=None),
                _figure(": Caption"),
                paragraph("Other", "Heading1"),
                image(alt="Word"),
            ),
            [("Caption", False), ("Word", False)],
        )
