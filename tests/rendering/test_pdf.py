"""The printed PDF: page size and margins, header and footer, bookmarks and tagging."""

from __future__ import annotations

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

Introduction.

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


def test_text_fills_the_width_between_one_inch_margins(pdf: Path) -> None:
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
    # A shrunken page would end its lines well short of the right margin.
    assert 612 - INCH - 10 < max(right for _left, right in words) <= 612 - INCH + 1
