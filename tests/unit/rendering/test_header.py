"""Page header and footer text, as CSS strings in the page styles that rendering inlines."""

from __future__ import annotations

import pytest

from md_doc_converter.rendering import header as header_module
from md_doc_converter.rendering import pipeline as render


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param('A"B\\C', '"A\\22 B\\5c C"', id="quote-and-backslash"),
        pytest.param("\n\r\f\t\x7f", '"\\a \\d \\c \\9 \\7f "', id="control-characters"),
        pytest.param("", '""', id="empty"),
        pytest.param("\u03bcController", '"\u03bcController"', id="non-ascii"),
    ],
)
def test_quotes_backslashes_and_control_characters_are_css_escaped(
    text: str, expected: str
) -> None:
    assert header_module.css_string(text) == expected


def test_html_delimiters_cannot_close_an_embedded_stylesheet() -> None:
    assert (
        header_module.css_string("</style><script>&") == '"\\3c /style\\3e \\3c script\\3e \\26 "'
    )


def test_header_and_footer_select_their_own_margin_boxes() -> None:
    assert header_module.page_css() == ""
    header = header_module.page_css(header="Design review")
    assert header == '@page {\n  @top-right {\n    content: "Design review";\n  }\n}\n'
    assert "@bottom-" not in header
    footer = header_module.page_css(footer="Draft")
    assert '@bottom-left {\n    content: "Draft";' in footer
    assert "@top-" not in footer
    both = header_module.page_css('Review "A" & $value', "Draft")
    assert header_module.css_string('Review "A" & $value') in both
    assert both.index("@top-right") < both.index("@bottom-left")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("bad\0header", "NUL characters", id="nul"),
        pytest.param("\ud800", "surrogates", id="surrogate"),
    ],
)
def test_invalid_text_is_rejected(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        header_module.page_css(header=text)
    with pytest.raises(ValueError, match=message):
        header_module.page_css(footer=text)


def test_shared_overrides_page_text_and_extra_css_are_inlined_in_order() -> None:
    styles = render.page_styles("Header", "Footer", ".markdown-body { color: red; }\n")
    assert styles.startswith("<style>\n")
    assert styles.endswith("</style>\n")
    assert styles.count("<style>") == 1
    overrides = styles.index(".table-cols table")
    header = styles.index('content: "Header"')
    footer = styles.index('content: "Footer"')
    extra = styles.index("color: red")
    assert overrides < header
    assert header < footer
    assert footer < extra


def test_no_fixed_header_or_footer_text_is_shipped() -> None:
    styles = render.page_styles()
    for box in ("@top-right", "@bottom-left"):
        block = styles[styles.index(box) :]
        assert "content" not in block[: block.index("}")]
    assert 'content: "Page " counter(page) " of " counter(pages)' in styles


def test_invalid_page_text_is_a_render_error() -> None:
    with pytest.raises(render.RenderError, match=r"Invalid header or footer text"):
        render.page_styles(footer="bad\0footer")
