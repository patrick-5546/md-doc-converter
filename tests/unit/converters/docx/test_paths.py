"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication


class PathSafetyTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-path-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.docx"
        self.source.write_bytes(b"source")

    def test_default_output_is_beside_provided_symlink(self) -> None:
        alias = self.root / "aliases" / "Requested Name.docx"
        alias.parent.mkdir()
        alias.symlink_to(self.source)
        paths = _publication.validate_paths(alias, None)
        self.assertEqual(paths.source, self.source)
        self.assertEqual(paths.output, alias.with_suffix(".md"))
        self.assertEqual(paths.images, alias.parent / ".images" / "Requested Name")

    def test_explicit_output_and_safe_symlink_parent(self) -> None:
        directory = self.root / "real"
        directory.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        paths = _publication.validate_paths(self.source, alias / "Other.Name.md")
        self.assertEqual(paths.output, directory / "Other.Name.md")
        self.assertEqual(paths.images, directory / ".images" / "Other.Name")

    def test_existing_derived_markdown_needs_permission_even_when_empty(self) -> None:
        self.source.with_suffix(".md").touch()
        with self.assertRaisesRegex(_models.ConversionError, "Derived Markdown already exists"):
            _publication.validate_paths(self.source, None)
        _publication.validate_paths(self.source, None, replace_generated=True)
        _publication.validate_paths(self.source, self.source.with_suffix(".md"))

    def test_nonempty_images_and_pages_require_replace_flag(self) -> None:
        for target, depth in ((self.root / ".images" / "output", 0), (self.root / "output", 1)):
            with self.subTest(target=target):
                target.mkdir(parents=True)
                (target / "old").write_text("old", encoding="utf-8")
                with self.assertRaisesRegex(
                    _models.ConversionError, "requires --replace-generated"
                ):
                    _publication.validate_paths(
                        self.source, self.root / "output.md", split_depth=depth
                    )
                _publication.validate_paths(
                    self.source, self.root / "output.md", split_depth=depth, replace_generated=True
                )
                (target / "old").unlink()

    def test_depth_zero_does_not_claim_old_page_tree(self) -> None:
        old = self.root / "input"
        old.mkdir()
        (old / "old.md").write_text("old", encoding="utf-8")
        _publication.validate_paths(self.source, None)
        self.assertEqual((old / "old.md").read_text(encoding="utf-8"), "old")

    def test_hardlinked_markdown_source_alias_is_rejected(self) -> None:
        output = self.root / "alias.md"
        os.link(self.source, output)
        with self.assertRaisesRegex(_models.ConversionError, "aliases the source"):
            _publication.validate_paths(self.source, output, replace_generated=True)

    def test_output_symlink_is_never_replaced(self) -> None:
        output = self.root / "alias.md"
        output.symlink_to(self.source)
        with self.assertRaisesRegex(_models.ConversionError, "destination symlink"):
            _publication.validate_paths(self.source, output, replace_generated=True)

    def test_dangling_output_symlink_is_rejected(self) -> None:
        output = self.root / "alias.md"
        output.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(_models.ConversionError, "destination symlink"):
            _publication.validate_paths(self.source, output)

    def test_image_parent_symlink_cannot_escape_containment(self) -> None:
        (self.root / ".images").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(_models.ConversionError, "must not be a symlink"):
            _publication.validate_paths(self.source, None, replace_generated=True)

    def test_output_types_are_checked_before_mutation(self) -> None:
        output = self.root / "output.md"
        output.mkdir()
        with self.assertRaisesRegex(_models.ConversionError, "not a regular file"):
            _publication.validate_paths(self.source, output)

    def test_incompatible_parent_is_rejected(self) -> None:
        parent = self.root / "file"
        parent.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(_models.ConversionError, "not a directory"):
            _publication.validate_paths(self.source, parent / "output.md")

    def test_source_inside_selected_page_tree_is_rejected(self) -> None:
        directory = self.root / "selected"
        directory.mkdir()
        source = directory / "source.docx"
        source.write_bytes(b"source")
        with self.assertRaisesRegex(_models.ConversionError, "contains the source"):
            _publication.validate_paths(
                source, self.root / "selected.md", split_depth=1, replace_generated=True
            )

    def test_source_alias_entry_inside_selected_tree_is_rejected(self) -> None:
        directory = self.root / "selected"
        directory.mkdir()
        source = directory / "alias.docx"
        source.symlink_to(self.source)
        with self.assertRaisesRegex(_models.ConversionError, "contains the source"):
            _publication.validate_paths(
                source, self.root / "selected.md", split_depth=1, replace_generated=True
            )

    def test_hardlink_alias_inside_generated_tree_is_rejected(self) -> None:
        directory = self.root / ".images" / "output"
        directory.mkdir(parents=True)
        os.link(self.source, directory / "source.bin")
        with self.assertRaisesRegex(_models.ConversionError, "contains a source alias"):
            _publication.validate_paths(
                self.source, self.root / "output.md", replace_generated=True
            )

    def test_child_symlink_inside_generated_tree_is_rejected(self) -> None:
        directory = self.root / ".images" / "output"
        directory.mkdir(parents=True)
        (directory / "link").symlink_to(self.root)
        with self.assertRaisesRegex(_models.ConversionError, "Unsafe symlink"):
            _publication.validate_paths(
                self.source, self.root / "output.md", replace_generated=True
            )

    def test_repository_root_is_protected_as_page_tree(self) -> None:
        directory = self.root / "repository"
        (directory / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(
            _models.ConversionError, "protected filesystem, home, or repository root"
        ):
            _publication.validate_paths(
                self.source, self.root / "repository.md", split_depth=1, replace_generated=True
            )

    def test_repository_nested_inside_generated_directory_is_protected(self) -> None:
        (self.root / ".images" / "output" / "nested" / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(_models.ConversionError, "protected repository"):
            _publication.validate_paths(
                self.source, self.root / "output.md", replace_generated=True
            )

    def test_image_leaf_and_page_leaf_symlinks_are_rejected(self) -> None:
        images = self.root / ".images" / "output"
        images.parent.mkdir()
        images.symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaisesRegex(_models.ConversionError, "destination symlink"):
            _publication.validate_paths(
                self.source, self.root / "output.md", replace_generated=True
            )
        images.unlink()
        (self.root / "output").symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaisesRegex(_models.ConversionError, "destination symlink"):
            _publication.validate_paths(
                self.source, self.root / "output.md", split_depth=1, replace_generated=True
            )

    def test_incompatible_image_parent_is_rejected(self) -> None:
        (self.root / ".images").write_bytes(b"not a directory")
        with self.assertRaisesRegex(_models.ConversionError, "not a directory"):
            _publication.validate_paths(self.source, self.root / "output.md")

    def test_diagnostics_cannot_be_source_hardlink(self) -> None:
        diagnostic = self.root / "debug"
        os.link(self.source, diagnostic)
        with self.assertRaisesRegex(_models.ConversionError, "not a directory"):
            _publication.validate_paths(self.source, None, keep_intermediates=diagnostic)

    def test_diagnostics_reject_all_destination_overlaps(self) -> None:
        output = self.root / "output.md"
        for directory in (
            self.root,
            output,
            self.root / ".images",
            self.root / ".images" / "output" / "debug",
            self.root / "output" / "debug",
        ):
            with (
                self.subTest(directory=directory),
                self.assertRaisesRegex(_models.ConversionError, "Diagnostic directory overlaps"),
            ):
                _publication.validate_paths(self.source, output, keep_intermediates=directory)

    def test_diagnostic_directory_must_be_new_or_empty(self) -> None:
        directory = self.root / "debug"
        directory.mkdir()
        _publication.validate_paths(self.source, None, keep_intermediates=directory)
        (directory / "mine").touch()
        with self.assertRaisesRegex(_models.ConversionError, "not empty"):
            _publication.validate_paths(self.source, None, keep_intermediates=directory)

    def test_diagnostic_symlink_is_rejected(self) -> None:
        target = self.root / "empty"
        target.mkdir()
        link = self.root / "debug"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(_models.ConversionError, "destination symlink"):
            _publication.validate_paths(self.source, None, keep_intermediates=link)
