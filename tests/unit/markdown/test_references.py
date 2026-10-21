from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote, urlsplit

from typing_extensions import override

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import READER, MarkdownDocument, MarkdownError

PANDOC = shutil.which("pandoc")


class RenderedTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self.images: list[tuple[str, str]] = []
        self.current: tuple[str, str, list[str]] | None = None
        self.text: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and "href" in attributes:
            self.current = (attributes["href"] or "", attributes.get("title") or "", [])
        if tag == "img":
            self.images.append((attributes.get("src") or "", attributes.get("title") or ""))

    @override
    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.current is not None:
            self.current[2].append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            href, title, label = self.current
            self.links.append((href, title, "".join(label)))
            self.current = None


def pandoc(markdown: str, target: str = "html5") -> str:
    return subprocess.run(
        [
            PANDOC or "pandoc",
            f"--from={READER}",
            f"--to={target}",
            "--wrap=none",
            "--syntax-highlighting=none",
        ],
        input=markdown,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def rendered(markdown: str) -> RenderedTargets:
    result = RenderedTargets()
    result.feed(pandoc(markdown))
    return result


@unittest.skipUnless(PANDOC, "pandoc not installed")
class ReferenceLinksTest(unittest.TestCase):
    def test_collection_resolves_reference_forms_before_combining_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "docs"
            docs.mkdir()
            (docs / "pic.png").write_bytes(b"fixture")
            first = """# Same
[inline b](b%20page.md#same "Inline")
[full b][shared]
[shared][]
[shared]
[formatted `label`][shared]
![full image][pic]
![pic][]
![pic]
[unbound]
[undefined][absent]

[shared]: <b page.md#same> "Reference title"
[pic]: pic.png "Picture"
"""
            second = """# Same
[local][shared]
[shared][]
[shared]

[shared]: #same "Local title"
[unbound]: #same
"""
            sources = {docs / "a.md": first, docs / "b page.md": second}
            for path, text in sources.items():
                path.write_text(text, encoding="utf-8")
            stats: dict[str, Counter[str]] = defaultdict(Counter)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = preprocess.collect_directory(str(docs), stats)
            output = rendered(result)
            self.assertEqual(len(output.links), 8)
            self.assertTrue(all(link[0] == "#same-1" for link in output.links))
            self.assertEqual(
                [link[1] for link in output.links],
                ["Inline"] + ["Reference title"] * 4 + ["Local title"] * 3,
            )
            self.assertEqual(output.links[4][2], "formatted label")
            self.assertEqual(len(output.images), 3)
            self.assertTrue(
                all(unquote(image[0]) == str(docs / "pic.png") for image in output.images)
            )
            self.assertTrue(all(image[1] == "Picture" for image in output.images))
            self.assertIn("[unbound]", "".join(output.text))
            self.assertIn("undefined reference link '[absent]'", stderr.getvalue())
            self.assertNotIn("broken anchor", stderr.getvalue())
            for path, text in sources.items():
                self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_multiline_casefolded_definitions_titles_and_encoded_image_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source's space \u03bc"
            images = root / ".images" / "cache.v2 \u03bc"
            images.mkdir(parents=True)
            picture = images / "picture#1?.png"
            picture.write_bytes(b"fixture")
            path = quote(str(picture.relative_to(root)), safe="/")
            source = f"""# R\u00e9sum\u00e9
[full][MULTI line]
[multi line][]
[multi line]
[inline](<other page(1).md#r\u00e9sum\u00e9> 'Title "quoted"')
![image][picture]
![inline](<{path}?version=1&mode=2#part> "Image title")

[multi
 line]:
  <#r%C3%A9sum%C3%A9>
  "Anchor title"
[picture]:
  <{path}?version=1&mode=2#part>
  'Image title'
"""
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = preprocess.preprocess(source, source_path=str(root / "input.md"))
            output = rendered(result)
            self.assertEqual(
                output.links[:3],
                [
                    ("#r%C3%A9sum%C3%A9", "Anchor title", "full"),
                    ("#r%C3%A9sum%C3%A9", "Anchor title", "multi line"),
                    ("#r%C3%A9sum%C3%A9", "Anchor title", "multi line"),
                ],
            )
            self.assertEqual(output.links[3][1], 'Title "quoted"')
            self.assertEqual(
                unquote(urlsplit(output.links[3][0]).path), str(root / "other page(1).md")
            )
            self.assertEqual(len(output.images), 2)
            for image, title in output.images:
                url = urlsplit(image)
                self.assertEqual(unquote(url.path), str(picture))
                self.assertEqual(url.query, "version=1&mode=2")
                self.assertEqual(url.fragment, "part")
                self.assertEqual(title, "Image title")
            self.assertEqual(stderr.getvalue(), "")

    def test_repeated_reference_image_labels_are_scoped_to_their_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary)
            expected = []
            for name in ("a", "b"):
                directory = docs / name
                directory.mkdir()
                picture = directory / "pic.png"
                picture.write_bytes(b"fixture")
                expected.append(str(picture))
                (directory / "page.md").write_text(
                    f"# {name}\n\n![pic][]\n\n[pic]: pic.png\n", encoding="utf-8"
                )
            with contextlib.redirect_stderr(io.StringIO()):
                result = preprocess.collect_directory(str(docs), defaultdict(Counter))
            self.assertEqual([unquote(path) for path, _title in rendered(result).images], expected)

    def test_escaped_reference_labels_and_literal_brackets(self) -> None:
        source = (
            "# Page\n\n"
            r"[escaped label][a\]b] and \[literal]\[a\]b]"
            "\n\n"
            r'[a\]b]: #page "Escaped label"'
            "\n"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = preprocess.preprocess(source, source_path="/docs/input.md")
        self.assertEqual(rendered(result).links, [("#page", "Escaped label", "escaped label")])
        self.assertEqual(stderr.getvalue(), "")

    def test_reference_labels_ending_in_an_escaped_backslash_remain_file_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a", "b"):
                (root / f"{name}.md").write_text(
                    f"# {name.upper()}\n\n"
                    + r"[local][ref\\]"
                    + "\n\n"
                    + r"[ref\\]: "
                    + f"#{name}\n",
                    encoding="utf-8",
                )
            with contextlib.redirect_stderr(io.StringIO()):
                result = preprocess.collect_directory(str(root), defaultdict(Counter))
            self.assertEqual(rendered(result).links, [("#a", "", "local"), ("#b", "", "local")])

    def test_normalization_preserves_parsed_content_including_entity_like_urls(self) -> None:
        source = """% Document title
% Author name
% 2026-09-18

# Same

# Same

# Explicit {#custom}

[formatted `code`][ref] [ref][] [ref] ![picture][image]

[ref]: <other page.md#target> "Title"
[image]: .images/picture.png "Image title"

<div class="table-sm">

| A | B |
| --- | --- |
| [cell][ref] | &lt;mask&gt; &amp;copy; |

</div>

```text
[ref]: untouched.md
<a href="literal.md">literal</a>
```

    [ref]: also-untouched.md

`[ref][]` and `[ref]: untouched.md`

\\[escaped][ref]

[entity](target&amp;copy;.md#x) [query](target.md?a=1&amp;b=2#x)

A footnote[^note].

[^note]: Paragraph with a [ref] and **emphasis**.

    Second paragraph.

    - List item

    ```text
    literal
    ```
"""
        result = MarkdownDocument(source).render(normalize_references=True)
        self.assertEqual(json.loads(pandoc(source, "json")), json.loads(pandoc(result, "json")))

    def test_directory_normalization_keeps_distinct_footnote_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("First", "Second"):
                (root / f"{name}.md").write_text(
                    f"# {name}\n\n[local][ref] and a note[^{name}].\n\n"
                    f"[ref]: #{name.lower()}\n"
                    f"[^{name}]: {name} unique footnote.\n\n"
                    f"    {name} second paragraph.\n",
                    encoding="utf-8",
                )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = preprocess.collect_directory(str(root), defaultdict(Counter))
            text = "".join(rendered(result).text)
            for name in ("First", "Second"):
                self.assertEqual(text.count(f"{name} unique footnote."), 1)
                self.assertEqual(text.count(f"{name} second paragraph."), 1)
            self.assertNotIn("Duplicate", stderr.getvalue())

    def test_structural_html_targets_are_rewritten_but_literal_code_is_not(self) -> None:
        source = """# Page

<table><tr><td>
<a data-example="href='leave.md'" href=real.md>HTML link</a>
<img src='pic.png' title="Title">
</td></tr></table>

`[inline](literal.md)` and `<a href="literal.md">literal</a>`

```html
<a href="literal.md">literal</a>
```

    [reference][missing]
"""
        with contextlib.redirect_stderr(io.StringIO()):
            result = preprocess.preprocess(source, source_path="/docs/input.md")
        output = rendered(result)
        self.assertEqual(output.links, [("/docs/real.md", "", "HTML link")])
        self.assertEqual(output.images, [("/docs/pic.png", "Title")])
        self.assertIn("href='leave.md'", result)
        self.assertIn('`<a href="literal.md">literal</a>`', result)
        self.assertIn('<a href="literal.md">literal</a>', "".join(output.text))

    def test_missing_reference_targets_warn_without_guessing_or_losing_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary)
            (docs / "a.md").write_text(
                "# A\n\n[missing][ref] [query][query] [remote][remote]\n\n"
                '[ref]: b.md#absent "Missing"\n'
                '[query]: b.md?version=1#b "Query"\n'
                '[remote]: https://example.test/b.md#b "Remote"\n',
                encoding="utf-8",
            )
            (docs / "b.md").write_text("# B\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = preprocess.collect_directory(str(docs), defaultdict(Counter))
            output = rendered(result)
            self.assertEqual(
                output.links,
                [
                    (str(docs / "b.md") + "#absent", "Missing", "missing"),
                    (str(docs / "b.md") + "?version=1#b", "Query", "query"),
                    ("https://example.test/b.md#b", "Remote", "remote"),
                ],
            )
            self.assertIn("heading not found in target file", stderr.getvalue())
            self.assertIn("query parameters", stderr.getvalue())

    def test_single_file_warnings_cover_reference_links_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "other page.md").write_text("# Other\n", encoding="utf-8")
            source = (
                "# Page\n[other][ref] ![missing][pic] ![placeholder][placeholder]\n\n"
                '[ref]: <other page.md> "Title"\n[pic]: absent.png "Missing"\n'
                "[placeholder]: data:image/x-emf;base64...\n"
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                preprocess.preprocess(source, str(root / "input.md"), single_file=True)
            self.assertIn("will not resolve in single-file output", stderr.getvalue())
            self.assertIn("missing image 'absent.png'", stderr.getvalue())
            self.assertIn("placeholder image with no data", stderr.getvalue())

    def test_undefined_reference_check_takes_escapes_as_escapes(self) -> None:
        # Unescaped brackets with many escapes after them once took exponential time.
        source = "[x " + "\\_" * 28 + " y]\n\n" + r"[a\][b] and [c][d]" + "\n"
        stderr = io.StringIO()
        started = time.monotonic()
        with contextlib.redirect_stderr(stderr):
            result = preprocess.warn_undefined_references(source, "page.md", Counter())
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result, source)
        self.assertEqual(stderr.getvalue().count("WARNING: undefined reference"), 1)
        self.assertIn("'[d]'", stderr.getvalue())


class BitRangeDefinitionsTest(unittest.TestCase):
    def test_bit_ranges_starting_html_block_text_are_escaped(self) -> None:
        source = (
            "<table>\n<tr>\n<td>[1:0]: Mode selector</td>\n<td><p>[10:2]: SlotID</p></td>\n"
            "<TH class=x> [11]: Foo</TH>\n<td>[11] Foo</td>\n</tr>\n</table>\n\n"
            "<pre>[3:0]: literal</pre>\n\nInline <span>[2:0]: text</span> and `<td>[4:0]: code`\n"
        )
        stats: Counter[str] = Counter()
        self.assertEqual(
            preprocess.escape_bit_ranges(source, None, stats),
            source.replace("<td>[1:0]", r"<td>\[1:0\]")
            .replace("<p>[10:2]", r"<p>\[10:2\]")
            .replace("> [11]:", r"> \[11\]:"),
        )
        self.assertEqual(stats["bit-range definitions defused"], 3)

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_html_table_cells_with_bit_ranges_keep_their_text(self) -> None:
        source = (
            "<table>\n<tr>\n<td>[1:0]: Mode selector</td>\n<td>[1:0]: QOS_SEL</td>\n"
            "</tr>\n</table>\n"
        )
        html = pandoc(preprocess.preprocess(source))
        self.assertRegex(html, r"<td>\s*\[1:0\]: Mode selector\s*</td>")
        self.assertRegex(html, r"<td>\s*\[1:0\]: QOS_SEL\s*</td>")


class TableAlignmentTest(unittest.TestCase):
    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_a_rewritten_page_keeps_table_alignment_however_long_its_lines(self) -> None:
        # A table line longer than 72 characters gives the table column widths in Pandoc.
        row = "| rst | I | " + "Long description text which makes this row longer. " * 2 + "|"
        source = (
            "![Picture](picture.png)\n\n"
            "| Signal | Dir | Description |\n| --- | :-: | --- |\n" + row + "\n"
        )
        document = MarkdownDocument(source)
        document.transform_targets(lambda target, _image: "images/" + target)
        markdown = document.render()
        self.assertIn("images/picture.png", markdown)
        blocks = json.loads(pandoc(markdown, "json"))["blocks"]
        table = next(block for block in blocks if block["t"] == "Table")
        self.assertEqual(
            [spec[0]["t"] for spec in table["c"][2]],
            ["AlignDefault", "AlignCenter", "AlignDefault"],
        )


class TableCellBlockHtmlTest(unittest.TestCase):
    def test_block_html_in_a_pipe_table_cell_is_reported_with_its_line(self) -> None:
        source = (
            "# Page\n\n"
            "| Name | Values |\n| --- | --- |\n| A | x<sup>2</sup><br>`<p>` code |\n\n"
            "Name | Values\n--- | ---\nB | <ul><li>One</li></ul> and <P>two</P>\n\n"
            "```markdown\n| C | D |\n| --- | --- |\n| <div>x</div> | y |\n```\n\n"
            "<table><tr><td><ul><li>html</li></ul></td></tr></table>\n\n"
            "> | Q | R |\n> | --- | --- |\n> | <blockquote>q</blockquote> | r |\n"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = preprocess.warn_block_html_in_table_cells(source, "page.md", Counter())
        self.assertEqual(result, source)
        warnings = stderr.getvalue().splitlines()
        self.assertEqual(len(warnings), 2)
        self.assertIn(
            "HTML <ul>, <li>, <p> in a Markdown table cell in page.md (line 9;", warnings[0]
        )
        self.assertIn(
            "HTML <blockquote> in a Markdown table cell in page.md (line 21;", warnings[1]
        )

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_md_to_html_reports_the_cell_whose_rest_it_drops(self) -> None:
        source = "[[_TOC_]]\n\n\n\n| Name |\n| --- |\n| kept <ul><li>dropped</li></ul> |\n"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            prepared = preprocess.preprocess(source, "page.md")
        self.assertIn("in page.md (line 7;", stderr.getvalue())
        rendered = pandoc(prepared)
        self.assertIn("kept", rendered)
        self.assertNotIn("dropped", rendered)


class MarkdownFailureTest(unittest.TestCase):
    def test_missing_pandoc_is_an_explicit_error(self) -> None:
        with (
            mock.patch("subprocess.run", side_effect=FileNotFoundError("missing executable")),
            self.assertRaisesRegex(MarkdownError, "failed to run.*missing executable"),
        ):
            MarkdownDocument("[text][ref]")

    def test_failed_and_malformed_parser_output_are_rejected(self) -> None:
        for code, stdout, stderr in ((1, "", "bad reader"), (0, "not JSON", ""), (0, "{}", "")):
            with self.subTest(code=code, stdout=stdout):
                completed = subprocess.CompletedProcess(["pandoc"], code, stdout, stderr)
                with (
                    mock.patch("subprocess.run", return_value=completed),
                    self.assertRaises(MarkdownError),
                ):
                    MarkdownDocument("[text][ref]")

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_failed_writer_is_an_explicit_error(self) -> None:
        document = MarkdownDocument("# Heading\n")
        completed = subprocess.CompletedProcess(["pandoc"], 1, "", "writer failure")
        with (
            mock.patch("subprocess.run", return_value=completed),
            self.assertRaisesRegex(MarkdownError, "writer failure"),
        ):
            document.render(normalize_references=True)


if __name__ == "__main__":
    unittest.main()
