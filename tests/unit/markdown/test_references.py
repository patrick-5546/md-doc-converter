"""Reference links and images, bit ranges and table cells in Markdown preprocessing.

Most tests render the preprocessed Markdown with the pinned Pandoc and check its links.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote, urlsplit

import pytest
from typing_extensions import override

from md_doc_converter.markdown import preprocess
from md_doc_converter.markdown.document import READER, MarkdownDocument, MarkdownError


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


Convert = Callable[[str, str], str]
Render = Callable[[str], RenderedTargets]


@pytest.fixture
def convert(pandoc: str) -> Convert:
    """Convert Markdown, read as the renderer reads it, to a Pandoc output format."""

    def run(markdown: str, target: str) -> str:
        return subprocess.run(
            [
                pandoc,
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

    return run


@pytest.fixture
def rendered(convert: Convert) -> Render:
    """The links, images and text of Markdown rendered to HTML."""

    def parse(markdown: str) -> RenderedTargets:
        result = RenderedTargets()
        result.feed(convert(markdown, "html5"))
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


def test_normalization_preserves_parsed_content_including_entity_like_urls(
    convert: Convert,
) -> None:
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
    assert json.loads(convert(source, "json")) == json.loads(convert(result, "json"))


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


def test_structural_html_targets_are_rewritten_but_literal_code_is_not(rendered: Render) -> None:
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
    assert "href='leave.md'" in result
    assert '`<a href="literal.md">literal</a>`' in result
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
    assert stderr.count("WARNING: undefined reference") == 1
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


def test_a_rewritten_page_keeps_table_alignment_however_long_its_lines(convert: Convert) -> None:
    # A table line longer than 72 characters gives the table column widths in Pandoc.
    row = "| rst | I | " + "Long description text which makes this row longer. " * 2 + "|"
    source = (
        "![Picture](picture.png)\n\n"
        "| Signal | Dir | Description |\n| --- | :-: | --- |\n" + row + "\n"
    )
    document = MarkdownDocument(source)
    document.transform_targets(lambda target, _image: "images/" + target)
    markdown = document.render()
    assert "images/picture.png" in markdown
    blocks = json.loads(convert(markdown, "json"))["blocks"]
    table = next(block for block in blocks if block["t"] == "Table")
    assert [spec[0]["t"] for spec in table["c"][2]] == [
        "AlignDefault",
        "AlignCenter",
        "AlignDefault",
    ]


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
    assert "HTML <ul>, <li>, <p> in a Markdown table cell in page.md (line 9;" in warnings[0]
    assert "HTML <blockquote> in a Markdown table cell in page.md (line 21;" in warnings[1]


def test_md_to_html_reports_the_cell_whose_rest_it_drops(
    convert: Convert, capsys: pytest.CaptureFixture[str]
) -> None:
    source = "[[_TOC_]]\n\n\n\n| Name |\n| --- |\n| kept <ul><li>dropped</li></ul> |\n"
    prepared = preprocess.preprocess(source, "page.md")
    assert "in page.md (line 7;" in capsys.readouterr().err
    html = convert(prepared, "html5")
    assert "kept" in html
    assert "dropped" not in html


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


def test_failed_writer_is_an_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    document = MarkdownDocument("# Heading\n")
    completed = subprocess.CompletedProcess(["pandoc"], 1, "", "writer failure")
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=completed))
    with pytest.raises(MarkdownError, match=r"writer failure"):
        document.render(normalize_references=True)


def test_only_image_files_inside_the_folder_are_embedded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "docs"
    (folder / "pictures").mkdir(parents=True)
    (folder / "pictures" / "chart.png").write_bytes(b"chart")
    (folder / "notes.txt").write_text("notes", encoding="utf-8")
    (tmp_path / "secret.png").write_bytes(b"secret")
    (folder / "pictures" / "escape.png").symlink_to(tmp_path / "secret.png")
    chart, secret = folder / "pictures" / "chart.png", tmp_path / "secret.png"
    kept = [
        str(secret),
        secret.as_uri(),
        str(folder / "pictures" / "escape.png"),
        str(folder / "notes.txt"),
        "https://example.test/remote.png",
    ]
    markdown = (
        f'![chart]({chart}#part) <img src="{chart}">\n\n'
        + " ".join(f"![kept]({target})" for target in kept)
        + "\n\n![data](data:image/png;base64,ZGF0YQ==) [link](https://example.test/page)\n"
    )

    result = preprocess.embed_images(markdown, folder)

    assert "data:image/png;base64,Y2hhcnQ=#part" in result
    assert '<img src="data:image/png;base64,Y2hhcnQ=">' in result
    for target in kept:
        assert f"]({target})" in result
    assert "](data:image/png;base64,ZGF0YQ==)" in result
    assert "[link](https://example.test/page)" in result
    assert capsys.readouterr().err.splitlines() == [
        f"WARNING: image {secret} is not embedded: it is not a file in {folder}",
        f"WARNING: image {secret.as_uri()} is not embedded: it is not a file in {folder}",
        (
            f"WARNING: image {folder / 'pictures' / 'escape.png'} is not embedded: "
            f"it is not a file in {folder}"
        ),
        f"WARNING: image {folder / 'notes.txt'} is not embedded: it is not an image",
        (
            "WARNING: image https://example.test/remote.png is not embedded: "
            "rendering downloads nothing"
        ),
    ]
