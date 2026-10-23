"""The printed PDF: page size and margins, header and footer, bookmarks, tagging and fonts."""

from __future__ import annotations

import html
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

CLI = (sys.executable, "-m", "md_doc_converter")
# No fi, fl or ff, which fonts may print as ligatures.
HEADER, FOOTER = "Design review", "Example Corp"
# Content wider than the page unless it wraps; Chrome would shrink every page to fit it.
LONG_URL = "https://example.com/" + "averyveryverylongpathsegment" * 6 + ".html"
LONG_CODE = "x = 1  # " + "a comment that has to wrap inside the code block " * 4
DOCUMENT = f"""# Cache controller

Introduction, with math: $E = mc^2$.

## Registers

| Name | Reference |
| --- | --- |
| CTRL | <{LONG_URL}> |

### Control register

```python
{LONG_CODE}
```

## Interfaces

{"Each request names an address and a size, and the controller answers it. " * 80}

### Request port

The last section.
"""
BOOKMARKS = [
    "Cache controller",
    ["Registers", ["Control register"], "Interfaces", ["Request port"]],
]
INCH = 72
# Tables wider than the page, as Word documents' signal tables are: no column is narrower than
# its longest identifier. The first fits at about 70% of its size; the second does not fit even
# at half its size, so its identifiers break.
SIGNALS = [f"ctl_signal_name_{n}" for n in range(5)]
LONG_SIGNALS = ["very_long_signal_identifier_" * 3 + str(n) for n in range(3)]
PARAGRAPH = "Each request names an address and a size, and the controller answers it. " * 4
WIDE_DOCUMENT = f"""# Signals

{PARAGRAPH}

| {" | ".join(SIGNALS)} |
| {" | ".join("---" for _ in SIGNALS)} |
| {" | ".join(SIGNALS)} |

{PARAGRAPH}

| {" | ".join(LONG_SIGNALS)} |
| {" | ".join("---" for _ in LONG_SIGNALS)} |
"""


def run(*arguments: str | Path, cwd: Path | None = None) -> str:
    result = subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("pdf")
    source = directory / "spec.md"
    source.write_text(DOCUMENT, encoding="utf-8")
    run(*CLI, "md-to-pdf", source, "--header", HEADER, "--footer", FOOTER, cwd=directory)
    return source.with_suffix(".pdf")


@pytest.fixture(scope="module")
def pdf_without_math(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The document without math, for which katex.js links no stylesheet."""
    directory = tmp_path_factory.mktemp("pdf-without-math")
    source = directory / "spec.md"
    source.write_text(DOCUMENT.replace(", with math: $E = mc^2$", ""), encoding="utf-8")
    run(*CLI, "md-to-pdf", source, cwd=directory)
    return source.with_suffix(".pdf")


@pytest.fixture(scope="module")
def wide_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("wide")
    source = directory / "signals.md"
    source.write_text(WIDE_DOCUMENT, encoding="utf-8")
    run(*CLI, "md-to-pdf", source, cwd=directory)
    return source.with_suffix(".pdf")


def words(pdf: Path) -> list[tuple[str, float, float, float]]:
    """The words between the header and the footer: text, left and right edges, and height."""
    layout = run("pdftotext", "-bbox", pdf, "-")
    pattern = r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<'
    return [
        (html.unescape(text), float(left), float(right), float(bottom) - float(top))
        for left, top, right, bottom, text in re.findall(pattern, layout)
        if INCH < float(top) < 792 - INCH
    ]


def pages(pdf: Path) -> int:
    match = re.search(r"^Pages:\s+(\d+)$", run("pdfinfo", pdf), re.MULTILINE)
    assert match is not None
    return int(match[1])


def test_pages_are_tagged_us_letter(pdf: Path) -> None:
    info = run("pdfinfo", pdf)
    assert re.search(r"^Tagged:\s+yes$", info, re.MULTILINE)
    assert re.search(r"^Page size:\s+612 x 792 pts \(letter\)$", info, re.MULTILINE)
    assert pages(pdf) >= 2


def test_every_page_shows_the_header_footer_and_its_number(pdf: Path) -> None:
    count = pages(pdf)
    for page in range(1, count + 1):
        text = run("pdftotext", "-f", str(page), "-l", str(page), pdf, "-")
        assert HEADER in text
        assert FOOTER in text
        assert f"Page {page} of {count}" in text


def test_headings_become_nested_bookmarks(pdf: Path) -> None:
    def titles(outline: ET.Element) -> list[object]:
        return [titles(child) if child.tag == "outline" else child.text for child in outline]

    root = ET.fromstring(run("pdftohtml", "-xml", "-i", "-stdout", "-q", pdf))
    outline = root.find("outline")
    assert outline is not None
    assert titles(outline) == BOOKMARKS


@pytest.mark.parametrize("document", ["pdf", "pdf_without_math"], ids=["math", "no-math"])
def test_text_fills_the_width_between_one_inch_margins(
    document: str, request: pytest.FixtureRequest
) -> None:
    # Pandoc's own document styles, a narrow padded body, would apply without math.
    pdf: Path = request.getfixturevalue(document)
    # Words between the header and the footer, as (left, right) edges in points.
    layout = run("pdftotext", "-bbox", pdf, "-")
    words = [
        (float(left), float(right))
        for left, top, right in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)"', layout
        )
        if INCH < float(top) < 792 - INCH
    ]
    assert min(left for left, _right in words) == pytest.approx(INCH, abs=1)
    # Lines end at the right margin, give or take the fonts; Chrome shrinks every page
    # when something is wider than the page, and lines then end well short of it.
    assert max(right for _left, right in words) == pytest.approx(612 - INCH, abs=5)


def test_long_code_lines_wrap_inside_the_page(pdf: Path) -> None:
    # The PDF leaves out what a code block cuts off at the page edge.
    text = " ".join(run("pdftotext", pdf, "-").split())
    assert " ".join(LONG_CODE.split()) in text


def test_a_table_wider_than_the_page_shrinks_alone(pdf: Path, wide_pdf: Path) -> None:
    # Chrome would shrink every page to fit the widest table; only the tables shrink instead.
    found = words(wide_pdf)
    height = {text: size for text, _left, _right, size in found}
    normal = next(size for text, _left, _right, size in words(pdf) if text == "answers")
    assert height["answers"] == pytest.approx(normal, rel=0.01)
    assert min(left for _text, left, _right, _size in found) == pytest.approx(INCH, abs=1)
    assert max(right for _text, _left, right, _size in found) < 612 - INCH + 1
    # The first table prints whole, its identifiers unbroken but smaller than the text.
    for signal in SIGNALS:
        assert 0.5 * normal < height[signal] < 0.9 * normal
    # The second, too wide even at half its size, breaks its identifiers but leaves none out.
    printed = "".join(run("pdftotext", wide_pdf, "-").split())
    for signal in LONG_SIGNALS:
        assert signal in printed


def test_math_prints_with_the_embedded_katex_fonts(pdf: Path) -> None:
    # The HTML links nothing, so these fonts come from its embedded stylesheet.
    assert "KaTeX_Main" in run("pdffonts", pdf)
