"""The conversion report: each warning's kind and part of the summary."""

from __future__ import annotations

import io

from md_doc_converter.console import Console
from md_doc_converter.converters.docx.diagnostics import TransformReport


def test_the_summary_counts_each_kind_in_its_part_and_quotes_nothing() -> None:
    stream = io.StringIO()
    report = TransformReport(console=Console("docx-to-md", stream=stream))
    for number in range(1, 5):
        report.warn(
            f"Unresolved alt text: word/media/image{number}.png at source paragraph {number}",
            kind="image without alt text; alt text left empty",
        )
    report.skipped(
        "links",
        "link to a missing Word bookmark; kept as plain text",
        "link to missing Word bookmark '_Secret'; kept as plain text",
    )
    report.warn(
        "Table 7 remains HTML: merged cells",
        kind="table kept as HTML: merged cells",
        section="markdown",
    )
    report.warn("Image name a.png is already used; published as a-2.png", section="detail")

    assert report.issues() == [
        "docx-to-md: to check in Word:",
        "  4  image without alt text; alt text left empty",
        "  1  link to a missing Word bookmark; kept as plain text",
        "docx-to-md: expected in Markdown:",
        "  1  table kept as HTML: merged cells",
    ]
    assert "_Secret" not in "\n".join(report.issues())
    # The first three of a kind are shown; a detail only with --verbose, but it is recorded.
    shown = stream.getvalue()
    assert shown.count("Unresolved alt text") == 3
    assert "Image name a.png" not in shown
    assert "Image name a.png is already used; published as a-2.png" in report.findings
    assert len(report.findings) == 7


def test_a_failed_transform_is_counted_to_check() -> None:
    report = TransformReport(console=Console("docx-to-md", stream=io.StringIO()))

    report.failed("lists", ValueError("numbering 3 is broken"))

    assert report.issues() == [
        "docx-to-md: to check in Word:",
        "  1  lists: failed and was not applied",
    ]


def test_without_warnings_there_is_no_summary() -> None:
    report = TransformReport(console=Console("docx-to-md", stream=io.StringIO()))

    report.applied("links", count=3)

    assert report.issues() == []
