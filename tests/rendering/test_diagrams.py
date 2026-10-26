"""Mermaid diagrams drawn by the installed mermaid-cli, with the packages that it loads."""

from __future__ import annotations

import base64
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests.support.suites import poppler

CLI = (sys.executable, "-m", "md_doc_converter")
# One diagram of each common type. Their labels hold letters, such as "l" and "0", that Chrome
# printed without their text when diagrams were drawn in Mermaid's own font.
DIAGRAMS = """# Diagrams

```mermaid
flowchart LR
  A[Client label] --> B{Pipe 0} -->|all| C[Worker 1]
```

```mermaid
sequenceDiagram
  Client->>Server: login all
  Server-->>Client: token 0
```

```mermaid
classDiagram
  class Pool
  Pool <|-- SmallPool
```

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> Loading: load 0
```
"""
LABELS = [
    "Client label",
    "Pipe 0",
    "Worker 1",
    "login all",
    "token 0",
    "SmallPool",
    "Idle",
    "load 0",
]


@dataclass(frozen=True)
class Printed:
    html: str
    pdf: Path


def svgs(html: str) -> list[str]:
    """The SVG of each Mermaid diagram in *html*."""
    images: list[str] = []
    for tag in re.findall(r"<img\s[^>]*>", html):
        if 'class="mermaid-diagram"' not in tag:
            continue
        header, data = re.findall(r'src="([^"]+)"', tag)[0].split(",", 1)
        images.append(
            base64.b64decode(data).decode("utf-8") if header.endswith(";base64") else unquote(data)
        )
    return images


def diagrams(markdown: str, directory: Path) -> list[str]:
    """Render *markdown* to HTML; return the SVG of each Mermaid diagram in it."""
    source = directory / "page.md"
    source.write_text(markdown, encoding="utf-8")
    result = subprocess.run(
        [*CLI, "md-to-html", str(source)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return svgs(source.with_suffix(".html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def printed(tmp_path_factory: pytest.TempPathFactory) -> Printed:
    """DIAGRAMS printed to PDF, with the HTML that was printed."""
    directory = tmp_path_factory.mktemp("diagrams")
    source = directory / "diagrams.md"
    source.write_text(DIAGRAMS, encoding="utf-8")
    kept = directory / "intermediates"
    result = subprocess.run(
        [*CLI, "md-to-pdf", str(source), "--keep-intermediates", str(kept)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    html = (kept / "render" / "diagrams.html").read_text(encoding="utf-8")
    return Printed(html, source.with_suffix(".pdf"))


def pdf_text(pdf: Path) -> str:
    """The PDF's text, its words separated by single spaces."""
    result = subprocess.run(
        [poppler("pdftotext"), "-raw", str(pdf), "-"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return " ".join(result.stdout.split())


def test_diagram_labels_are_text_in_the_pdf(printed: Printed) -> None:
    # Diagrams embed no font of their own: their labels use the text's fonts.
    images = svgs(printed.html)
    assert len(images) == 4
    assert not [svg for svg in images if "@font-face" in svg]
    text = pdf_text(printed.pdf)
    assert [label for label in LABELS if label not in text] == []


def test_math_in_a_diagram_is_drawn_with_katex(tmp_path: Path) -> None:
    # mermaid-cli, Mermaid and the math filter share one KaTeX, which Mermaid draws math with.
    [svg] = diagrams('```mermaid\nflowchart LR\n  A["$$x^2 + y_i$$"] --> B[Plain]\n```\n', tmp_path)
    assert "<math" in svg
    assert "$$" not in svg


def test_zenuml_diagrams_are_drawn(tmp_path: Path) -> None:
    # ZenUML's optional canvas package, which setup does not install, draws only in Node.js.
    [svg] = diagrams(
        "```mermaid\nzenuml\n  title Order\n  Client->Server: request()\n```\n", tmp_path
    )
    assert 'aria-roledescription="zenuml"' in svg
    assert "request" in svg


MINDMAP = "mindmap\n  root((Root))\n    Alpha\n      One\n      Two\n    Beta\n      Three\n"


def test_the_tidy_tree_layout_is_installed(tmp_path: Path) -> None:
    # Without its package, Mermaid draws its default layout instead, and reports nothing.
    tidy, default = diagrams(
        f"```mermaid\n---\nconfig:\n  layout: tidy-tree\n---\n{MINDMAP}```\n\n"
        f"```mermaid\n{MINDMAP}```\n",
        tmp_path,
    )
    view_box = re.compile(r'viewBox="([^"]*)"')
    assert view_box.findall(tidy)[0] != view_box.findall(default)[0]
