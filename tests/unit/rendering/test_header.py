import unittest

from md_doc_converter.rendering import header as header_module
from md_doc_converter.rendering import pipeline as render


class PageTextCssTest(unittest.TestCase):
    def test_quotes_backslashes_and_control_characters_are_css_escaped(self) -> None:
        cases = (
            ('A"B\\C', '"A\\22 B\\5c C"'),
            ("\n\r\f\t\x7f", '"\\a \\d \\c \\9 \\7f "'),
            ("", '""'),
            ("\u03bcController", '"\u03bcController"'),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(header_module.css_string(text), expected)

    def test_html_delimiters_cannot_close_an_embedded_stylesheet(self) -> None:
        self.assertEqual(
            header_module.css_string("</style><script>&"),
            '"\\3c /style\\3e \\3c script\\3e \\26 "',
        )

    def test_header_and_footer_select_their_own_margin_boxes(self) -> None:
        self.assertEqual(header_module.page_css(), "")
        header = header_module.page_css(header="Design review")
        self.assertEqual(header, '@page {\n  @top-right {\n    content: "Design review";\n  }\n}\n')
        self.assertNotIn("@bottom-", header)
        footer = header_module.page_css(footer="Draft")
        self.assertIn('@bottom-left {\n    content: "Draft";', footer)
        self.assertNotIn("@top-", footer)
        both = header_module.page_css('Review "A" & $value', "Draft")
        self.assertIn(header_module.css_string('Review "A" & $value'), both)
        self.assertLess(both.index("@top-right"), both.index("@bottom-left"))

    def test_invalid_text_is_rejected(self) -> None:
        for text in ("bad\0header", "\ud800"):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    header_module.page_css(header=text)
                with self.assertRaises(ValueError):
                    header_module.page_css(footer=text)


class PageStylesTest(unittest.TestCase):
    def test_shared_overrides_page_text_and_extra_css_are_inlined_in_order(self) -> None:
        styles = render.page_styles("Header", "Footer", ".markdown-body { color: red; }\n")
        self.assertTrue(styles.startswith("<style>\n"))
        self.assertTrue(styles.endswith("</style>\n"))
        self.assertEqual(styles.count("<style>"), 1)
        overrides = styles.index(".table-cols table")
        header = styles.index('content: "Header"')
        footer = styles.index('content: "Footer"')
        extra = styles.index("color: red")
        self.assertLess(overrides, header)
        self.assertLess(header, footer)
        self.assertLess(footer, extra)

    def test_no_fixed_header_or_footer_text_is_shipped(self) -> None:
        styles = render.page_styles()
        for box in ("@top-right", "@bottom-left"):
            block = styles[styles.index(box) :]
            self.assertNotIn("content", block[: block.index("}")])
        self.assertIn('content: "Page " counter(page) " of " counter(pages)', styles)

    def test_invalid_page_text_is_a_render_error(self) -> None:
        with self.assertRaisesRegex(render.RenderError, "Invalid header or footer text"):
            render.page_styles(footer="bad\0footer")


if __name__ == "__main__":
    unittest.main()
