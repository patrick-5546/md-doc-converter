"""Manual LibreOffice/Poppler EMF conversion regressions."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import pipeline as _pipeline
from md_doc_converter.converters.docx import reference as _reference
from tests.support.docx import image, synthetic_emf, write_docx

SOURCE = "Synthetic Guide.docx"


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


def assert_cropped_to_the_drawing(source: Path, drawing: str = "diagram") -> None:
    """LibreOffice draws the synthetic EMF, 10 mm square, on a whole page."""
    path = source.parent / ".images" / source.stem / f"{drawing}.svg"
    _media.validate_svg(path)
    root = ET.parse(path).getroot()
    for name in ("width", "height"):
        size = _media._length_mm(root.get(name))
        assert size is not None
        assert size == pytest.approx(10, abs=0.1)


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
    assert descriptions.count("EMF-to-SVG conversion") == 1
    assert "EMF-to-PDF fallback conversion" not in descriptions
    for name in names:
        assert_cropped_to_the_drawing(source, name)


def test_real_synthetic_emf_pdf_fallback(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = _reference.run_command

    def force_pdf(
        command: list[str],
        description: str,
        *,
        input_text: str | None = None,
        quiet: bool = False,
    ) -> str:
        if description == "EMF-to-SVG conversion":
            raise _models.ConversionError("Exercising real PDF fallback")
        return original(command, description, input_text=input_text, quiet=quiet)

    monkeypatch.setattr(_reference, "run_command", force_pdf)
    _pipeline.convert_docx(source)
    assert "diagram.svg" in source.with_suffix(".md").read_text(encoding="utf-8")
    assert_cropped_to_the_drawing(source)
