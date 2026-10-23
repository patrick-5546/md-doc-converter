"""Destination safety: derived paths, aliases of the source, symlinks, and protected trees."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "input.docx"
    path.write_bytes(b"source")
    return path


def test_default_output_is_beside_provided_symlink(source: Path, tmp_path: Path) -> None:
    alias = tmp_path / "aliases" / "Requested Name.docx"
    alias.parent.mkdir()
    alias.symlink_to(source)
    paths = _publication.validate_paths(alias, None)
    assert paths.source == source
    assert paths.output == alias.with_suffix(".md")
    assert paths.images == alias.parent / ".images" / "Requested Name"


def test_explicit_output_and_safe_symlink_parent(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / "real"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    paths = _publication.validate_paths(source, alias / "Other.Name.md")
    assert paths.output == directory / "Other.Name.md"
    assert paths.images == directory / ".images" / "Other.Name"


def test_existing_derived_markdown_needs_permission_even_when_empty(source: Path) -> None:
    source.with_suffix(".md").touch()
    with pytest.raises(_models.ConversionError, match="Markdown output already exists"):
        _publication.validate_paths(source, None)
    _publication.validate_paths(source, None, replace_generated=True)
    _publication.validate_paths(source, source.with_suffix(".md"))


@pytest.mark.parametrize(
    ("target", "split_depth"),
    [pytest.param(".images/output", 0, id="images"), pytest.param("output", 1, id="pages")],
)
def test_nonempty_images_and_pages_require_replace_flag(
    target: str, split_depth: int, source: Path, tmp_path: Path
) -> None:
    directory = tmp_path / target
    directory.mkdir(parents=True)
    (directory / "old").write_text("old", encoding="utf-8")
    with pytest.raises(_models.ConversionError, match="requires --replace-generated"):
        _publication.validate_paths(source, tmp_path / "output.md", split_depth=split_depth)
    _publication.validate_paths(
        source, tmp_path / "output.md", split_depth=split_depth, replace_generated=True
    )


def test_depth_zero_does_not_claim_old_page_tree(source: Path, tmp_path: Path) -> None:
    old = tmp_path / "input"
    old.mkdir()
    (old / "old.md").write_text("old", encoding="utf-8")
    _publication.validate_paths(source, None)
    assert (old / "old.md").read_text(encoding="utf-8") == "old"


def test_hardlinked_markdown_source_alias_is_rejected(source: Path, tmp_path: Path) -> None:
    output = tmp_path / "alias.md"
    os.link(source, output)
    with pytest.raises(_models.ConversionError, match="aliases the source"):
        _publication.validate_paths(source, output, replace_generated=True)


def test_output_symlink_is_never_replaced(source: Path, tmp_path: Path) -> None:
    output = tmp_path / "alias.md"
    output.symlink_to(source)
    with pytest.raises(_models.ConversionError, match="destination symlink"):
        _publication.validate_paths(source, output, replace_generated=True)


def test_dangling_output_symlink_is_rejected(source: Path, tmp_path: Path) -> None:
    output = tmp_path / "alias.md"
    output.symlink_to(tmp_path / "missing")
    with pytest.raises(_models.ConversionError, match="destination symlink"):
        _publication.validate_paths(source, output)


def test_image_parent_symlink_cannot_escape_containment(source: Path, tmp_path: Path) -> None:
    (tmp_path / ".images").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(_models.ConversionError, match="must not be a symbolic link"):
        _publication.validate_paths(source, None, replace_generated=True)


@pytest.mark.parametrize("depth", [-1, 7])
def test_split_depth_is_from_0_to_6(source: Path, depth: int) -> None:
    with pytest.raises(
        _models.ConversionError, match=f"^Split depth must be between 0 and 6, got {depth}$"
    ):
        _publication.validate_paths(source, None, split_depth=depth)


def test_output_must_be_markdown(source: Path, tmp_path: Path) -> None:
    with pytest.raises(_models.ConversionError, match=r"filename ending in \.md"):
        _publication.validate_paths(source, tmp_path / "output.txt")


def test_a_mount_point_is_protected_as_page_tree(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    real_is_mount = Path.is_mount

    def is_mount(path: Path) -> bool:
        return path == mounted or real_is_mount(path)

    monkeypatch.setattr(Path, "is_mount", is_mount)
    with pytest.raises(
        _models.ConversionError, match="protected filesystem, home, or repository root"
    ):
        _publication.validate_paths(
            source, tmp_path / "mounted.md", split_depth=1, replace_generated=True
        )


def test_output_types_are_checked_before_mutation(source: Path, tmp_path: Path) -> None:
    output = tmp_path / "output.md"
    output.mkdir()
    with pytest.raises(_models.ConversionError, match="not a regular file"):
        _publication.validate_paths(source, output)


def test_incompatible_parent_is_rejected(source: Path, tmp_path: Path) -> None:
    parent = tmp_path / "file"
    parent.write_text("file", encoding="utf-8")
    with pytest.raises(_models.ConversionError, match="not a directory"):
        _publication.validate_paths(source, parent / "output.md")


def test_source_inside_selected_page_tree_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "selected"
    directory.mkdir()
    source = directory / "source.docx"
    source.write_bytes(b"source")
    with pytest.raises(_models.ConversionError, match="contains the source"):
        _publication.validate_paths(
            source, tmp_path / "selected.md", split_depth=1, replace_generated=True
        )


def test_source_alias_entry_inside_selected_tree_is_rejected(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / "selected"
    directory.mkdir()
    alias = directory / "alias.docx"
    alias.symlink_to(source)
    with pytest.raises(_models.ConversionError, match="contains the source"):
        _publication.validate_paths(
            alias, tmp_path / "selected.md", split_depth=1, replace_generated=True
        )


def test_hardlink_alias_inside_generated_tree_is_rejected(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".images" / "output"
    directory.mkdir(parents=True)
    os.link(source, directory / "source.bin")
    with pytest.raises(_models.ConversionError, match="contains a source alias"):
        _publication.validate_paths(source, tmp_path / "output.md", replace_generated=True)


def test_child_symlink_inside_generated_tree_is_rejected(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / ".images" / "output"
    directory.mkdir(parents=True)
    (directory / "link").symlink_to(tmp_path)
    with pytest.raises(_models.ConversionError, match="Unsafe symlink"):
        _publication.validate_paths(source, tmp_path / "output.md", replace_generated=True)


def test_repository_root_is_protected_as_page_tree(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / "repository"
    (directory / ".git").mkdir(parents=True)
    with pytest.raises(
        _models.ConversionError, match="protected filesystem, home, or repository root"
    ):
        _publication.validate_paths(
            source, tmp_path / "repository.md", split_depth=1, replace_generated=True
        )


def test_repository_nested_inside_generated_directory_is_protected(
    source: Path, tmp_path: Path
) -> None:
    (tmp_path / ".images" / "output" / "nested" / ".git").mkdir(parents=True)
    with pytest.raises(_models.ConversionError, match="protected repository"):
        _publication.validate_paths(source, tmp_path / "output.md", replace_generated=True)


@pytest.mark.parametrize(
    ("leaf", "split_depth"),
    [pytest.param(".images/output", 0, id="images"), pytest.param("output", 1, id="pages")],
)
def test_image_leaf_and_page_leaf_symlinks_are_rejected(
    leaf: str, split_depth: int, source: Path, tmp_path: Path
) -> None:
    link = tmp_path / leaf
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(_models.ConversionError, match="destination symlink"):
        _publication.validate_paths(
            source, tmp_path / "output.md", split_depth=split_depth, replace_generated=True
        )


def test_incompatible_image_parent_is_rejected(source: Path, tmp_path: Path) -> None:
    (tmp_path / ".images").write_bytes(b"not a directory")
    with pytest.raises(_models.ConversionError, match="not a directory"):
        _publication.validate_paths(source, tmp_path / "output.md")


def test_diagnostics_cannot_be_source_hardlink(source: Path, tmp_path: Path) -> None:
    diagnostic = tmp_path / "debug"
    os.link(source, diagnostic)
    with pytest.raises(_models.ConversionError, match="not a directory"):
        _publication.validate_paths(source, None, keep_intermediates=diagnostic)


@pytest.mark.parametrize(
    "directory",
    [
        pytest.param(".", id="output-folder"),
        pytest.param("output.md", id="markdown"),
        pytest.param(".images", id="image-parent"),
        pytest.param(".images/output/debug", id="inside-images"),
        pytest.param("output/debug", id="inside-pages"),
    ],
)
def test_diagnostics_reject_all_destination_overlaps(
    directory: str, source: Path, tmp_path: Path
) -> None:
    with pytest.raises(_models.ConversionError, match="Diagnostic directory overlaps"):
        _publication.validate_paths(
            source, tmp_path / "output.md", keep_intermediates=tmp_path / directory
        )


def test_diagnostic_directory_must_be_new_or_empty(source: Path, tmp_path: Path) -> None:
    directory = tmp_path / "debug"
    directory.mkdir()
    _publication.validate_paths(source, None, keep_intermediates=directory)
    (directory / "mine").touch()
    with pytest.raises(_models.ConversionError, match="not empty"):
        _publication.validate_paths(source, None, keep_intermediates=directory)


def test_diagnostic_symlink_is_rejected(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "empty"
    target.mkdir()
    link = tmp_path / "debug"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(_models.ConversionError, match="destination symlink"):
        _publication.validate_paths(source, None, keep_intermediates=link)
