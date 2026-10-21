"""Page header and footer text as CSS for the Paged.js page-margin boxes."""


def css_string(text: str) -> str:
    if "\0" in text:
        raise ValueError("Header and footer text cannot contain NUL characters")
    escaped: list[str] = []
    for character in text:
        codepoint = ord(character)
        # Terminated hex escapes also keep embedded stylesheets safe in HTML.
        if character in '"\\<>&' or codepoint < 0x20 or codepoint == 0x7F:
            escaped.append(f"\\{codepoint:x} ")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def page_css(header: str = "", footer: str = "") -> str:
    """Show literal header text at the top right and footer text at the bottom left."""
    boxes = [(box, text) for box, text in (("top-right", header), ("bottom-left", footer)) if text]
    if not boxes:
        return ""
    rules = "".join(f"  @{box} {{\n    content: {css_string(text)};\n  }}\n" for box, text in boxes)
    css = "@page {\n" + rules + "}\n"
    # Reject text that cannot be written as UTF-8, such as lone surrogates.
    css.encode("utf-8")
    return css
