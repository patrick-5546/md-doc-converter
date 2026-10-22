"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
import zipfile
from collections.abc import Collection
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from tests.support.docx import (
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


INVALID_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path clip-path="url(#none)"/></svg>'
# What each step writes for a drawing: "svg" (the direct export) and "cairo" an SVG or nothing,
# "pdf" and "odg" whether LibreOffice exports them.
Outputs = dict[str, str | bool | None]


class FakeTools:
    """LibreOffice and pdftocairo stand-ins that write each drawing's outputs and record calls."""

    def __init__(self, outputs: dict[str, Outputs], failing: str | None = None) -> None:
        self.outputs = outputs
        self.failing = failing
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], description: str, *, quiet: bool = False) -> str:
        assert quiet, "drawing conversions must not show the tools' warnings on success"
        self.calls.append(args)
        if args[1] == "-svg":
            written = self.outputs[Path(args[2]).stem].get("cairo")
            if isinstance(written, str):
                Path(args[3]).write_text(written, encoding="utf-8")
            return ""
        kind = args[args.index("--convert-to") + 1]
        folder = Path(args[args.index("--outdir") + 1])
        for source in args[args.index("--outdir") + 2 :]:
            name = Path(source).stem
            written = self.outputs[name].get(kind, kind != "svg")
            if kind == "svg" and isinstance(written, str):
                (folder / f"{name}.svg").write_text(written, encoding="utf-8")
            elif kind == "pdf" and written:
                (folder / f"{name}.pdf").write_bytes(b"synthetic mocked PDF")
            elif kind == "odg" and written:
                _write_odg(folder / f"{name}.odg")
        if kind == self.failing:
            raise _models.ConversionError(f"{description} failed: synthetic crash")
        return ""

    def kinds(self) -> list[str]:
        return [
            "cairo" if call[1] == "-svg" else call[call.index("--convert-to") + 1]
            for call in self.calls
        ]

    def inputs(self, index: int) -> list[str]:
        call = self.calls[index]
        return [Path(source).stem for source in call[call.index("--outdir") + 2 :]]


def _convert(
    outputs: dict[str, Outputs],
    *,
    missing: Collection[str] = (),
    failing: str | None = None,
) -> tuple[dict[str, tuple[str, list[str]]], FakeTools]:
    """Convert one synthetic drawing per name in *outputs* with the native tools mocked;
    *missing* names tools that are not installed. Return each result, as text, and the fakes."""
    tools = FakeTools(outputs, failing)

    def which(name: str) -> str | None:
        return None if name in missing else "/test/" + name

    drawings = {name: (synthetic_emf(), ".emf") for name in outputs}
    with (
        tempfile.TemporaryDirectory(prefix="docx-emf-") as directory,
        patch.object(shutil, "which", which),
        patch.object(_reference, "run_command", tools),
    ):
        results = _media._convert_vectors(drawings, Path(directory) / "work")
    decoded = {
        name: (result.decode() if isinstance(result, bytes) else result, findings)
        for name, (result, findings) in results.items()
    }
    return decoded, tools


CROPPED_PAGE = LIBREOFFICE_PAGE.replace(
    'width="215.9mm" height="279.4mm" viewBox="0 0 21590 27940"',
    'width="97.16mm" height="116.55mm" viewBox="5937 8142 9716 11655"',
)
# The ODG frame is at 10.294 cm, 13.469 cm on a 21.59 cm by 27.94 cm page of 612 by 792 pt.
CROPPED_CAIRO_PAGE = CAIRO_PAGE.replace(
    'width="612pt" height="792pt" viewBox="0 0 612 792"',
    'width="28.3465pt" height="28.3465pt" viewBox="291.7984 381.7984 28.3465 28.3465"',
)


class EmfCropTests(unittest.TestCase):
    def test_libreoffice_page_is_cropped_to_the_drawing(self) -> None:
        results, tools = _convert({"drawing": {"svg": LIBREOFFICE_PAGE}})
        assert results == {"drawing": (CROPPED_PAGE, [])}
        assert tools.kinds() == ["svg"]

    def test_fallback_page_is_cropped_to_the_frame_of_an_odg_export(self) -> None:
        results, tools = _convert({"drawing": {"svg": None, "cairo": CAIRO_PAGE}})
        result, findings = results["drawing"]
        assert result == CROPPED_CAIRO_PAGE
        assert len(findings) == 1
        assert "trying the PDF/pdftocairo vector fallback" in findings[0]
        assert tools.kinds() == ["svg", "pdf", "cairo", "odg"]

    def test_page_is_kept_whole_when_the_drawing_frame_is_unknown(self) -> None:
        two = LIBREOFFICE_PAGE.replace(
            "<path", '<rect class="BoundingBox" x="1" y="1" width="1" height="1"/><path'
        )
        off_page = LIBREOFFICE_PAGE.replace('x="5937"', 'x="21000"')
        cases: dict[str, tuple[Outputs, str]] = {
            "LibreOffice did not mark one drawing on it": ({"svg": two}, two),
            "the drawing's frame is not on the page": ({"svg": off_page}, off_page),
            "LibreOffice did not report the drawing's frame": (
                {"svg": None, "cairo": CAIRO_PAGE, "odg": False},
                CAIRO_PAGE,
            ),
        }
        for reason, (outputs, expected) in cases.items():
            with self.subTest(reason=reason):
                results, _tools = _convert({"drawing": outputs})
                result, findings = results["drawing"]
                assert result == expected
                assert findings[-1] == f"kept LibreOffice's whole page around the drawing: {reason}"

    def test_a_unitless_pdftocairo_page_is_measured_in_points(self) -> None:
        # Poppler before 25.04 with Cairo 1.17.6 or newer writes the page size without a unit.
        unitless = CAIRO_PAGE.replace('width="612pt" height="792pt"', 'width="612" height="792"')
        cropped, _tools = _convert({"drawing": {"svg": None, "cairo": unitless}})
        assert cropped["drawing"][0] == CROPPED_CAIRO_PAGE
        whole, _tools = _convert({"drawing": {"svg": None, "cairo": unitless, "odg": False}})
        assert whole["drawing"][0] == CAIRO_PAGE

    def test_only_a_unitless_page_size_gets_points(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-emf-units-") as directory:
            path = Path(directory) / "page.svg"
            for before, after in (
                (
                    '<svg width="612" height="1e3" stroke-width="2">',
                    '<svg width="612pt" height="1e3pt" stroke-width="2">',
                ),
                ('<svg width="612pt" height="10mm">', '<svg width="612pt" height="10mm">'),
                (
                    "<svg height='79.5' viewBox='0 0 1 1'>",
                    "<svg height='79.5pt' viewBox='0 0 1 1'>",
                ),
            ):
                with self.subTest(before=before):
                    path.write_text(f"<?xml version='1.0'?>\n{before}<g width='5'/></svg>\n")
                    _media._size_in_points(path)
                    assert (
                        path.read_text() == f"<?xml version='1.0'?>\n{after}<g width='5'/></svg>\n"
                    )


class EmfBatchTests(unittest.TestCase):
    def test_all_drawings_convert_in_one_libreoffice_run(self) -> None:
        results, tools = _convert({name: {"svg": LIBREOFFICE_PAGE} for name in ("a", "b", "c")})
        assert results == {name: (CROPPED_PAGE, []) for name in ("a", "b", "c")}
        assert tools.kinds() == ["svg"]
        assert tools.inputs(0) == ["a", "b", "c"]

    def test_only_drawings_that_svg_export_missed_take_the_fallback_together(self) -> None:
        results, tools = _convert(
            {
                "a": {"svg": LIBREOFFICE_PAGE},
                "b": {"svg": None, "cairo": CAIRO_PAGE},
                "c": {"svg": INVALID_SVG, "cairo": CAIRO_PAGE},
            }
        )
        assert results["a"] == (CROPPED_PAGE, [])
        for name, reason in (
            ("b", "LibreOffice SVG export produced no nonempty SVG"),
            ("c", "Unresolved SVG resource"),
        ):
            result, findings = results[name]
            assert result == CROPPED_CAIRO_PAGE
            [finding] = findings
            assert reason in finding
            assert finding.endswith("; trying the PDF/pdftocairo vector fallback")
        assert tools.kinds() == ["svg", "pdf", "cairo", "cairo", "odg"]
        assert [tools.inputs(index) for index in (0, 1, 4)] == [
            ["a", "b", "c"],
            ["b", "c"],
            ["b", "c"],
        ]
        profiles = {call[1] for call in tools.calls if call[1] != "-svg"}
        assert len(profiles) == 1

    def test_a_failed_fallback_affects_only_its_drawing(self) -> None:
        results, tools = _convert(
            {
                "a": {"svg": None, "pdf": False},
                "b": {"svg": None, "cairo": CAIRO_PAGE},
            }
        )
        result, findings = results["a"]
        assert re.search(r"; EMF fallback did not produce .*a\.pdf$", result)
        assert len(findings) == 1
        assert results["b"][0] == CROPPED_CAIRO_PAGE
        assert tools.kinds() == ["svg", "pdf", "cairo", "odg"]
        assert tools.inputs(3) == ["b"]

    def test_a_failed_run_keeps_the_drawings_it_exported(self) -> None:
        results, _tools = _convert(
            {
                "a": {"svg": LIBREOFFICE_PAGE},
                "b": {"svg": None, "cairo": CAIRO_PAGE},
            },
            failing="svg",
        )
        assert results["a"] == (CROPPED_PAGE, [])
        result, [finding] = results["b"]
        assert result == CROPPED_CAIRO_PAGE
        assert (
            finding == "EMF-to-SVG conversion failed: synthetic crash; "
            "trying the PDF/pdftocairo vector fallback"
        )


class MediaFailureTests(unittest.TestCase):
    def test_missing_office_is_a_clear_conditional_failure(self) -> None:
        results, tools = _convert(
            {"a": {"svg": LIBREOFFICE_PAGE}, "b": {}}, missing=("libreoffice", "soffice")
        )
        for result, findings in results.values():
            assert "requires LibreOffice" in result
            assert findings == []
        assert tools.calls == []

    def test_pdftocairo_is_needed_only_by_the_fallback(self) -> None:
        results, tools = _convert(
            {"a": {"svg": LIBREOFFICE_PAGE}, "b": {"svg": None, "cairo": CAIRO_PAGE}},
            missing=("pdftocairo",),
        )
        assert results["a"] == (CROPPED_PAGE, [])
        assert "this EMF requires pdftocairo for PDF-to-SVG" in results["b"][0]
        assert tools.kinds() == ["svg"]

    def test_svg_css_resource_fragments_must_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-svg-fragment-") as directory:
            path = Path(directory) / "source.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><path clip-path="url(#missing)"/></svg>',
                encoding="utf-8",
            )
            with pytest.raises(_models.ConversionError, match="Unresolved SVG resource"):
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
        assert _described(image(alt=generated), caption) == [("Bus Interface", True)]

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
                assert _described(image(alt=None), caption) == [(title, True)]

    def test_an_image_between_two_captions_takes_the_side_most_captions_are_on(self) -> None:
        below = [image(alt=None), _figure(": One"), image(alt=None), _figure(": Two")]
        above = [_figure(": One"), image(alt=None), _figure(": Two"), image(alt=None)]
        expected = [("One", True), ("Two", True)]
        assert _described(*below) == expected
        assert _described(*above) == expected

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
        assert _described(*content) == [("One", True), ("Two", True), ("Three", True)]

    def test_a_caption_describes_one_image(self) -> None:
        assert _described(image(alt=None), _figure(": Shared"), image(alt="Word")) == [
            ("Shared", True),
            ("Word", False),
        ]

    def test_table_and_equation_captions_do_not_describe_images(self) -> None:
        for caption in (
            _caption("Table ", (" SEQ Table \\* ARABIC ", "2"), ": Registers"),
            _caption("Equation ", (" SEQ Equation ", "1"), style=None),
            _caption("Table 3: Registers"),
        ):
            with self.subTest(text=_ooxml.word_text(caption)):
                assert _described(image(), caption) == [("Diagram", False)]

    def test_without_a_caption_word_alt_text_is_kept(self) -> None:
        assert _described(image(), paragraph("Body text"), image(alt=None)) == [
            ("Diagram", False),
            (None, False),
        ]


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
        assert _headed(
            _caption("1.2", "\t", "Block", "\u2011", "diagram", style="Heading1"),
            paragraph(""),
            image(alt=None),
            paragraph("Data flow", "Heading1"),
            pair,
        ) == [("1.2 Block\u2011diagram", True), ("Data flow", True), ("Data flow", True)]

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
                assert _headed(paragraph("Heading", "Heading1"), *content)[-1] == (None, False)

    def test_a_caption_or_word_alt_text_wins_over_the_heading(self) -> None:
        assert _headed(
            paragraph("Heading", "Heading1"),
            image(alt=None),
            _figure(": Caption"),
            paragraph("Other", "Heading1"),
            image(alt="Word"),
        ) == [("Caption", False), ("Word", False)]
