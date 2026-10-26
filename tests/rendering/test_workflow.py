"""Real workflows: a Word document to canonical Markdown, HTML and PDF; a folder of pages."""

from __future__ import annotations

import base64
import os
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote

from tests.support.suites import poppler

CLI = (sys.executable, "-m", "md_doc_converter")
SETTINGS = {
    "MD_DOC_CONVERTER_HEADER",
    "MD_DOC_CONVERTER_FOOTER",
    "MD_DOC_CONVERTER_PDF_TIMEOUT",
    "MD_DOC_CONVERTER_LANG",
}


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def pixel_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(b"\0\x20\x70\xc0"))
        + png_chunk(b"IEND", b"")
    )


def command(directory: Path, *arguments: str) -> None:
    """Run a command in *directory*, without the user's rendering settings; it must succeed."""
    environment = {key: value for key, value in os.environ.items() if key not in SETTINGS}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        list(arguments),
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_word_to_html_and_pdf_next_to_the_canonical_markdown(tmp_path: Path) -> None:
    source_dir = tmp_path / "source docs"
    source_dir.mkdir()
    source = source_dir / "cache.v2 \u03bc.docx"
    fixture = tmp_path / "fixture.md"
    external_url = "https://example.test/R\u00e9sum\u00e9_(detail)"
    fixture.write_text(
        "# 1. Cache. {#cache}\n\n### 2. Status. {#word-status}\n\n"
        "**Step 1.**\n\nKeep the paragraph boundary.\n\n"
        "* Alpha\n* Beta\n\n"
        "[Status section](#word-status)\n\n"
        f"[External reference]({external_url})\n\n"
        "![Synthetic figure.](pixel.png)\n\n"
        "| Signal | Value |\n| --- | --- |\n| Data | One |\n| | |\n",
        encoding="utf-8",
    )
    (tmp_path / "pixel.png").write_bytes(pixel_png())
    command(tmp_path, "pandoc", str(fixture), "--output", str(source))
    with zipfile.ZipFile(source) as archive:
        word = ET.fromstring(archive.read("word/document.xml"))
    word_rows = len(
        word.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr")
    )
    assert word_rows == 3
    word_before = source.read_bytes()
    command(tmp_path, *CLI, "docx-to-md", str(source.relative_to(tmp_path)))
    canonical = source.with_suffix(".md")
    markdown_before = canonical.read_bytes()
    images = canonical.parent / ".images" / canonical.stem
    images_before = {
        path.relative_to(images): path.read_bytes() for path in images.rglob("*") if path.is_file()
    }
    assert images_before
    assert "## 2. Status.\n" in markdown_before.decode()
    assert "**Step 1.**\n\nKeep" in markdown_before.decode()
    assert "- Alpha\n- Beta" in markdown_before.decode()
    assert "[Status section](#2-status)" in markdown_before.decode()
    assert "<a " not in markdown_before.decode()
    assert "<table" not in markdown_before.decode()

    relative = str(canonical.relative_to(tmp_path))
    command(tmp_path, *CLI, "md-to-html", relative)
    html = canonical.with_suffix(".html")
    rendered = html.read_text(encoding="utf-8")
    # The HTML has LF line endings on every system.
    assert b"\r" not in html.read_bytes()
    # The blank Word row shows nothing, so it is omitted.
    assert len(re.findall(r"<tr(?:\s|>)", rendered)) == word_rows - 1
    assert 'id="1-cache"' in rendered
    assert 'id="2-status"' in rendered
    assert 'href="#2-status"' in rendered
    assert external_url in [unquote(url) for url in re.findall(r'href="([^"]+)"', rendered)]
    # The HTML is self-contained: styles inlined, images embedded, nothing linked.
    assert ".table-cols table" in rendered
    assert "style-overrides.css" not in rendered
    assert re.search(r'<img\s[^>]*src="data:image/', rendered)
    assert not re.search(r'\ssrc="(?!data:)', rendered)
    assert "<link" not in rendered
    # Without math, KaTeX's stylesheet and fonts stay out.
    assert "KaTeX_Main" not in rendered
    assert '<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">' in rendered

    command(tmp_path, *CLI, "md-to-pdf", relative)
    pdf = canonical.with_suffix(".pdf")
    assert pdf.read_bytes().startswith(b"%PDF-")
    # Chrome gives the PDF the HTML's language, rather than its own locale's.
    assert b"/Lang (en)" in pdf.read_bytes()
    review = tmp_path / "reviews" / "cache review.pdf"
    command(
        tmp_path,
        *CLI,
        "md-to-pdf",
        str(canonical),
        "-o",
        str(review.relative_to(tmp_path)),
        "--lang",
        "fr-CA",
    )
    assert review.read_bytes().startswith(b"%PDF-")
    assert b"/Lang (fr-CA)" in review.read_bytes()
    assert [path.name for path in review.parent.iterdir()] == [review.name]

    assert canonical.read_bytes() == markdown_before
    assert source.read_bytes() == word_before
    assert {
        path.relative_to(images): path.read_bytes() for path in images.rglob("*") if path.is_file()
    } == images_before
    assert {path.name for path in source_dir.iterdir()} == {
        source.name,
        canonical.name,
        html.name,
        pdf.name,
        ".images",
    }


def test_reference_folder_renders_linked_html_and_pdf_with_images(tmp_path: Path) -> None:
    docs = tmp_path / "guide's parts \u03bc"
    images = docs / ".images"
    images.mkdir(parents=True)
    image = images / "pixel \u03bc.png"
    image.write_bytes(pixel_png())
    sources = {
        docs / "a.md": (
            "# Page\n\n[Next][target]\n\n![Figure][pic]\n\n"
            '[target]: <b page.md#page> "Go"\n'
            '[pic]: <.images/pixel \u03bc.png> "Figure title"\n'
            "\nInline math: $x^2 + y^2$.\n\n$$\\frac{1}{2}$$\n\nBroken: $\\frac{1}$.\n\n"
            "```mermaid\nflowchart LR\n  Input --> Output\n```\n"
        ),
        docs / "b page.md": '# Page\n\n[Self][target]\n\n[target]: #page "Self"\n',
    }
    for path, text in sources.items():
        path.write_text(text, encoding="utf-8")
    page_text = ("--header", "Package layout validation", "--footer", "Draft")
    command(tmp_path, *CLI, "md-to-html", docs.name, *page_text)
    html = (docs / f"{docs.name}.html").read_text(encoding="utf-8")
    assert 'id="page"' in html
    assert 'id="page-1"' in html
    assert html.count('href="#page-1"') == 2
    image_sources = re.findall(r'<img\s[^>]*src="([^"]+)"', html)
    embedded = "data:image/png;base64," + base64.b64encode(pixel_png()).decode("ascii")
    assert image_sources.count(embedded) == 1
    assert all(source.startswith("data:") for source in image_sources)
    assert "mermaid-diagram" in html
    assert 'class="katex"' in html
    assert 'class="katex-display"' in html
    assert 'class="katex-error"' in html
    # KaTeX's stylesheet is embedded, as a data URI, with its WOFF2 fonts only; nothing else
    # is linked.
    [katex] = re.findall(
        r'<link href="data:text/css;charset=utf-8,([^"]+)" rel="stylesheet" />', html
    )
    katex_css = unquote(katex)
    assert re.search(r"font-family:KaTeX_Main;[^}]*src:url\(data:font/woff2;base64,", katex_css)
    assert 'format("woff")' not in katex_css
    assert html.count("<link") == 1
    assert 'content: "Package layout validation"' in html
    assert 'content: "Draft"' in html

    kept = tmp_path / "intermediates"
    command(
        tmp_path,
        *CLI,
        "md-to-pdf",
        docs.name,
        *page_text,
        "--keep-intermediates",
        str(kept),
    )
    pdf = (docs / f"{docs.name}.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert re.search(rb"/Subtype\s*/Image\b", pdf)
    assert (kept / "document" / f"{docs.name}.json").is_file()
    assert "Package layout validation" in (kept / "render" / f"{docs.name}.html").read_text(
        encoding="utf-8"
    )
    for path, text in sources.items():
        assert path.read_text(encoding="utf-8") == text
    assert image.read_bytes() == pixel_png()
    assert sorted(path.name for path in docs.glob("*.md")) == ["a.md", "b page.md"]


def test_githubs_math_syntax_is_drawn_with_katex(tmp_path: Path) -> None:
    (tmp_path / "page.md").write_text(
        "Inline $`a^2`$.\n\n```math\n\\sum_{i=1}^n i\n```\n", encoding="utf-8"
    )
    command(tmp_path, *CLI, "md-to-html", "page.md")
    html = (tmp_path / "page.html").read_text(encoding="utf-8")
    # KaTeX keeps each formula's TeX in its MathML.
    tex = re.findall(r'<annotation encoding="application/x-tex">(.*?)</annotation>', html)
    assert tex == ["a^2", "\\sum_{i=1}^n i"]
    assert html.count('class="katex-display"') == 1


def test_a_document_from_elsewhere_cannot_read_local_files_into_its_output(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"top-secret-token")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "pixel.png").write_bytes(pixel_png())
    read_secret = (
        f'var request = new XMLHttpRequest(); request.open("GET", "{secret.as_uri()}", false); '
        'request.send(); document.getElementById("leak").textContent = "LEAKED " + '
        "request.responseText;"
    )
    (docs / "page.md").write_text(
        "# Page\n\n![inside](pixel.png)\n\n"
        f"![absolute]({secret}) ![relative](../secret.png)\n\n"
        f'<img src="{secret.as_uri()}">\n\n<p id="leak">Kept</p>\n\n'
        f"<script>\n{read_secret}\n</script>\n",
        encoding="utf-8",
    )

    command(tmp_path, *CLI, "md-to-html", "docs/page.md")
    html = (docs / "page.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," + base64.b64encode(pixel_png()).decode("ascii") in html
    assert "top-secret-token" not in html
    assert base64.b64encode(b"top-secret-token").decode("ascii") not in html
    assert '<meta http-equiv="Content-Security-Policy"' in html

    command(tmp_path, *CLI, "md-to-pdf", "docs/page.md")
    text = subprocess.run(
        [poppler("pdftotext"), str(docs / "page.pdf"), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Kept" in text
    assert "LEAKED" not in text
