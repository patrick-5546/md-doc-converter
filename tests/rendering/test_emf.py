"""Manual LibreOffice/Poppler EMF conversion regressions."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from typing_extensions import override

from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import pipeline as _pipeline
from md_doc_converter.converters.docx import reference as _reference
from tests.support.docx import image, synthetic_emf, write_docx


class EmfWorkflowTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docx-emf-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "Synthetic Guide.docx"
        write_docx(
            self.source,
            [image()],
            relations=(("image", "image", "media/diagram.emf", "Internal"),),
            media={"diagram.emf": synthetic_emf()},
        )

    def assert_cropped_to_the_drawing(self, drawing: str = "diagram") -> None:
        """LibreOffice draws the synthetic EMF, 10 mm square, on a whole page."""
        path = self.root / ".images" / "Synthetic Guide" / f"{drawing}.svg"
        _media.validate_svg(path)
        root = ET.parse(path).getroot()
        for name in ("width", "height"):
            size = _media._length_mm(root.get(name))
            assert size is not None
            assert size == pytest.approx(10, abs=0.1)

    def test_real_synthetic_emf_conversion(self) -> None:
        stats = _pipeline.convert_docx(self.source)
        assert "diagram.svg" in self.source.with_suffix(".md").read_text(encoding="utf-8")
        assert stats["unique_images"] == 1
        self.assert_cropped_to_the_drawing()

    def test_real_drawings_of_a_document_convert_in_one_run(self) -> None:
        names = ("first", "second", "third")
        write_docx(
            self.source,
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
        ) -> str:
            descriptions.append(description)
            return original(command, description, input_text=input_text)

        with patch.object(_reference, "run_command", record):
            stats = _pipeline.convert_docx(self.source)
        assert stats["unique_images"] == 3
        assert descriptions.count("EMF-to-SVG conversion") == 1
        assert "EMF-to-PDF fallback conversion" not in descriptions
        for name in names:
            self.assert_cropped_to_the_drawing(name)

    def test_real_synthetic_emf_pdf_fallback(self) -> None:
        original = _reference.run_command

        def force_pdf(
            command: list[str],
            description: str,
            *,
            input_text: str | None = None,
        ) -> str:
            if description == "EMF-to-SVG conversion":
                raise _models.ConversionError("Exercising real PDF fallback")
            return original(command, description, input_text=input_text)

        with (
            patch.object(_reference, "run_command", force_pdf),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _pipeline.convert_docx(self.source)
        assert "diagram.svg" in self.source.with_suffix(".md").read_text(encoding="utf-8")
        self.assert_cropped_to_the_drawing()
