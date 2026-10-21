from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

CLI = (sys.executable, "-m", "md_doc_converter")
SETTINGS = {"MD_DOC_CONVERTER_HEADER", "MD_DOC_CONVERTER_FOOTER", "MD_DOC_CONVERTER_PDF_TIMEOUT"}


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


class DocumentWorkflowTest(unittest.TestCase):
    def command(self, directory: Path, *arguments: str) -> None:
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
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_word_to_html_and_pdf_next_to_the_canonical_markdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="md-doc-converter-workflow-") as temporary:
            workspace = Path(temporary)
            source_dir = workspace / "source docs"
            source_dir.mkdir()
            source = source_dir / "cache.v2 \u03bc.docx"
            fixture = workspace / "fixture.md"
            external_url = "https://example.test/R\u00e9sum\u00e9_(detail)"
            fixture.write_text(
                "# 1. Cache. {#cache}\n\n### 2. Status. {#word-status}\n\n"
                "**Step 1.**\n\nKeep the paragraph boundary.\n\n"
                "* Alpha\n* Beta\n\n"
                "[Status section](#word-status)\n\n"
                f"[External reference]({external_url})\n\n"
                "![Synthetic figure.](pixel.png)\n\n"
                "| Signal | Value |\n| --- | --- |\n| Data | One |\n| | |\n"
            )
            (workspace / "pixel.png").write_bytes(pixel_png())
            self.command(workspace, "pandoc", str(fixture), "--output", str(source))
            with zipfile.ZipFile(source) as archive:
                word = ET.fromstring(archive.read("word/document.xml"))
            word_rows = len(
                word.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr")
            )
            self.assertEqual(word_rows, 3)
            word_before = source.read_bytes()
            self.command(workspace, *CLI, "docx-to-md", str(source.relative_to(workspace)))
            canonical = source.with_suffix(".md")
            markdown_before = canonical.read_bytes()
            images = canonical.parent / ".images" / canonical.stem
            images_before = {
                path.relative_to(images): path.read_bytes()
                for path in images.rglob("*")
                if path.is_file()
            }
            self.assertTrue(images_before)
            self.assertIn("## 2. Status\n", markdown_before.decode())
            self.assertIn("**Step 1.**\n\nKeep", markdown_before.decode())
            self.assertIn("- Alpha\n- Beta", markdown_before.decode())
            self.assertIn("[Status section](#2-status)", markdown_before.decode())
            self.assertNotIn("<a ", markdown_before.decode())
            self.assertNotIn("<table", markdown_before.decode())

            relative = str(canonical.relative_to(workspace))
            self.command(workspace, *CLI, "md-to-html", relative)
            html = canonical.with_suffix(".html")
            rendered = html.read_text()
            # The blank Word row shows nothing, so it is omitted.
            self.assertEqual(len(re.findall(r"<tr(?:\s|>)", rendered)), word_rows - 1)
            self.assertIn('id="1-cache"', rendered)
            self.assertIn('id="2-status"', rendered)
            self.assertIn('href="#2-status"', rendered)
            self.assertIn(
                external_url,
                [unquote(url) for url in re.findall(r'href="([^"]+)"', rendered)],
            )
            # The shared styles are inlined, so the HTML does not depend on the installation.
            self.assertIn(".table-cols table", rendered)
            self.assertNotIn("style-overrides.css", rendered)

            self.command(workspace, *CLI, "md-to-pdf", relative)
            pdf = canonical.with_suffix(".pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            review = workspace / "reviews" / "cache review.pdf"
            self.command(
                workspace,
                *CLI,
                "md-to-pdf",
                str(canonical),
                "-o",
                str(review.relative_to(workspace)),
            )
            self.assertTrue(review.read_bytes().startswith(b"%PDF-"))
            self.assertEqual([path.name for path in review.parent.iterdir()], [review.name])

            self.assertEqual(canonical.read_bytes(), markdown_before)
            self.assertEqual(source.read_bytes(), word_before)
            self.assertEqual(
                {
                    path.relative_to(images): path.read_bytes()
                    for path in images.rglob("*")
                    if path.is_file()
                },
                images_before,
            )
            self.assertEqual(
                {path.name for path in source_dir.iterdir()},
                {source.name, canonical.name, html.name, pdf.name, ".images"},
            )

    def test_reference_folder_renders_linked_html_and_pdf_with_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="md-doc-converter-references-") as temporary:
            workspace = Path(temporary)
            docs = workspace / "guide's parts \u03bc"
            images = docs / ".images"
            images.mkdir(parents=True)
            image = images / "pixel \u03bc.png"
            image.write_bytes(pixel_png())
            sources = {
                docs / "a.md": (
                    "# Page\n\n[Next][target]\n\n![Figure][pic]\n\n"
                    '[target]: <b page.md#page> "Go"\n'
                    '[pic]: <.images/pixel \u03bc.png> "Figure title"\n'
                    "\nInline math: $x^2 + y^2$.\n\n"
                    "```mermaid\nflowchart LR\n  Input --> Output\n```\n"
                ),
                docs / "b page.md": '# Page\n\n[Self][target]\n\n[target]: #page "Self"\n',
            }
            for path, text in sources.items():
                path.write_text(text, encoding="utf-8")
            page_text = ("--header", "Package layout validation", "--footer", "Draft")
            self.command(workspace, *CLI, "md-to-html", docs.name, *page_text)
            html = (docs / f"{docs.name}.html").read_text(encoding="utf-8")
            self.assertIn('id="page"', html)
            self.assertIn('id="page-1"', html)
            self.assertEqual(html.count('href="#page-1"'), 2)
            image_sources = re.findall(r'<img\s[^>]*src="([^"]+)"', html)
            self.assertEqual(
                [unquote(urlsplit(source).path) for source in image_sources].count(str(image)), 1
            )
            self.assertIn("mermaid-diagram", html)
            self.assertIn('class="katex"', html)
            self.assertIn('content: "Package layout validation"', html)
            self.assertIn('content: "Draft"', html)

            kept = workspace / "intermediates"
            self.command(
                workspace,
                *CLI,
                "md-to-pdf",
                docs.name,
                *page_text,
                "--keep-intermediates",
                str(kept),
            )
            pdf = (docs / f"{docs.name}.pdf").read_bytes()
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertRegex(pdf, rb"/Subtype\s*/Image\b")
            self.assertTrue((kept / "markdown" / f"{docs.name}.md").is_file())
            self.assertIn(
                "Package layout validation",
                (kept / "render" / f"{docs.name}.html").read_text(encoding="utf-8"),
            )
            for path, text in sources.items():
                self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertEqual(image.read_bytes(), pixel_png())
            self.assertEqual(sorted(path.name for path in docs.glob("*.md")), ["a.md", "b page.md"])


if __name__ == "__main__":
    unittest.main()
