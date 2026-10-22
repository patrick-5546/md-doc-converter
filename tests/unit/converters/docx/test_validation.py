"""The final checks of a Word conversion, each of which must report the problem it looks for."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from md_doc_converter.converters.docx import publication as _publication
from md_doc_converter.converters.docx import validation as _validation
from md_doc_converter.converters.docx.models import SectionTarget, SourceDocument, SourceTable
from tests.support.docx import W, cell, row, table

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g id="layer"/></svg>'


@dataclass(frozen=True)
class Validator:
    """Validates pages as published next to guide.md; returns the reasons it reports."""

    root: Path
    staged: Path
    pandoc: str

    def __call__(
        self,
        pages: dict[str, str],
        source: SourceDocument | None = None,
        *,
        split_depth: int = 0,
        compare: frozenset[str] = _validation.SOURCE_CHECKS,
        shapes: list[dict[str, str]] | None = None,
    ) -> list[str]:
        paths = _publication.validate_paths(
            self.root / "input.docx", self.root / "guide.md", split_depth=split_depth
        )
        issues = _validation.validate_outputs(
            {Path(page): content for page, content in pages.items()},
            paths,
            self.staged,
            self.pandoc,
            source,
            compare=compare,
            shapes=shapes,
        )
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


def word_document(
    *,
    anchors: dict[str, str] | None = None,
    bookmark_targets: dict[str, SectionTarget] | None = None,
    tables: list[SourceTable] | None = None,
    markdown_tables: set[str] | None = None,
) -> SourceDocument:
    return SourceDocument(
        ET.Element(W + "document"),
        "test",
        anchors=anchors or {},
        bookmark_targets=bookmark_targets or {},
        tables=tables or [],
        markdown_tables=markdown_tables or set(),
    )


@pytest.mark.parametrize(
    ("markdown", "reason"),
    [
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
        pytest.param(
            "![x](file:x.png)\n", "an image source cannot be checked", id="relative-file-url"
        ),
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
    ],
)
def test_each_page_check_reports_its_problem(
    validate: Validator, markdown: str, reason: str
) -> None:
    assert validate({"guide.md": markdown}) == [reason]


def test_a_valid_page_reports_nothing(validate: Validator) -> None:
    validate.stage("pic.png", "png")
    markdown = (
        "# Overview\n\n![x](.images/guide/pic.png)\n\n[Top](#overview) "
        "[Web](https://example.test/) [Picture](.images/guide/pic.png)\n"
    )
    assert validate({"guide.md": markdown}) == []


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
    assert validate({"guide.md": f"![x](.images/guide/{target})\n"}) == reasons


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
    assert validate({"guide.md": f"[x](.images/guide/{target})\n"}) == reasons


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
    assert validate({"guide.md": f"[x]({target}) [y]({target})\n"}) == 2 * reasons


def test_split_pages_need_one_title_and_links_need_their_page(validate: Validator) -> None:
    pages = {
        "guide.md": "# Guide\n\n[Missing](guide/missing.md) [Present](guide/a.md#a)\n",
        "guide/a.md": "# A\n\n# B\n",
    }
    assert validate(pages, split_depth=1) == [
        "a split page does not contain exactly one H1",
        "a link into the page tree has no prepared page",
    ]


def test_explanations_describe_broken_local_links_and_fragments(validate: Validator) -> None:
    shapes: list[dict[str, str]] = []
    reasons = validate({"guide.md": "[x](../Missing.PDF) [y](#_Ref123)\n"}, shapes=shapes)
    assert reasons == ["a local link target does not exist", "a link fragment does not resolve"]
    assert shapes == [
        {
            "matches a Word hyperlink": "no",
            "file type": "pdf",
            "form": "parent folder",
            "fragment": "no",
            "on disk": "no",
        },
        {"fragment": "_Ref", "points to": "this page"},
    ]


def test_word_bookmarks_must_become_sections(validate: Validator) -> None:
    source = word_document(
        anchors={"Intro": "_Ref1"},
        bookmark_targets={
            "_Ref2": SectionTarget(Path("guide.md"), "missing"),
            "_Ref3": SectionTarget(Path("gone.md")),
            "_Ref4": SectionTarget(Path("guide.md"), "overview"),
        },
    )
    markdown = '# Overview\n\n<a id="_Ref1"></a>Text\n'
    assert validate({"guide.md": markdown}, source) == [
        "Word bookmark markup remains",
        "a Word bookmark has no valid section destination",
        "a Word bookmark has no valid section destination",
    ]
    assert validate({"guide.md": markdown}, source, compare=frozenset()) == []


def test_images_must_match_the_word_document(validate: Validator) -> None:
    validate.stage("pic.png", "png")
    pages = {"guide.md": "![x](.images/guide/pic.png)\n"}
    assert validate(pages, word_document()) == ["image occurrences differ from the Word document"]
    assert validate(pages, word_document(), compare=frozenset({"tables", "links"})) == []


WORD_TABLE = table(row(cell("a"), cell("b")), row(cell("1"), cell("2")))
PIPE_TABLE = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"


@pytest.mark.parametrize(
    ("markdown", "reasons"),
    [
        pytest.param(PIPE_TABLE, [], id="same"),
        pytest.param("Text\n", ["the table count differs from the Word document"], id="missing"),
        pytest.param(
            PIPE_TABLE + "| 3 | 4 |\n", ["a final table lost or invented a Word row"], id="row"
        ),
        pytest.param(
            "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n",
            ["a final table lost or invented a Word cell"],
            id="cell",
        ),
        pytest.param(
            PIPE_TABLE.replace("2", "3"), ["a final table changed Word cell content"], id="content"
        ),
    ],
)
def test_pipe_tables_must_keep_the_word_table(
    validate: Validator, markdown: str, reasons: list[str]
) -> None:
    source = word_document(
        tables=[SourceTable("T1", WORD_TABLE, nested=False)], markdown_tables={"T1"}
    )
    assert validate({"guide.md": markdown}, source) == reasons


@pytest.mark.parametrize(
    ("word", "html", "reason"),
    [
        pytest.param(
            WORD_TABLE,
            "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>",
            "a final table uses HTML without a structural limitation",
            id="needless-html",
        ),
        pytest.param(
            table(row(cell("a", colspan=2)), row(cell("1"), cell("2"))),
            "<table><tr><td>a</td></tr><tr><td>1</td><td>2</td></tr></table>",
            "a final table changed Word header/merge structure",
            id="lost-merge",
        ),
    ],
)
def test_html_tables_must_need_html_and_keep_merges(
    validate: Validator, word: ET.Element, html: str, reason: str
) -> None:
    source = word_document(tables=[SourceTable("T1", word, nested=False)])
    assert validate({"guide.md": html + "\n"}, source) == [reason]
