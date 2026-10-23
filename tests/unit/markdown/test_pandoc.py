"""Markdown parsed once by Pandoc, and reused by the DOCX page rewriter without changing it."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest

from md_doc_converter import pandoc
from md_doc_converter.converters.docx import navigation, reference
from md_doc_converter.converters.docx.models import ConversionError, SectionTarget


def test_pandoc_runs_overlap_and_their_results_keep_their_order() -> None:
    # Each call waits for the other: run one after the other, they would time out.
    both = threading.Barrier(2, timeout=10)

    def wait_then(value: int) -> Callable[[], int]:
        def call() -> int:
            both.wait()
            return value

        return call

    def failing(message: str) -> Callable[[], int]:
        def call() -> int:
            raise pandoc.PandocError(message)

        return call

    assert pandoc.run_all([wait_then(1), wait_then(2)]) == [1, 2]
    assert pandoc.run_all([lambda: 3]) == [3]
    with pytest.raises(pandoc.PandocError, match="first"):
        pandoc.run_all([failing("first"), failing("second")])


def test_explicit_reader_is_used_and_document_is_parsed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = subprocess.CompletedProcess(["pandoc"], 0, '{"blocks":[]}', "")
    run = mock.Mock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", run)
    parsed = pandoc.parse_markdown("# Heading", reader="gfm+raw_html")
    assert parsed.markdown == "# Heading"
    assert parsed.reader == "gfm+raw_html"
    assert parsed.blocks == []
    run.assert_called_once()
    assert run.call_args.args[0] == ["pandoc", "--from=gfm+raw_html", "--to=json"]


@pytest.mark.parametrize(
    ("text", "reader"),
    [
        pytest.param("# Changed", "gfm+raw_html", id="changed-content"),
        pytest.param("# Original", "markdown", id="changed-reader"),
    ],
)
def test_ast_reuse_rejects_changed_content_or_reader(text: str, reader: str) -> None:
    parsed = pandoc.ParsedMarkdown("# Original", "gfm+raw_html", {"blocks": []})
    with pytest.raises(pandoc.PandocError, match=r"different content or reader"):
        parsed.copy_ast(text, reader)


def test_ast_copy_does_not_change_the_reusable_document() -> None:
    parsed = pandoc.ParsedMarkdown("word", "gfm+raw_html", {"blocks": [{"t": "Para", "c": []}]})
    copied = parsed.copy_ast(parsed.markdown, parsed.reader)
    blocks = copied["blocks"]
    assert isinstance(blocks, list)
    blocks.clear()
    assert len(parsed.blocks) == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("not JSON", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="no-blocks"),
        pytest.param('{"blocks":{}}', id="blocks-not-a-list"),
    ],
)
def test_invalid_json_or_root_is_an_explicit_error(payload: str) -> None:
    with pytest.raises(pandoc.PandocError):
        pandoc.decode_document(payload)


def test_docx_rewriter_rejects_a_stale_parse_before_running_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = pandoc.ParsedMarkdown("# Original", "gfm+raw_html", {"blocks": []})
    run = mock.Mock()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ConversionError, match=r"different content or reader"):
        navigation.rewrite_page_references(
            "# Changed", Path("out.md"), Path("/docs/out.md"), {}, "pandoc", parsed=parsed
        )
    run.assert_not_called()


def test_docx_headings_and_link_rewriting_share_one_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "# Top\n\n[Self](#top)\n"
    run = mock.Mock(wraps=subprocess.run)
    monkeypatch.setattr(subprocess, "run", run)
    pages = navigation.split_markdown_pages(source, Path("/docs/out.md"), 0, None, "pandoc")
    reads = [call for call in run.call_args_list if "--to=json" in call.args[0]]
    assert len(reads) == 1
    assert "[Self](#top)" in pages[Path("out.md")]


def test_rewriting_does_not_mutate_the_parse_used_for_heading_identity() -> None:
    source = "# Top\n\n[Self](#top)\n"
    parsed = pandoc.parse_markdown(source, reader=reference.DOCX_READER)
    before = json.dumps(parsed.ast)
    result = navigation.rewrite_page_references(
        source,
        Path("guide/first.md"),
        Path("/docs/guide.md"),
        {"top": SectionTarget(Path("guide/second.md"), "top")},
        "pandoc",
        parsed=parsed,
    )
    assert "(second.md#top)" in result
    assert json.dumps(parsed.ast) == before


def test_bare_addresses_in_prose_become_autolinks_and_nothing_else_changes() -> None:
    source = (
        "# About https://a.example\n\n"
        "Mail me@b.example, see https://c.example/x_(y). or \\<d@e.example\\>.\n\n"
        "[https://linked.example](https://linked.example) `https://code.example` "
        "![https://alt.example](pic.png) <span>https://raw.example</span>\n\n"
        "    https://indented.example\n"
    )
    linked: list[int] = []
    result = navigation.rewrite_page_references(
        source, Path("page.md"), Path("/docs/page.md"), {}, "pandoc", linked=linked
    )
    assert linked == [5]
    assert "# About <https://a.example>\n" in result
    assert "Mail <me@b.example>, see <https://c.example/x_(y)>. or <d@e.example>." in result
    # Text between inline HTML tags is still Markdown prose.
    assert "<span><https://raw.example></span>" in result
    for unchanged in (
        "<https://linked.example>",
        "`https://code.example`",
        "![https://alt.example](pic.png)",
        "    https://indented.example",
    ):
        assert unchanged in result
