"""Synthetic DOCX regression coverage."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from typing_extensions import override

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication
from tests.support.docx import (
    PNG,
    SVG,
)


class PublicationTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="docx-publish-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "source.docx"
        source.write_bytes(b"source")
        self.paths = _publication.validate_paths(
            source, self.root / "output" / "document.md", split_depth=2, replace_generated=True
        )
        self.staged_md = self.root / "staged.md"
        self.staged_images = self.root / "staged-images"
        self.staged_pages = self.root / "staged-pages"
        self.staged_md.write_text("new markdown", encoding="utf-8")
        self.staged_images.mkdir()
        (self.staged_images / "new.svg").write_bytes(SVG)
        self.staged_pages.mkdir()
        (self.staged_pages / "01-new.md").write_text("new page", encoding="utf-8")
        self.paths.output.parent.mkdir()
        self.paths.output.write_text("old markdown", encoding="utf-8")
        self.paths.images.mkdir(parents=True)
        (self.paths.images / "old.svg").write_bytes(SVG)
        self.paths.pages.mkdir()
        (self.paths.pages / "01-old.md").write_text("old page", encoding="utf-8")

    def publish(self) -> None:
        _publication.publish_outputs(
            self.staged_md, self.staged_images, self.paths, self.staged_pages
        )

    def assert_old(self) -> None:
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "old markdown")
        self.assertEqual((self.paths.images / "old.svg").read_bytes(), SVG)
        self.assertEqual((self.paths.pages / "01-old.md").read_text(encoding="utf-8"), "old page")
        self.assertFalse((self.paths.images / "new.svg").exists())
        self.assertFalse((self.paths.pages / "01-new.md").exists())

    def assert_no_transfers(self) -> None:
        for parent in (self.paths.output.parent, self.paths.images.parent):
            self.assertFalse(
                any(path.name.startswith(".docx-publish-") for path in parent.iterdir())
            )

    def test_complete_batch_replaces_only_selected_artifacts(self) -> None:
        unrelated = self.paths.output.parent / "unrelated.md"
        unrelated.write_text("mine", encoding="utf-8")
        self.publish()
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "new markdown")
        self.assertTrue((self.paths.images / "new.svg").is_file())
        self.assertTrue((self.paths.pages / "01-new.md").is_file())
        self.assertFalse((self.paths.pages / "01-old.md").exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "mine")
        self.assert_no_transfers()

    def _without_staged_images(self) -> None:
        shutil.rmtree(self.staged_images)
        self.staged_images.mkdir()

    def test_without_images_no_image_directory_is_created(self) -> None:
        paths = _publication.validate_paths(self.paths.source, self.root / "fresh" / "document.md")
        self._without_staged_images()
        _publication.publish_outputs(self.staged_md, self.staged_images, paths)
        self.assertEqual(paths.output.read_text(encoding="utf-8"), "new markdown")
        self.assertEqual([path.name for path in paths.output.parent.iterdir()], ["document.md"])

    def test_without_images_the_previous_image_directory_and_its_parent_go(self) -> None:
        self._without_staged_images()
        self.publish()
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "new markdown")
        self.assertTrue((self.paths.pages / "01-new.md").is_file())
        self.assertFalse(self.paths.images.parent.exists())

    def test_without_images_other_image_directories_stay(self) -> None:
        self._without_staged_images()
        other = self.paths.images.parent / "other"
        other.mkdir()
        (other / "kept.svg").write_bytes(SVG)
        self.publish()
        self.assertFalse(self.paths.images.exists())
        self.assertEqual([path.name for path in self.paths.images.parent.iterdir()], ["other"])

    def test_without_images_a_failure_restores_the_previous_image_directory(self) -> None:
        self._without_staged_images()
        original = Path.replace
        # Three backups, then two publications: nothing replaces the image directory.
        for fail_at in range(1, 6):
            with self.subTest(step=fail_at):
                calls = 0

                def fail_once(source: Path, target: Path, failure: int = fail_at) -> Path:
                    nonlocal calls
                    calls += 1
                    if calls == failure:
                        raise OSError(f"failure at replace {failure}")
                    return original(source, target)

                with (
                    patch.object(Path, "replace", fail_once),
                    self.assertRaisesRegex(_models.ConversionError, "previous outputs restored"),
                ):
                    self.publish()
                self.assert_old()
                self.assert_no_transfers()

    def test_every_backup_and_publish_failure_restores_whole_batch(self) -> None:
        original = Path.replace
        for fail_at in range(1, 7):
            with self.subTest(step=fail_at):
                calls = 0

                def fail_once(source: Path, target: Path, failure: int = fail_at) -> Path:
                    nonlocal calls
                    calls += 1
                    if calls == failure:
                        raise OSError(f"failure at replace {failure}")
                    return original(source, target)

                with (
                    patch.object(Path, "replace", fail_once),
                    self.assertRaisesRegex(_models.ConversionError, "previous outputs restored"),
                ):
                    self.publish()
                self.assert_old()
                self.assert_no_transfers()

    def test_each_directory_preparation_failure_cleans_staging(self) -> None:
        original = shutil.copytree
        for fail_at in (1, 2):
            with self.subTest(copy=fail_at):
                calls = 0

                def fail_copy(source: Path, destination: Path, failure: int = fail_at) -> Path:
                    nonlocal calls
                    calls += 1
                    if calls == failure:
                        destination.mkdir()
                        raise OSError("partial directory copy")
                    return Path(original(source, destination))

                with (
                    patch.object(shutil, "copytree", fail_copy),
                    self.assertRaisesRegex(_models.ConversionError, "preparation failed"),
                ):
                    self.publish()
                self.assert_old()
                self.assert_no_transfers()

    def test_markdown_preparation_failure_never_starts_backups(self) -> None:
        with (
            patch.object(shutil, "copy2", side_effect=OSError("copy failed")),
            patch.object(Path, "replace") as replace,
            self.assertRaisesRegex(_models.ConversionError, "preparation failed"),
        ):
            self.publish()
        replace.assert_not_called()
        self.assert_old()
        self.assert_no_transfers()

    def test_second_stage_creation_failure_cleans_prior_stage(self) -> None:
        original = tempfile.mkdtemp
        calls = 0

        def fail_stage(*, prefix: str, dir: Path) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("stage creation failed")
            return original(prefix=prefix, dir=dir)

        with (
            patch.object(tempfile, "mkdtemp", fail_stage),
            self.assertRaisesRegex(_models.ConversionError, "preparation failed"),
        ):
            self.publish()
        self.assert_old()
        self.assert_no_transfers()

    def test_failed_restore_retains_exact_backups_and_reports_both_errors(self) -> None:
        original = Path.replace

        def fail_restore(source: Path, target: Path) -> Path:
            if source.name == "prepared" and target == self.paths.pages:
                raise OSError("original publication error")
            if source.name == "backup" and target == self.paths.images:
                raise OSError("original recovery error")
            return original(source, target)

        with (
            patch.object(Path, "replace", fail_restore),
            self.assertRaises(_models.ConversionError) as raised,
        ):
            self.publish()
        message = str(raised.exception)
        self.assertIn("original publication error", message)
        self.assertIn("original recovery error", message)
        roots = [
            path
            for path in self.paths.images.parent.iterdir()
            if path.name.startswith(".docx-publish-")
        ]
        self.assertEqual(len(roots), 1)
        backup = roots[0] / "backup"
        self.assertIn(str(backup), message)
        self.assertEqual((backup / "old.svg").read_bytes(), SVG)
        self.assertTrue((roots[0] / "recovery-new" / "new.svg").is_file())
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "old markdown")

    def test_failed_vacate_also_retains_recoverable_backup(self) -> None:
        original = Path.replace

        def fail_vacate(source: Path, target: Path) -> Path:
            if source.name == "prepared" and target == self.paths.pages:
                raise OSError("publish failed")
            if source == self.paths.images and target.name == "recovery-new":
                raise OSError("cannot move replacement aside")
            return original(source, target)

        with (
            patch.object(Path, "replace", fail_vacate),
            self.assertRaisesRegex(_models.ConversionError, "rollback failed") as raised,
        ):
            self.publish()
        roots = [
            path
            for path in self.paths.images.parent.iterdir()
            if path.name.startswith(".docx-publish-")
        ]
        self.assertEqual(len(roots), 1)
        self.assertIn(str(roots[0] / "backup"), str(raised.exception))
        self.assertTrue((roots[0] / "backup" / "old.svg").is_file())
        self.assertTrue((self.paths.images / "new.svg").is_file())

    def test_cleanup_error_after_success_does_not_claim_rollback(self) -> None:
        original = shutil.rmtree

        def fail_cleanup(path: Path) -> None:
            if path.parent == self.paths.images.parent:
                raise OSError("cleanup failed")
            original(path)

        with (
            patch.object(shutil, "rmtree", fail_cleanup),
            self.assertRaisesRegex(_models.ConversionError, "All outputs were published"),
        ):
            self.publish()
        self.assertTrue((self.paths.images / "new.svg").is_file())
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "new markdown")

    def test_renames_are_destination_local_even_with_foreign_scratch(self) -> None:
        original = Path.replace
        observed: list[tuple[Path, Path]] = []

        def assert_local(source: Path, target: Path) -> Path:
            observed.append((source, target))
            self.assertEqual(source.stat().st_dev, target.parent.stat().st_dev)
            self.assertNotEqual(source, self.staged_md)
            self.assertNotEqual(source, self.staged_images)
            return original(source, target)

        with patch.object(Path, "replace", assert_local):
            self.publish()
        self.assertEqual(len(observed), 6)

    def test_real_cross_filesystem_scratch_publication(self) -> None:
        with tempfile.TemporaryDirectory(prefix="docx-cross-fs-", dir="/dev/shm") as directory:
            scratch = Path(directory)
            self.assertNotEqual(scratch.stat().st_dev, self.paths.output.parent.stat().st_dev)
            markdown = scratch / "final.md"
            images = scratch / "images"
            pages = scratch / "pages"
            markdown.write_text("cross filesystem", encoding="utf-8")
            images.mkdir()
            (images / "image.png").write_bytes(PNG)
            pages.mkdir()
            (pages / "01-child.md").write_text("# Child\n", encoding="utf-8")
            _publication.publish_outputs(markdown, images, self.paths, pages)
        self.assertEqual(self.paths.output.read_text(encoding="utf-8"), "cross filesystem")
        self.assertTrue((self.paths.images / "image.png").is_file())
        self.assert_no_transfers()

    def test_arbitrary_image_destination_cannot_bypass_derivation(self) -> None:
        paths = replace(self.paths, images=self.root / "arbitrary-images")
        with self.assertRaisesRegex(_models.ConversionError, "must derive"):
            _publication.publish_outputs(
                self.staged_md, self.staged_images, paths, self.staged_pages
            )
        self.assert_old()

    def test_rollback_restores_absence_of_new_outputs_and_parents(self) -> None:
        paths = _publication.validate_paths(
            self.paths.source, self.root / "new-parent" / "document.md", split_depth=1
        )
        original = Path.replace

        def fail_last(source: Path, target: Path) -> Path:
            if source.name == "prepared" and target == paths.pages:
                raise OSError("new-page publication failed")
            return original(source, target)

        with (
            patch.object(Path, "replace", fail_last),
            self.assertRaisesRegex(_models.ConversionError, "previous outputs restored"),
        ):
            _publication.publish_outputs(
                self.staged_md, self.staged_images, paths, self.staged_pages
            )
        self.assertFalse(paths.output.parent.exists())
        self.assert_old()

    def test_failed_rollback_keeps_backups_for_every_failed_restore(self) -> None:
        original = Path.replace

        def fail_restore(source: Path, target: Path) -> Path:
            if source.name == "prepared" and target == self.paths.pages:
                raise OSError("original error")
            if source.name == "backup":
                raise OSError("recovery error")
            return original(source, target)

        with (
            patch.object(Path, "replace", fail_restore),
            self.assertRaises(_models.ConversionError) as raised,
        ):
            self.publish()
        roots = [
            path
            for parent in (self.paths.output.parent, self.paths.images.parent)
            for path in parent.iterdir()
            if path.name.startswith(".docx-publish-")
        ]
        self.assertEqual(len(roots), 3)
        for root in roots:
            self.assertTrue((root / "backup").exists())
            self.assertIn(str(root / "backup"), str(raised.exception))
