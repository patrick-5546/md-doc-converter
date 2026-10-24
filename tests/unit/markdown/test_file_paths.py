"""File system paths as the URL paths of link and image targets, and back, on either system."""

from __future__ import annotations

import pytest

from md_doc_converter.markdown import file_paths


@pytest.mark.parametrize(
    ("path", "url", "windows"),
    [
        pytest.param("/home/me/my page.md", "/home/me/my%20page.md", False, id="linux"),
        pytest.param("docs/a#1?.png", "docs/a%231%3F.png", False, id="linux-relative"),
        pytest.param("/srv/a:b.png", "/srv/a%3Ab.png", False, id="linux-colon"),
        pytest.param(r"C:\Users\me\my page.md", "/C:/Users/me/my%20page.md", True, id="windows"),
        pytest.param(r"docs\a#1%.png", "docs/a%231%25.png", True, id="windows-relative"),
        pytest.param("C:\\", "/C:/", True, id="windows-drive"),
        pytest.param(r"\\server\share\doc.md", "//server/share/doc.md", True, id="windows-share"),
    ],
)
def test_a_file_path_is_a_url_path_and_back(path: str, url: str, windows: bool) -> None:
    assert file_paths.url_path(path, windows=windows) == url
    assert file_paths.file_path(url, windows=windows) == path
