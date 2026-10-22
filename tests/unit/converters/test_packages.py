"""Limits on the ZIP packages that Word and Excel files are."""

from __future__ import annotations

import zipfile

import pytest

from md_doc_converter.converters import packages


def members(*sizes: int) -> list[zipfile.ZipInfo]:
    entries = []
    for index, size in enumerate(sizes):
        entry = zipfile.ZipInfo(f"part{index}.xml")
        entry.file_size = size
        entries.append(entry)
    return entries


@pytest.mark.parametrize(
    "sizes",
    [
        pytest.param((), id="empty"),
        pytest.param((packages.MAX_EXPANDED_SIZE,), id="at-the-limit"),
        pytest.param((packages.MAX_EXPANDED_SIZE // 2,) * 2, id="parts-at-the-limit"),
    ],
)
def test_packages_up_to_the_limit_are_read(sizes: tuple[int, ...]) -> None:
    assert packages.too_large(members(*sizes)) is None


def test_the_expanded_size_of_all_parts_counts() -> None:
    sizes = (packages.MAX_EXPANDED_SIZE // 2, packages.MAX_EXPANDED_SIZE // 2 + 3 * packages.MIB)
    assert packages.too_large(members(*sizes)) == (
        "expands to 1,027 MiB, more than the 1,024 MiB that md-doc-converter reads"
    )
