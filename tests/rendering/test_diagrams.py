"""Mermaid diagrams drawn by the installed mermaid-cli, with the packages that it loads."""

from __future__ import annotations

import base64
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

CLI = (sys.executable, "-m", "md_doc_converter")


def diagrams(markdown: str, directory: Path) -> list[str]:
    """Render *markdown* to HTML; return the SVG of each Mermaid diagram in it."""
    source = directory / "page.md"
    source.write_text(markdown, encoding="utf-8")
    result = subprocess.run(
        [*CLI, "md-to-html", str(source)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    html = source.with_suffix(".html").read_text(encoding="utf-8")
    images: list[str] = []
    for tag in re.findall(r"<img\s[^>]*>", html):
        if 'class="mermaid-diagram"' not in tag:
            continue
        header, data = re.findall(r'src="([^"]+)"', tag)[0].split(",", 1)
        images.append(
            base64.b64decode(data).decode("utf-8") if header.endswith(";base64") else unquote(data)
        )
    return images


def test_math_in_a_diagram_is_drawn_with_katex(tmp_path: Path) -> None:
    # mermaid-cli, Mermaid and the math filter share one KaTeX, which Mermaid draws math with.
    [svg] = diagrams('```mermaid\nflowchart LR\n  A["$$x^2 + y_i$$"] --> B[Plain]\n```\n', tmp_path)
    assert "<math" in svg
    assert "$$" not in svg
