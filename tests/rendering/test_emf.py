"""Real EMF conversion with LibreOffice and Poppler: cropping, one run, searchable text, and a
PNG for a drawing whose SVG is too large."""

from __future__ import annotations

import re
import struct
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import pipeline as _pipeline
from md_doc_converter.converters.docx import reference as _reference
from tests.support.docx import bitmap_emf, image, synthetic_emf, text_emf, write_docx
from tests.support.suites import poppler

SOURCE = "Synthetic Guide.docx"
CLI = (sys.executable, "-m", "md_doc_converter")
SVG = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A Word document with one synthetic EMF drawing, diagram.emf."""
    path = tmp_path / SOURCE
    write_docx(
        path,
        [image()],
        relations=(("image", "image", "media/diagram.emf", "Internal"),),
        media={"diagram.emf": synthetic_emf()},
    )
    return path


def size_mm(svg: ET.Element) -> tuple[float, float]:
    """The width and height of a converted drawing, which pdftocairo gives in points."""
    sizes = []
    for name in ("width", "height"):
        match = re.fullmatch(r"([0-9.]+)pt", svg.get(name, ""))
        assert match is not None, svg.get(name)
        sizes.append(float(match[1]) * 25.4 / 72)
    return sizes[0], sizes[1]


def assert_cropped_to_the_drawing(source: Path, drawing: str = "diagram") -> None:
    """LibreOffice draws the synthetic EMF, 10 mm square, on a whole page."""
    path = source.parent / ".images" / source.stem / f"{drawing}.svg"
    assert size_mm(_media.validate_svg(path)) == pytest.approx((10, 10), abs=0.1)


def test_real_synthetic_emf_conversion(source: Path) -> None:
    stats = _pipeline.convert_docx(source)
    assert "diagram.svg" in source.with_suffix(".md").read_text(encoding="utf-8")
    assert stats["unique_images"] == 1
    assert_cropped_to_the_drawing(source)


def test_real_drawings_of_a_document_convert_in_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ("first", "second", "third")
    source = tmp_path / SOURCE
    write_docx(
        source,
        [image(name) for name in names],
        relations=tuple((name, "image", f"media/{name}.emf", "Internal") for name in names),
        media={f"{name}.emf": synthetic_emf(variant) for variant, name in enumerate(names)},
    )
    original = _reference.run_command
    descriptions: list[str] = []

    def record(
        command: list[str],
        description: str,
        *,
        input_text: str | None = None,
        quiet: bool = False,
    ) -> str:
        descriptions.append(description)
        return original(command, description, input_text=input_text, quiet=quiet)

    monkeypatch.setattr(_reference, "run_command", record)
    stats = _pipeline.convert_docx(source)
    assert stats["unique_images"] == 3
    assert [name for name in descriptions if name.startswith("EMF-to-")] == [
        "EMF-to-PDF conversion"
    ]
    for name in names:
        assert_cropped_to_the_drawing(source, name)


def test_real_drawing_text_stays_searchable(tmp_path: Path) -> None:
    source = tmp_path / SOURCE
    write_docx(
        source,
        [image()],
        relations=(("image", "image", "media/diagram.emf", "Internal"),),
        media={"diagram.emf": text_emf("Cache controller", "Request queue")},
    )
    _pipeline.convert_docx(source)
    svg = _media.validate_svg(tmp_path / ".images" / source.stem / "diagram.svg")
    assert size_mm(svg) == pytest.approx((60, 20), abs=0.1)
    # The text is drawn as outlines; its only text elements are the invisible copy.
    [layer] = [group for group in svg.iter(SVG + "g") if group.get("fill-opacity") == "0"]
    assert [node.text for node in layer.iter(SVG + "tspan")] == [
        "Cache",
        "controller",
        "Request",
        "queue",
    ]
    assert len(list(svg.iter(SVG + "text"))) == len(list(layer.iter(SVG + "text"))) == 2
    # Chrome prints the invisible words, so that a search of the PDF finds them.
    markdown = source.with_suffix(".md")
    subprocess.run([*CLI, "md-to-pdf", str(markdown)], check=True, capture_output=True)
    printed = subprocess.run(
        [poppler("pdftotext"), str(markdown.with_suffix(".pdf")), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    words = " ".join(printed.split())
    assert "Cache controller" in words
    assert "Request queue" in words


def test_real_drawings_that_libreoffice_cannot_export_as_svg_convert(tmp_path: Path) -> None:
    drawings = {
        "space": (text_emf("Cache controller", space_glyph=True), ".emf"),
        "bitmap": (bitmap_emf(), ".emf"),
    }
    results = _media._convert_vectors(drawings, tmp_path / "work")
    svgs: dict[str, ET.Element] = {}
    for name, (result, findings) in results.items():
        assert isinstance(result, bytes), result
        assert findings == []
        svgs[name] = ET.fromstring(result)
        # Each letter and picture is drawn where pdftocairo showed it with a <use>.
        assert not list(svgs[name].iter(SVG + "use"))
    assert size_mm(svgs["space"]) == pytest.approx((60, 20), abs=0.1)
    assert size_mm(svgs["bitmap"]) == pytest.approx((30, 15), abs=0.1)
    assert list(svgs["bitmap"].iter(SVG + "image"))


def test_real_drawing_whose_svg_is_too_large_is_a_png_of_the_drawing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every SVG is too large for these limits.
    monkeypatch.setattr(_media, "_LARGE_SVG", 0)
    monkeypatch.setattr(_media, "_SVG_GROWTH", 0)
    work = tmp_path / "work"
    [(png, findings)] = _media._convert_vectors(
        {"diagram": (synthetic_emf(), ".emf")}, work
    ).values()
    assert isinstance(png, bytes)
    assert png.startswith(_media.PNG_SIGNATURE)
    assert len(findings) == 1
    assert findings[0].startswith(_media._DRAWN_AS_PNG)
    # The IHDR chunk gives the width and height: the 10 mm drawing at 192 pixels per inch.
    assert struct.unpack(">II", png[16:24]) == (76, 76)
    # A part of the page as large without the drawing, at its top left, looks different.
    blank = tmp_path / "blank"
    subprocess.run(
        [
            *(poppler("pdftocairo"), "-png", "-singlefile", "-r", "192", "-W", "76", "-H", "76"),
            str(work / "pdf" / "diagram.pdf"),
            str(blank),
        ],
        check=True,
        capture_output=True,
    )
    assert png != blank.with_name("blank.png").read_bytes()
