"""Reference links and images, bit ranges and table cells in Markdown preprocessing.

Most tests render the preprocessed Markdown with the pinned Pandoc and check its links.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import override
from unittest import mock
from urllib.parse import quote, unquote, urlsplit

import pytest

from md_doc_converter.console import Console, activated
from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import MarkdownDocument, MarkdownError
from tests.support import documents


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


Convert = Callable[[documents.Source, str], str]
Render = Callable[[documents.Source], RenderedTargets]


@pytest.fixture
def convert(pandoc: str) -> Convert:
    """Convert Markdown, read as the renderer reads it, or a prepared document, to a Pandoc
    output format."""

    def run(source: documents.Source, target: str) -> str:
        return documents.convert(pandoc, source, target)

    return run


@pytest.fixture
def rendered(convert: Convert) -> Render:
    """The links, images and text of Markdown rendered to HTML."""

    def parse(source: documents.Source) -> RenderedTargets:
        result = RenderedTargets()
        result.feed(convert(source, "html5"))
        return result

    return parse


def test_collection_resolves_reference_forms_before_combining_files(
    tmp_path: Path, rendered: Render, capsys: pytest.CaptureFixture[str]
) -> None:
    docs = tmp_path / "docs"
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
    result = preprocess.collect_directory(docs, stats)
    stderr = capsys.readouterr().err
    output = rendered(result)
    assert len(output.links) == 8
    assert all(link[0] == "#same-1" for link in output.links)
    assert [link[1] for link in output.links] == ["Inline"] + ["Reference title"] * 4 + [
        "Local title"
    ] * 3
    assert output.links[4][2] == "formatted label"
    assert len(output.images) == 3
    assert all(unquote(image[0]) == str(docs / "pic.png") for image in output.images)
    assert all(image[1] == "Picture" for image in output.images)
    assert "[unbound]" in "".join(output.text)
    assert "undefined reference link '[absent]'" in stderr
    assert "broken anchor" not in stderr
    for path, text in sources.items():
        assert path.read_text(encoding="utf-8") == text


def test_multiline_casefolded_definitions_titles_and_encoded_image_paths(
    tmp_path: Path, rendered: Render, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "source's space \u03bc"
    images = root / ".images" / "cache.v2 \u03bc"
    images.mkdir(parents=True)
    # Characters that a URL path must encode; Windows allows no ? in a file name.
    picture = images / ("picture#1%.png" if sys.platform == "win32" else "picture#1?.png")
    picture.write_bytes(b"fixture")
    path = quote(picture.relative_to(root).as_posix(), safe="/")
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
    result = preprocess.preprocess(source, source_path=str(root / "input.md"))
    stderr = capsys.readouterr().err
    output = rendered(result)
    assert output.links[:3] == [
        ("#r%C3%A9sum%C3%A9", "Anchor title", "full"),
        ("#r%C3%A9sum%C3%A9", "Anchor title", "multi line"),
        ("#r%C3%A9sum%C3%A9", "Anchor title", "multi line"),
    ]
    assert output.links[3][1] == 'Title "quoted"'
    assert unquote(urlsplit(output.links[3][0]).path) == str(root / "other page(1).md")
    assert len(output.images) == 2
    for image, title in output.images:
        url = urlsplit(image)
        assert unquote(url.path) == str(picture)
        assert url.query == "version=1&mode=2"
        assert url.fragment == "part"
        assert title == "Image title"
    assert stderr == ""


def test_repeated_reference_image_labels_are_scoped_to_their_source(
    tmp_path: Path, rendered: Render
) -> None:
    expected: list[str] = []
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        picture = directory / "pic.png"
        picture.write_bytes(b"fixture")
        expected.append(str(picture))
        (directory / "page.md").write_text(
            f"# {name}\n\n![pic][]\n\n[pic]: pic.png\n", encoding="utf-8"
        )
    result = preprocess.collect_directory(tmp_path, defaultdict(Counter))
    assert [unquote(path) for path, _title in rendered(result).images] == expected


def test_escaped_reference_labels_and_literal_brackets(
    rendered: Render, capsys: pytest.CaptureFixture[str]
) -> None:
    source = (
        "# Page\n\n"
        r"[escaped label][a\]b] and \[literal]\[a\]b]"
        "\n\n"
        r'[a\]b]: #page "Escaped label"'
        "\n"
    )
    result = preprocess.preprocess(source, source_path="/docs/input.md")
    stderr = capsys.readouterr().err
    assert rendered(result).links == [("#page", "Escaped label", "escaped label")]
    assert stderr == ""


def test_reference_labels_ending_in_an_escaped_backslash_remain_file_scoped(
    tmp_path: Path, rendered: Render
) -> None:
    for name in ("a", "b"):
        (tmp_path / f"{name}.md").write_text(
            f"# {name.upper()}\n\n" + r"[local][ref\\]" + "\n\n" + r"[ref\\]: " + f"#{name}\n",
            encoding="utf-8",
        )
    result = preprocess.collect_directory(tmp_path, defaultdict(Counter))
    assert rendered(result).links == [("#a", "", "local"), ("#b", "", "local")]


def test_directory_normalization_keeps_distinct_footnote_bodies(
    tmp_path: Path, rendered: Render, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("First", "Second"):
        (tmp_path / f"{name}.md").write_text(
            f"# {name}\n\n[local][ref] and a note[^{name}].\n\n"
            f"[ref]: #{name.lower()}\n"
            f"[^{name}]: {name} unique footnote.\n\n"
            f"    {name} second paragraph.\n",
            encoding="utf-8",
        )
    result = preprocess.collect_directory(tmp_path, defaultdict(Counter))
    stderr = capsys.readouterr().err
    text = "".join(rendered(result).text)
    for name in ("First", "Second"):
        assert text.count(f"{name} unique footnote.") == 1
        assert text.count(f"{name} second paragraph.") == 1
    assert "Duplicate" not in stderr


def test_structural_html_targets_are_rewritten_but_literal_code_is_not(
    rendered: Render, pandoc: str
) -> None:
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
    result = preprocess.preprocess(source, source_path="/docs/input.md")
    output = rendered(result)
    assert output.links == [("/docs/real.md", "", "HTML link")]
    assert output.images == [("/docs/pic.png", "Title")]
    written = documents.markdown(pandoc, result)
    assert "href='leave.md'" in written
    assert '`<a href="literal.md">literal</a>`' in written
    assert '<a href="literal.md">literal</a>' in "".join(output.text)


def test_missing_reference_targets_warn_without_guessing_or_losing_titles(
    tmp_path: Path, rendered: Render, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.md").write_text(
        "# A\n\n[missing][ref] [query][query] [remote][remote]\n\n"
        '[ref]: b.md#absent "Missing"\n'
        '[query]: b.md?version=1#b "Query"\n'
        '[remote]: https://example.test/b.md#b "Remote"\n',
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    result = preprocess.collect_directory(tmp_path, defaultdict(Counter))
    stderr = capsys.readouterr().err
    output = rendered(result)
    assert output.links == [
        (str(tmp_path / "b.md") + "#absent", "Missing", "missing"),
        (str(tmp_path / "b.md") + "?version=1#b", "Query", "query"),
        ("https://example.test/b.md#b", "Remote", "remote"),
    ]
    assert "heading not found in target file" in stderr
    assert "query parameters" in stderr


def test_single_file_warnings_cover_reference_links_and_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "other page.md").write_text("# Other\n", encoding="utf-8")
    source = (
        "# Page\n[other][ref] ![missing][pic] ![placeholder][placeholder]\n\n"
        '[ref]: <other page.md> "Title"\n[pic]: absent.png "Missing"\n'
        "[placeholder]: data:image/x-emf;base64...\n"
    )
    preprocess.preprocess(source, str(tmp_path / "input.md"), single_file=True)
    stderr = capsys.readouterr().err
    assert "will not resolve in single-file output" in stderr
    assert "missing image 'absent.png'" in stderr
    assert "placeholder image with no data" in stderr


def test_undefined_reference_check_takes_escapes_as_escapes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Unescaped brackets with many escapes after them once took exponential time.
    source = "[x " + "\\_" * 28 + " y]\n\n" + r"[a\][b] and [c][d]" + "\n"
    started = time.monotonic()
    result = preprocess.warn_undefined_references(source, "page.md", Counter())
    assert time.monotonic() - started < 5
    assert result == source
    stderr = capsys.readouterr().err
    assert stderr.count("warning: page.md: undefined reference") == 1
    assert "'[d]'" in stderr


def test_bit_ranges_starting_html_block_text_are_escaped() -> None:
    source = (
        "<table>\n<tr>\n<td>[1:0]: Mode selector</td>\n<td><p>[10:2]: SlotID</p></td>\n"
        "<TH class=x> [11]: Foo</TH>\n<td>[11] Foo</td>\n</tr>\n</table>\n\n"
        "<pre>[3:0]: literal</pre>\n\nInline <span>[2:0]: text</span> and `<td>[4:0]: code`\n"
    )
    stats: Counter[str] = Counter()
    assert preprocess.escape_bit_ranges(source, None, stats) == source.replace(
        "<td>[1:0]", r"<td>\[1:0\]"
    ).replace("<p>[10:2]", r"<p>\[10:2\]").replace("> [11]:", r"> \[11\]:")
    assert stats["bit-range definitions defused"] == 3


def test_html_table_cells_with_bit_ranges_keep_their_text(convert: Convert) -> None:
    source = (
        "<table>\n<tr>\n<td>[1:0]: Mode selector</td>\n<td>[1:0]: QOS_SEL</td>\n</tr>\n</table>\n"
    )
    html = convert(preprocess.preprocess(source), "html5")
    assert re.search(r"<td>\s*\[1:0\]: Mode selector\s*</td>", html)
    assert re.search(r"<td>\s*\[1:0\]: QOS_SEL\s*</td>", html)


def test_block_html_in_a_pipe_table_cell_is_reported_with_its_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = (
        "# Page\n\n"
        "| Name | Values |\n| --- | --- |\n| A | x<sup>2</sup><br>`<p>` code |\n\n"
        "Name | Values\n--- | ---\nB | <ul><li>One</li></ul> and <P>two</P>\n\n"
        "```markdown\n| C | D |\n| --- | --- |\n| <div>x</div> | y |\n```\n\n"
        "<table><tr><td><ul><li>html</li></ul></td></tr></table>\n\n"
        "> | Q | R |\n> | --- | --- |\n> | <blockquote>q</blockquote> | r |\n"
    )
    result = preprocess.warn_block_html_in_table_cells(source, "page.md", Counter())
    assert result == source
    warnings = capsys.readouterr().err.splitlines()
    assert len(warnings) == 2
    assert "page.md:9: HTML <ul>, <li>, <p> in a Markdown table cell;" in warnings[0]
    assert "page.md:21: HTML <blockquote> in a Markdown table cell;" in warnings[1]


def test_md_to_html_reports_the_cell_whose_rest_it_drops(
    convert: Convert, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "[[_TOC_]]\n\n\n\n| Name |\n| --- |\n| kept <ul><li>dropped</li></ul> |\n"
    prepared = preprocess.preprocess(source, "page.md")
    assert "page.md:7: HTML <ul>, <li> in a Markdown table cell" in capsys.readouterr().err
    html = convert(prepared, "html5")
    assert "kept" in html
    assert "dropped" not in html


def test_pandocs_warnings_name_the_file_and_the_line(capsys: pytest.CaptureFixture[str]) -> None:
    # A macro line, removed, keeps the lines after it where they were.
    source = "[[_TOC_]]\n\n# A\n\nText[^1].\n\n[^1]: one\n[^1]: two\n"

    preprocess.preprocess(source, "page.md")

    assert capsys.readouterr().err == (
        "md-doc-converter: warning: page.md:8: Duplicate note reference '1'\n"
    )


def test_wiki_macros_go_without_moving_lines_or_blank_lines_in_code(convert: Convert) -> None:
    code = "def a():\n    pass\n\n\ndef b():\n    pass"
    source = f"[[_TOC_]]\n\n\n[[_TOSP_]]  \n\n```python\n{code}\n```\n"

    stats: Counter[str] = Counter()
    assert preprocess.strip_ado_macros(source, "page.md", stats).splitlines() == [
        "",
        "",
        "",
        "",
        "",
        "```python",
        *code.splitlines(),
        "```",
    ]
    assert stats["macros removed"] == 2
    html = convert(preprocess.preprocess(source, "page.md"), "html5")
    assert "pass\n\n\n" in re.sub(r"<[^>]+>", "", html)


def test_verbose_counts_what_preparing_the_markdown_changed(tmp_path: Path) -> None:
    source = tmp_path / "page.md"
    source.write_text("[[_TOC_]]\n\n# Page\n\n- [1:0]: Mode\n", encoding="utf-8")
    stream = io.StringIO()

    with activated(Console("md-to-html", "verbose", stream=stream)):
        preprocess.prepare_markdown(source)

    assert stream.getvalue() == (
        "md-to-html: prepared the Markdown (macros removed: 1, bit-range definitions defused: 1)\n"
    )


def test_missing_pandoc_is_an_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = mock.Mock(side_effect=FileNotFoundError("missing executable"))
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(MarkdownError, match=r"failed to run.*missing executable"):
        MarkdownDocument("[text][ref]")


@pytest.mark.parametrize(
    ("code", "stdout", "stderr"),
    [
        pytest.param(1, "", "bad reader", id="failed"),
        pytest.param(0, "not JSON", "", id="not-json"),
        pytest.param(0, "{}", "", id="no-blocks"),
    ],
)
def test_failed_and_malformed_parser_output_are_rejected(
    code: int, stdout: str, stderr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = subprocess.CompletedProcess(["pandoc"], code, stdout, stderr)
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=completed))
    with pytest.raises(MarkdownError):
        MarkdownDocument("[text][ref]")
