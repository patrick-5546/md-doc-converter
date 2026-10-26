"""The final checks of a Word conversion, each of which must report the problem it looks for."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.converters.docx.models import DocumentNavigation

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g id="layer"/></svg>'


@dataclass(frozen=True)
class Validator:
    """Validates Markdown as published to guide.md; returns the reasons it reports."""

    root: Path
    staged: Path
    pandoc: str

    def __call__(self, markdown: str, source: DocumentNavigation | None = None) -> list[str]:
        paths = _publication.validate_paths(self.root / "input.docx", self.root / "guide.md")
        issues = _validation.validate_outputs(markdown, paths, self.staged, self.pandoc, source)
        return [reason for reason, _detail in issues]

    def stage(self, name: str, content: str) -> None:
        """Stage an image, which the page refers to as .images/guide/<name>."""
        (self.staged / name).write_text(content, encoding="utf-8")


@pytest.fixture
def validate(tmp_path: Path, pandoc: str) -> Validator:
    (tmp_path / "input.docx").write_bytes(b"source")
    staged = tmp_path / "staged-images"
    staged.mkdir()
    return Validator(tmp_path, staged, pandoc)


# A page with each problem that a page check looks for, and the reason it reports.
PAGE_PROBLEMS = [
    pytest.param(
        '<a id="x"></a>\n\n<a id="x"></a>\n', "duplicate anchors on a page", id="duplicate-ids"
    ),
    pytest.param('<img alt="Figure">\n', "retained media has no source", id="no-source"),
    pytest.param(
        "![x](https://example.test/x.png)\n",
        "an image is remote or was not recovered",
        id="remote-image",
    ),
    pytest.param(
        "![x](elsewhere/x.png)\n",
        "an image is outside the image directory",
        id="image-outside",
    ),
    pytest.param(
        "![x](.images/guide/missing.png)\n", "an image file is missing", id="missing-image"
    ),
    pytest.param(
        "![x](file://host/x.png)\n", "an image source cannot be checked", id="remote-file-image"
    ),
    pytest.param("![x](file:x.png)\n", "an image source cannot be checked", id="relative-file-url"),
    pytest.param(
        "[x](file://host/doc.pdf)\n", "a link target cannot be checked", id="remote-file-link"
    ),
    pytest.param(
        "[x](.images/guide/missing.png)\n",
        "a linked image file is missing",
        id="missing-linked-image",
    ),
    pytest.param(
        "[x](missing.pdf)\n", "a local link target does not exist", id="missing-local-file"
    ),
    pytest.param("[x](#nowhere)\n", "a link fragment does not resolve", id="missing-section"),
]


@pytest.mark.parametrize(("markdown", "reason"), PAGE_PROBLEMS)
def test_each_page_check_reports_its_problem(
    validate: Validator, markdown: str, reason: str
) -> None:
    assert validate(markdown) == [reason]


def test_every_problem_on_a_page_is_reported(validate: Validator) -> None:
    page = "\n".join(str(problem.values[0]) for problem in PAGE_PROBLEMS)
    assert sorted(validate(page)) == sorted(str(problem.values[1]) for problem in PAGE_PROBLEMS)


@pytest.mark.parametrize("kind", ["empty", "symbolic-link"])
def test_an_empty_or_linked_image_file_is_missing(
    validate: Validator, kind: str, tmp_path: Path
) -> None:
    if kind == "empty":
        validate.stage("pic.png", "")
    else:
        (tmp_path / "real.png").write_text("png", encoding="utf-8")
        (validate.staged / "pic.png").symlink_to(tmp_path / "real.png")
    assert validate("![x](.images/guide/pic.png)\n") == ["an image file is missing"]


def test_a_valid_page_reports_nothing(validate: Validator) -> None:
    validate.stage("pic.png", "png")
    markdown = (
        "# Overview\n\n![x](.images/guide/pic.png)\n\n[Top](#overview) "
        "[Web](https://example.test/) [Picture](.images/guide/pic.png)\n"
    )
    assert validate(markdown) == []


@pytest.mark.parametrize(
    ("staged", "target", "reasons"),
    [
        pytest.param(SVG, "d.svg#layer", [], id="svg-element"),
        pytest.param(SVG, "d.svg#nope", ["an image fragment does not resolve"], id="svg-missing"),
        pytest.param("<svg", "d.svg#layer", ["an image fragment does not resolve"], id="bad-svg"),
        pytest.param("png", "d.png#layer", ["an image fragment does not resolve"], id="not-svg"),
    ],
)
def test_image_fragments_must_name_an_svg_element(
    validate: Validator, staged: str, target: str, reasons: list[str]
) -> None:
    validate.stage(target.partition("#")[0], staged)
    assert validate(f"![x](.images/guide/{target})\n") == reasons


@pytest.mark.parametrize(
    ("staged", "target", "reasons"),
    [
        pytest.param(SVG, "d.svg#layer", [], id="svg-element"),
        pytest.param(SVG, "d.svg#nope", ["a link fragment does not resolve"], id="svg-missing"),
        pytest.param("<svg", "d.svg#layer", ["a link fragment does not resolve"], id="bad-svg"),
        pytest.param("png", "d.png#layer", ["a link fragment does not resolve"], id="not-svg"),
    ],
)
def test_links_to_images_check_their_fragments_too(
    validate: Validator, staged: str, target: str, reasons: list[str]
) -> None:
    validate.stage(target.partition("#")[0], staged)
    assert validate(f"[x](.images/guide/{target})\n") == reasons


@pytest.mark.parametrize(
    ("target", "reasons"),
    [
        pytest.param("other.md#overview", [], id="markdown-heading"),
        pytest.param("other.md#nope", ["a link fragment does not resolve"], id="markdown-missing"),
        pytest.param("notes.html#part", [], id="html-element"),
        pytest.param("notes.html#nope", ["a link fragment does not resolve"], id="html-missing"),
        pytest.param(
            "notes.txt#part",
            ["a link fragment into a local file cannot be checked"],
            id="unsupported-file",
        ),
    ],
)
def test_fragments_into_local_files_are_checked_in_the_file(
    validate: Validator, target: str, reasons: list[str]
) -> None:
    (validate.root / "other.md").write_text("# Overview\n", encoding="utf-8")
    (validate.root / "notes.html").write_text('<h2 id="part">Part</h2>', encoding="utf-8")
    (validate.root / "notes.txt").write_text("part", encoding="utf-8")
    # A second link to the same file reuses its anchors, and reports its own problem.
    assert validate(f"[x]({target}) [y]({target})\n") == 2 * reasons


def test_word_bookmarks_must_become_sections(validate: Validator) -> None:
    # A bookmark leads to its section's heading, or with no fragment to the document top.
    source = DocumentNavigation(
        bookmarks={"_Ref1", "_Ref2", "_Ref3", "_Ref4"},
        bookmark_targets={"_Ref2": "missing", "_Ref3": "", "_Ref4": "overview"},
    )
    markdown = '# Overview\n\n<a id="_Ref1"></a>Text\n'
    assert validate(markdown, source) == [
        "Word bookmark markup remains",
        "a Word bookmark has no valid section destination",
    ]


def test_references_include_named_anchors_and_every_media_source() -> None:
    page = ET.fromstring(
        '<body><a name="legacy"></a><span name="ignored"></span><area href="map.html"/>'
        '<object data="diagram.svg"></object><video src="clip.mp4" poster="poster.png"></video>'
        "</body>"
    )
    references = _validation.collect_references(page)
    assert references.anchors == ["legacy"]
    assert references.links == []
    assert references.images == ["diagram.svg", "clip.mp4", "poster.png"]
