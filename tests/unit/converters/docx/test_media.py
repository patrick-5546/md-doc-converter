"""Word drawings: EMF conversion through LibreOffice and Poppler, and images' alt text."""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Collection
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter import executables
from md_doc_converter.converters.docx import media as _media
from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import ooxml as _ooxml
from md_doc_converter.converters.docx import reference as _reference
from md_doc_converter.converters.docx.diagnostics import TransformReport
from tests.support.docx import (
    W,
    image,
    paragraph,
    synthetic_emf,
)

# pdftocairo's SVG of LibreOffice's page: US Letter, 612 by 792 points, the drawing in the middle.
CAIRO_PAGE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="612pt" height="792pt" viewBox="0 0 612 792">'
    '\n<path d="M 300 390 L 310 400"/>\n</svg>\n'
)
# What `pdfinfo -struct` prints of the tagged PDF: the frame of a 1 cm drawing in the middle.
FIGURE = "Figure:\n   /Placement /Block\n   /BBox [291.827 381.827 320.173 410.173]\n"
CROPPED_PAGE = CAIRO_PAGE.replace(
    'width="612pt" height="792pt" viewBox="0 0 612 792"',
    'width="28.346pt" height="28.346pt" viewBox="291.827 381.827 28.346 28.346"',
)
INVALID_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path clip-path="url(#none)"/></svg>'
# A word that pdftotext found: its left, top, right and bottom edges, and its text.
Word = tuple[float, float, float, float, str]


def words_page(*lines: list[Word]) -> str:
    """What `pdftotext -bbox-layout` writes of a page with *lines* of words."""
    blocks = "".join(
        "<block><line>"
        + "".join(
            f'<word xMin="{left}" yMin="{top}" xMax="{right}" yMax="{bottom}">'
            f"{html.escape(text, quote=False)}</word>"
            for left, top, right, bottom, text in line
        )
        + "</line></block>"
        for line in lines
    )
    return (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n<title></title>\n</head>\n<body>\n'
        f'<doc>\n<page width="612.000000" height="792.000000">\n<flow>{blocks}</flow>\n</page>\n'
        "</doc>\n</body>\n</html>\n"
    )


NO_WORDS = words_page()
# A line of two words across the drawing, and the invisible copy that keeps them searchable.
LINE = [(295.0, 390.0, 305.0, 398.0, "Cache"), (306.0, 390.0, 316.0, 398.0, "line")]
LAYER = (
    '<g fill-opacity="0" font-family="sans-serif"><text y="396.24" font-size="6.8">'
    '<tspan x="295" textLength="10" lengthAdjust="spacingAndGlyphs">Cache</tspan> '
    '<tspan x="306" textLength="10" lengthAdjust="spacingAndGlyphs">line</tspan></text></g>'
)
# What each tool writes for a drawing: "pdf" whether LibreOffice exports one; "cairo" and
# "words" what pdftocairo and pdftotext write, or None for nothing; "struct" what pdfinfo
# prints. False makes a tool fail.
Outputs = dict[str, str | bool | None]
DEFAULTS: Outputs = {"pdf": True, "cairo": CAIRO_PAGE, "struct": FIGURE, "words": NO_WORDS}
POPPLER = {"pdftocairo": "cairo", "pdfinfo": "struct", "pdftotext": "words"}


class FakeDrawingTools:
    """LibreOffice and Poppler stand-ins that write each drawing's outputs and record calls.

    An instance replaces `run_command`, which runs the real tools.
    """

    def __init__(self, outputs: dict[str, Outputs], failing: bool) -> None:
        self.outputs = outputs
        self.failing = failing
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], description: str, *, quiet: bool = False) -> str:
        assert quiet, "drawing conversions must not show the tools' warnings on success"
        self.calls.append(args)
        crash = _models.ConversionError(f"{description} failed: synthetic crash")
        tool = Path(args[0]).name
        if tool not in POPPLER:
            folder = Path(args[args.index("--outdir") + 1])
            for source in args[args.index("--outdir") + 2 :]:
                if self.output(Path(source).stem, "pdf"):
                    (folder / f"{Path(source).stem}.pdf").write_bytes(b"synthetic mocked PDF")
            if self.failing:
                raise crash
            return ""
        pdf = next(Path(arg) for arg in args if arg.endswith(".pdf"))
        written = self.output(pdf.stem, POPPLER[tool])
        if written is False:
            raise crash
        if tool == "pdfinfo":
            return str(written)
        if isinstance(written, str):
            Path(args[-1]).write_text(written, encoding="utf-8", newline="\n")
        return ""

    def output(self, name: str, kind: str) -> str | bool | None:
        return self.outputs[name].get(kind, DEFAULTS[kind])

    def kinds(self) -> list[str]:
        return [POPPLER.get(Path(call[0]).name, "pdf") for call in self.calls]

    def inputs(self, index: int) -> list[str]:
        call = self.calls[index]
        return [Path(source).stem for source in call[call.index("--outdir") + 2 :]]


# Each drawing's result, as text, and the findings about its conversion, by name.
Results = dict[str, tuple[str, list[str]]]
Convert = Callable[..., tuple[Results, FakeDrawingTools]]


@pytest.fixture
def convert(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Convert:
    """Convert one synthetic drawing per name in *outputs* with the native tools faked;
    *missing* names tools that are not installed, and *failing* makes LibreOffice's run fail
    after its exports. Return each result, as text, and the fakes."""

    def run(
        outputs: dict[str, Outputs], *, missing: Collection[str] = (), failing: bool = False
    ) -> tuple[Results, FakeDrawingTools]:
        tools = FakeDrawingTools(outputs, failing)

        def which(name: str, path: str | None = None) -> str | None:
            return None if name in missing else "/test/" + name

        def libreoffice() -> str | None:
            return which("libreoffice") or which("soffice")

        drawings = {name: (synthetic_emf(), ".emf") for name in outputs}
        work = tmp_path_factory.mktemp("docx-emf") / "work"
        with monkeypatch.context() as patch:
            patch.setattr(executables, "which", which)
            patch.setattr(executables, "libreoffice", libreoffice)
            patch.setattr(_reference, "run_command", tools)
            results = _media._convert_vectors(drawings, work)
        decoded = {
            name: (result.decode() if isinstance(result, bytes) else result, findings)
            for name, (result, findings) in results.items()
        }
        return decoded, tools

    return run


def whole_page(reason: str) -> str:
    return f"kept LibreOffice's whole page around the drawing: {reason}"


def test_the_page_is_cropped_to_the_frame_of_its_drawing(convert: Convert) -> None:
    results, tools = convert({"drawing": {}})
    assert results == {"drawing": (CROPPED_PAGE, [])}
    assert tools.kinds() == ["pdf", "cairo", "struct", "words"]
    office = tools.calls[0]
    target = office[office.index("--convert-to") + 1]
    kind, filter_name, options = target.split(":", 2)
    assert (kind, filter_name) == ("pdf", "draw_pdf_Export")
    assert json.loads(options) == {"UseTaggedPDF": {"type": "boolean", "value": "true"}}


@pytest.mark.parametrize(
    ("structure", "reason"),
    [
        pytest.param(
            "Figure\n",
            "LibreOffice did not report the drawing's frame, as LibreOffice 7.4 and newer do",
            id="no-frame",
        ),
        pytest.param(FIGURE * 2, "LibreOffice reported more than one frame", id="two-frames"),
        pytest.param(
            FIGURE.replace("291.827 381.827 320.173", "100 381.827 128.346"),
            "the drawing's frame is not in the middle of the page",
            id="left-of-the-middle",
        ),
        pytest.param(
            FIGURE.replace("381.827 320.173 410.173", "300 320.173 328.346"),
            "the drawing's frame is not in the middle of the page",
            id="below-the-middle",
        ),
        pytest.param(
            "   /BBox [-20 -20 632 812]\n",
            "the drawing's frame is not on the page",
            id="beyond-the-page",
        ),
        pytest.param(False, "EMF frame lookup failed: synthetic crash", id="pdfinfo-fails"),
    ],
)
def test_page_is_kept_whole_when_the_drawing_frame_is_unknown(
    structure: str | bool, reason: str, convert: Convert
) -> None:
    results, _tools = convert({"drawing": {"struct": structure}})
    assert results["drawing"] == (CAIRO_PAGE, [whole_page(reason)])


def test_frames_are_read_as_pdfinfo_prints_numbers(convert: Convert) -> None:
    exponent = FIGURE.replace("291.827 381.827", "2.91827e+02 3.81827E2")
    results, _tools = convert({"drawing": {"struct": exponent}})
    assert results["drawing"] == (CROPPED_PAGE, [])


def test_a_unitless_pdftocairo_page_is_measured_in_points(convert: Convert) -> None:
    # Poppler before 25.04 with Cairo 1.17.6 or newer writes the page size without a unit.
    unitless = CAIRO_PAGE.replace('width="612pt" height="792pt"', 'width="612" height="792"')
    cropped, _tools = convert({"drawing": {"cairo": unitless}})
    assert cropped["drawing"][0] == CROPPED_PAGE
    whole, _tools = convert({"drawing": {"cairo": unitless, "struct": ""}})
    assert whole["drawing"][0] == CAIRO_PAGE


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            '<svg width="612" height="1e3" stroke-width="2">',
            '<svg width="612pt" height="1e3pt" stroke-width="2">',
            id="unitless-width-and-height",
        ),
        pytest.param(
            '<svg width="612pt" height="10mm">',
            '<svg width="612pt" height="10mm">',
            id="sizes-with-units",
        ),
        pytest.param(
            "<svg height='79.5' viewBox='0 0 1 1'>",
            "<svg height='79.5pt' viewBox='0 0 1 1'>",
            id="single-quoted-height",
        ),
    ],
)
def test_only_a_unitless_page_size_gets_points(before: str, after: str, tmp_path: Path) -> None:
    path = tmp_path / "page.svg"
    path.write_text(f"<?xml version='1.0'?>\n{before}<g width='5'/></svg>\n", encoding="utf-8")
    _media._size_in_points(path)
    assert (
        path.read_text(encoding="utf-8") == f"<?xml version='1.0'?>\n{after}<g width='5'/></svg>\n"
    )


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_a_rewritten_svg_keeps_the_line_endings_of_its_tool(newline: bytes, tmp_path: Path) -> None:
    def page(tag: bytes, layer: bytes = b"") -> bytes:
        return newline.join((b"<?xml version='1.0'?>", tag, layer + b"</svg>", b""))

    path = tmp_path / "page.svg"
    path.write_bytes(page(b'<svg width="10" height="20" viewBox="0 0 10 20">'))
    _media._size_in_points(path)
    assert path.read_bytes() == page(b'<svg width="10pt" height="20pt" viewBox="0 0 10 20">')
    assert _media._crop_svg(path, (0, 0, 5, 10)) is None
    cropped = b'<svg width="5pt" height="10pt" viewBox="0 0 5 10">'
    assert path.read_bytes() == page(cropped)
    words = tmp_path / "page.html"
    words.write_text(words_page(LINE), encoding="utf-8")
    _media._add_text_layer(path, words)
    assert path.read_bytes() == page(cropped, LAYER.encode())


def test_all_drawings_convert_in_one_libreoffice_run(convert: Convert) -> None:
    results, tools = convert({name: {} for name in ("a", "b", "c")})
    assert results == {name: (CROPPED_PAGE, []) for name in ("a", "b", "c")}
    assert tools.kinds() == ["pdf", *["cairo", "struct", "words"] * 3]
    assert tools.inputs(0) == ["a", "b", "c"]


def test_a_failed_run_keeps_the_drawings_it_exported(convert: Convert) -> None:
    results, _tools = convert({"a": {}, "b": {"pdf": False}}, failing=True)
    assert results == {
        "a": (CROPPED_PAGE, []),
        "b": ("EMF-to-PDF conversion failed: synthetic crash", []),
    }


@pytest.mark.parametrize(
    ("outputs", "error"),
    [
        pytest.param({"pdf": False}, "LibreOffice exported no PDF of the drawing", id="no-pdf"),
        pytest.param(
            {"cairo": False}, "PDF-to-SVG conversion failed: synthetic crash", id="cairo-fails"
        ),
        pytest.param({"cairo": None}, "PDF-to-SVG conversion produced no SVG", id="no-svg"),
        pytest.param({"cairo": INVALID_SVG}, "Unresolved SVG resource in ", id="invalid-svg"),
    ],
)
def test_a_failed_conversion_affects_only_its_drawing(
    outputs: Outputs, error: str, convert: Convert
) -> None:
    results, _tools = convert({"a": outputs, "b": {}})
    result, findings = results["a"]
    assert error in result
    assert findings == []
    assert results["b"] == (CROPPED_PAGE, [])


def test_missing_office_is_a_clear_conditional_failure(convert: Convert) -> None:
    results, tools = convert({"a": {}, "b": {}}, missing=("libreoffice", "soffice"))
    for result, findings in results.values():
        assert "requires LibreOffice" in result
        assert findings == []
    assert tools.calls == []


@pytest.mark.parametrize(
    ("missing", "tools"),
    [
        pytest.param(("pdftocairo",), "pdftocairo and pdfinfo", id="no-poppler"),
        pytest.param(("pdfinfo",), "pdfinfo", id="no-pdfinfo"),
    ],
)
def test_missing_poppler_is_a_clear_conditional_failure(
    missing: tuple[str, ...], tools: str, convert: Convert
) -> None:
    results, fakes = convert({"a": {}}, missing=missing)
    assert results == {"a": (f"EMF conversion requires Poppler's {tools}; other images do not", [])}
    assert fakes.calls == []


def test_the_words_of_a_drawing_stay_searchable(convert: Convert) -> None:
    results, _tools = convert({"drawing": {"words": words_page(LINE)}})
    assert results["drawing"] == (CROPPED_PAGE.replace("</svg>", LAYER + "</svg>"), [])


@pytest.mark.parametrize(
    ("missing", "outputs", "reason"),
    [
        pytest.param(("pdftotext",), {}, "Poppler's pdftotext is not installed", id="no-pdftotext"),
        pytest.param(
            (), {"words": False}, "EMF text lookup failed: synthetic crash", id="pdftotext-fails"
        ),
        pytest.param((), {"words": None}, "No such file or directory", id="no-words"),
        pytest.param((), {"words": "<doc><line>"}, "no element found", id="unreadable-words"),
    ],
)
def test_a_drawing_whose_words_cannot_be_read_keeps_its_svg(
    missing: tuple[str, ...], outputs: Outputs, reason: str, convert: Convert
) -> None:
    results, tools = convert({"drawing": outputs}, missing=missing)
    result, [finding] = results["drawing"]
    assert result == CROPPED_PAGE
    assert finding.startswith("its text is not searchable: ")
    assert reason in finding
    assert ("words" in tools.kinds()) == (not missing)


def layer(*lines: list[Word]) -> ET.Element:
    """The text layer of a page with *lines* of words, parsed."""
    return ET.fromstring(_media._text_layer(words_page(*lines)))


def test_a_line_of_words_is_one_text_element_over_their_boxes() -> None:
    assert _media._text_layer(words_page(LINE)) == LAYER
    assert _media._text_layer(NO_WORDS) == ""


@pytest.mark.parametrize(
    ("line", "transform", "starts"),
    [
        pytest.param(
            [(100.0, 140.0, 110.0, 170.0, "Request"), (100.0, 100.0, 110.0, 135.0, "Queue")],
            "translate(107.8 170) rotate(-90)",
            ["0", "35"],
            id="up",
        ),
        pytest.param(
            [(100.0, 100.0, 110.0, 135.0, "Request"), (100.0, 140.0, 110.0, 170.0, "Queue")],
            "translate(102.2 100) rotate(90)",
            ["0", "40"],
            id="down",
        ),
        pytest.param(
            [(100.0, 100.0, 110.0, 135.0, "Request")],
            "translate(107.8 135) rotate(-90)",
            ["0"],
            id="one-word-up",
        ),
    ],
)
def test_a_vertical_line_turns_to_read_along_its_words(
    line: list[Word], transform: str, starts: list[str]
) -> None:
    [text] = layer(line)
    assert text.attrib == {"transform": transform, "font-size": "8.5"}
    assert [span.get("x") for span in text] == starts
    assert [span.get("textLength") for span in text] == [
        f"{bottom - top:g}" for _left, top, _right, bottom, _text in line
    ]


def test_short_words_side_by_side_are_a_horizontal_line() -> None:
    # Narrower than tall, but beside each other: a horizontal line of short words.
    [text] = layer([(100.0, 100.0, 104.0, 110.0, "a"), (106.0, 100.0, 110.0, 110.0, "b")])
    assert "transform" not in text.attrib
    assert [span.text for span in text] == ["a", "b"]


def test_layer_text_is_escaped_and_unusable_words_are_dropped() -> None:
    words = words_page(
        [
            (100.0, 100.0, 110.0, 110.0, "a<b&c"),
            (111.0, 100.0, 120.0, 110.0, "x\x03y"),
            (121.0, 100.0, 121.0, 110.0, "flat"),
            (122.0, 100.0, 130.0, 110.0, "bad"),
            (131.0, 100.0, 140.0, 110.0, " "),
        ]
    ).replace('xMin="122.0"', 'xMin="wide"')
    layer = _media._text_layer(words)
    assert [span.text for span in ET.fromstring(layer).iter("tspan")] == ["a<b&c", "xy"]
    assert ">a&lt;b&amp;c<" in layer


def test_characters_that_no_font_draws_are_left_out_of_the_layer() -> None:
    # Word's Symbol bullet and Wingdings arrow are private-use codes, and U+0380 is unassigned: a
    # PDF would show a missing glyph for each. A letter in the wrong place is still a letter.
    words = words_page(
        [
            (100.0, 100.0, 110.0, 110.0, "\uf0b7"),
            (111.0, 100.0, 120.0, 110.0, "a\uf0e0b"),
            (121.0, 100.0, 130.0, 110.0, "\u0380x"),
            (131.0, 100.0, 140.0, 110.0, "\u03ec"),
        ]
    )
    layer = ET.fromstring(_media._text_layer(words))
    assert [span.text for span in layer.iter("tspan")] == ["ab", "x", "\u03ec"]
    assert _media._text_layer(words_page([(100.0, 100.0, 110.0, 110.0, "\uf0b7")])) == ""


def test_a_drawing_s_notes_are_reported_by_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = [
        f"{_media._WHOLE_PAGE}: synthetic reason",
        f"{_media._UNSEARCHABLE}: synthetic reason",
    ]

    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        return dict.fromkeys(drawings, (b"<svg/>", notes))

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    drawing = synthetic_emf()
    report = TransformReport()
    stager = _media.ImageStager(tmp_path, _package({"word/media/drawing.emf": drawing}))
    try:
        stager.publish("part:word/media/drawing.emf", drawing, PurePosixPath("d.emf"), report)
    finally:
        stager.close()
    assert report.findings == [f"d.emf: {note}" for note in notes]
    # Only the whole page shows in the Markdown; the unsearchable text is a detail.
    assert report.kinds == {_media._WHOLE_PAGE: 1}
    assert report.sections == {_media._WHOLE_PAGE: "markdown"}


def _package(parts: dict[str, bytes], shown: Collection[str] = ()) -> _models.WordPackage:
    """A Word package of *parts*, whose body shows the images *shown*, by part name."""
    relationships = ET.Element(_ooxml.PACKAGE_REL + "Relationships")
    for number, name in enumerate(shown, 1):
        ET.SubElement(
            relationships,
            _ooxml.PACKAGE_REL + "Relationship",
            {
                "Id": f"rId{number}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                "Target": name.removeprefix("word/"),
            },
        )
    return _models.WordPackage(
        entries=[],
        parts=parts,
        root=ET.Element(W + "document"),
        relationships=relationships,
        styles=ET.Element(W + "styles"),
    )


def test_a_drawing_that_the_body_does_not_show_converts_when_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def convert_vectors(
        drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        calls.append(len(drawings))
        return {name: (b"<svg/>", []) for name in drawings}

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    note = synthetic_emf()
    stager = _media.ImageStager(tmp_path, _package({"word/media/note.emf": note}))
    try:
        assert calls == []
        published = stager.publish(
            "part:word/media/note.emf", note, PurePosixPath("note.emf"), TransformReport()
        )
    finally:
        stager.close()
    assert published == PurePosixPath("note.svg")
    assert calls == [1]


def test_a_failed_background_conversion_fails_the_drawings_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def convert_vectors(
        _drawings: dict[str, tuple[bytes, str]], _work: Path
    ) -> dict[str, _media.VectorResult]:
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(_media, "_convert_vectors", convert_vectors)
    shown = synthetic_emf()
    package = _package({"word/media/shown.emf": shown}, shown=["word/media/shown.emf"])
    stager = _media.ImageStager(tmp_path, package)
    try:
        with pytest.raises(OSError, match="synthetic disk failure"):
            stager.publish(
                "part:word/media/shown.emf", shown, PurePosixPath("shown.emf"), TransformReport()
            )
    finally:
        stager.close()


def test_svg_css_resource_fragments_must_exist(tmp_path: Path) -> None:
    path = tmp_path / "source.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path clip-path="url(#missing)"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(_models.ConversionError, match=r"Unresolved SVG resource"):
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


def test_caption_title_wins_over_word_alt_text() -> None:
    caption = _caption(
        "Figure ",
        (" STYLEREF 1 \\s ", "14"),
        "\u2011",
        (" SEQ Figure \\* ARABIC \\s 1 ", "13"),
        ": Bus Interface",
    )
    generated = "A diagram of a computer. AI-generated content may be incorrect."
    assert _described(image(alt=generated), caption) == [("Bus Interface", True)]


@pytest.mark.parametrize(
    ("title", "caption"),
    [
        pytest.param("Data flow", _caption("Figure 3", "\t", "Data flow"), id="tab"),
        pytest.param(
            "Pipeline",
            _caption("Figure ", (" SEQ Figure ", "6"), " : Pipeline", style=None),
            id="spaced-colon-without-the-caption-style",
        ),
        pytest.param(
            "USB\u2011to\u2011LAN bridge",
            _caption("Figure 9", "\u2011", "13 \u2013 USB", "\u2011", "to", "\u2011", "LAN bridge"),
            id="en-dash-after-a-chapter-number",
        ),
        pytest.param("Request flow", _caption("Figure 2-1: Request flow"), id="typed-number"),
        pytest.param("Figure 5", _caption("Figure ", (" SEQ Figure ", "5")), id="no-title"),
    ],
)
def test_title_follows_the_number_with_any_separator(title: str, caption: ET.Element) -> None:
    assert _described(image(alt=None), caption) == [(title, True)]


def test_an_image_between_two_captions_takes_the_side_most_captions_are_on() -> None:
    below = [image(alt=None), _figure(": One"), image(alt=None), _figure(": Two")]
    above = [_figure(": One"), image(alt=None), _figure(": Two"), image(alt=None)]
    expected = [("One", True), ("Two", True)]
    assert _described(*below) == expected
    assert _described(*above) == expected


def test_a_caption_on_the_less_common_side_is_still_used() -> None:
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


def test_a_caption_describes_one_image() -> None:
    assert _described(image(alt=None), _figure(": Shared"), image(alt="Word")) == [
        ("Shared", True),
        ("Word", False),
    ]


@pytest.mark.parametrize(
    "caption",
    [
        pytest.param(
            _caption("Table ", (" SEQ Table \\* ARABIC ", "2"), ": Registers"), id="table"
        ),
        pytest.param(_caption("Equation ", (" SEQ Equation ", "1"), style=None), id="equation"),
        pytest.param(_caption("Table 3: Registers"), id="typed-table-number"),
    ],
)
def test_table_and_equation_captions_do_not_describe_images(caption: ET.Element) -> None:
    assert _described(image(), caption) == [("Diagram", False)]


def test_without_a_caption_word_alt_text_is_kept() -> None:
    assert _described(image(), paragraph("Body text"), image(alt=None)) == [
        ("Diagram", False),
        (None, False),
    ]


def test_a_heading_above_an_image_does_not_describe_it() -> None:
    # A heading is not alt text: the image's own description is missing, and reported.
    assert _described(paragraph("Data flow", "Heading1"), paragraph(""), image(alt=None)) == [
        (None, False)
    ]


def test_images_below_headings_take_their_caption_or_word_alt_text() -> None:
    assert _described(
        paragraph("Heading", "Heading1"),
        image(alt=None),
        _figure(": Caption"),
        paragraph("Other", "Heading1"),
        image(alt="Word"),
    ) == [("Caption", True), ("Word", False)]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("<script>alert(1)</script>", id="script"),
        pytest.param('<rect onload="alert(1)"/>', id="event-handler"),
        pytest.param(
            '<foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
            "<script>alert(1)</script></body></foreignObject>",
            id="html-script",
        ),
    ],
)
def test_svgs_with_script_are_refused(content: str) -> None:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg">{content}</svg>'.encode()
    with pytest.raises(_models.ConversionError, match=r"SVG contains script"):
        _media.validate_svg_bytes(svg, "drawing.svg")
