"""Publishing a converted batch: every output is replaced, or the previous outputs are restored."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

import pytest

from md_doc_converter.converters.docx import models as _models
from md_doc_converter.converters.docx import publication as _publication
from tests.support.docx import PNG, SVG

# Runs before a Path.replace, with its source and target; raising fails that replacement.
ReplaceHook = Callable[[Path, Path], None]
OnReplace = Callable[[ReplaceHook], None]


@dataclass(frozen=True)
class Staged:
    """The new outputs, staged outside the destination: Markdown, images and pages."""

    markdown: Path
    images: Path
    pages: Path

    def publish(self, paths: _models.ConversionPaths) -> None:
        _publication.publish_outputs(self.markdown, self.images, paths, self.pages)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source.docx"
    path.write_bytes(b"source")
    return path


@pytest.fixture
def paths(source: Path, tmp_path: Path) -> _models.ConversionPaths:
    """Destinations of a document split into pages, which hold the previous outputs."""
    paths = _publication.validate_paths(
        source, tmp_path / "output" / "document.md", split_depth=2, replace_generated=True
    )
    paths.output.parent.mkdir()
    paths.output.write_text("old markdown", encoding="utf-8")
    paths.images.mkdir(parents=True)
    (paths.images / "old.svg").write_bytes(SVG)
    paths.pages.mkdir()
    (paths.pages / "01-old.md").write_text("old page", encoding="utf-8")
    return paths


@pytest.fixture
def staged(tmp_path: Path) -> Staged:
    batch = Staged(tmp_path / "staged.md", tmp_path / "staged-images", tmp_path / "staged-pages")
    batch.markdown.write_text("new markdown", encoding="utf-8")
    batch.images.mkdir()
    (batch.images / "new.svg").write_bytes(SVG)
    batch.pages.mkdir()
    (batch.pages / "01-new.md").write_text("new page", encoding="utf-8")
    return batch


@pytest.fixture
def without_images(staged: Staged) -> Staged:
    """New outputs without images: the staged image directory is empty."""
    shutil.rmtree(staged.images)
    staged.images.mkdir()
    return staged


@pytest.fixture
def on_replace(monkeypatch: pytest.MonkeyPatch) -> OnReplace:
    """Install a hook that runs before every Path.replace, and may fail it by raising."""
    original = Path.replace

    def install(hook: ReplaceHook) -> None:
        def hooked(source: Path, target: Path) -> Path:
            hook(source, target)
            return original(source, target)

        monkeypatch.setattr(Path, "replace", hooked)

    return install


def fail_replacement(step: int) -> ReplaceHook:
    """A hook that fails only the step-th replacement."""
    calls = 0

    def hook(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == step:
            raise OSError(f"failure at replace {step}")

    return hook


def transfers(*parents: Path) -> list[Path]:
    """The staging and recovery directories that publication made in the parents."""
    return [
        path
        for parent in parents
        for path in parent.iterdir()
        if path.name.startswith(".docx-publish-")
    ]


def assert_old(paths: _models.ConversionPaths) -> None:
    """The previous outputs are in place, and none of the new ones."""
    assert paths.output.read_text(encoding="utf-8") == "old markdown"
    assert (paths.images / "old.svg").read_bytes() == SVG
    assert (paths.pages / "01-old.md").read_text(encoding="utf-8") == "old page"
    assert not (paths.images / "new.svg").exists()
    assert not (paths.pages / "01-new.md").exists()


def assert_no_transfers(paths: _models.ConversionPaths) -> None:
    assert transfers(paths.output.parent, paths.images.parent) == []


def test_complete_batch_replaces_only_selected_artifacts(
    staged: Staged, paths: _models.ConversionPaths
) -> None:
    unrelated = paths.output.parent / "unrelated.md"
    unrelated.write_text("mine", encoding="utf-8")
    staged.publish(paths)
    assert paths.output.read_text(encoding="utf-8") == "new markdown"
    assert (paths.images / "new.svg").is_file()
    assert (paths.pages / "01-new.md").is_file()
    assert not (paths.pages / "01-old.md").exists()
    assert unrelated.read_text(encoding="utf-8") == "mine"
    assert_no_transfers(paths)


def test_without_images_no_image_directory_is_created(
    source: Path, without_images: Staged, tmp_path: Path
) -> None:
    paths = _publication.validate_paths(source, tmp_path / "fresh" / "document.md")
    _publication.publish_outputs(without_images.markdown, without_images.images, paths)
    assert paths.output.read_text(encoding="utf-8") == "new markdown"
    assert [path.name for path in paths.output.parent.iterdir()] == ["document.md"]


def test_without_images_the_previous_image_directory_and_its_parent_go(
    without_images: Staged, paths: _models.ConversionPaths
) -> None:
    without_images.publish(paths)
    assert paths.output.read_text(encoding="utf-8") == "new markdown"
    assert (paths.pages / "01-new.md").is_file()
    assert not paths.images.parent.exists()


def test_without_images_other_image_directories_stay(
    without_images: Staged, paths: _models.ConversionPaths
) -> None:
    other = paths.images.parent / "other"
    other.mkdir()
    (other / "kept.svg").write_bytes(SVG)
    without_images.publish(paths)
    assert not paths.images.exists()
    assert [path.name for path in paths.images.parent.iterdir()] == ["other"]


# Three backups, then two publications: nothing replaces the image directory.
@pytest.mark.parametrize(
    "step",
    [
        pytest.param(1, id="backing-up-markdown"),
        pytest.param(2, id="backing-up-images"),
        pytest.param(3, id="backing-up-pages"),
        pytest.param(4, id="publishing-markdown"),
        pytest.param(5, id="publishing-pages"),
    ],
)
def test_without_images_a_failure_restores_the_previous_image_directory(
    step: int, without_images: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    on_replace(fail_replacement(step))
    with pytest.raises(_models.ConversionError, match="previous outputs restored"):
        without_images.publish(paths)
    assert_old(paths)
    assert_no_transfers(paths)


# Each previous output is backed up, then each new one published.
@pytest.mark.parametrize(
    "step",
    [
        pytest.param(1, id="backing-up-markdown"),
        pytest.param(2, id="backing-up-images"),
        pytest.param(3, id="backing-up-pages"),
        pytest.param(4, id="publishing-markdown"),
        pytest.param(5, id="publishing-images"),
        pytest.param(6, id="publishing-pages"),
    ],
)
def test_every_backup_and_publish_failure_restores_whole_batch(
    step: int, staged: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    on_replace(fail_replacement(step))
    with pytest.raises(_models.ConversionError, match="previous outputs restored"):
        staged.publish(paths)
    assert_old(paths)
    assert_no_transfers(paths)


@pytest.mark.parametrize("step", [pytest.param(1, id="images"), pytest.param(2, id="pages")])
def test_each_directory_preparation_failure_cleans_staging(
    step: int, staged: Staged, paths: _models.ConversionPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = shutil.copytree
    calls = 0

    def fail_copy(source: Path, destination: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == step:
            destination.mkdir()
            raise OSError("partial directory copy")
        return Path(original(source, destination))

    monkeypatch.setattr(shutil, "copytree", fail_copy)
    with pytest.raises(_models.ConversionError, match="preparation failed"):
        staged.publish(paths)
    assert_old(paths)
    assert_no_transfers(paths)


def test_markdown_preparation_failure_never_starts_backups(
    staged: Staged, paths: _models.ConversionPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacements = mock.Mock()
    monkeypatch.setattr(shutil, "copy2", mock.Mock(side_effect=OSError("copy failed")))
    monkeypatch.setattr(Path, "replace", replacements)
    with pytest.raises(_models.ConversionError, match="preparation failed"):
        staged.publish(paths)
    replacements.assert_not_called()
    assert_old(paths)
    assert_no_transfers(paths)


def test_second_stage_creation_failure_cleans_prior_stage(
    staged: Staged, paths: _models.ConversionPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tempfile.mkdtemp
    calls = 0

    def fail_stage(*, prefix: str, dir: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("stage creation failed")
        return original(prefix=prefix, dir=dir)

    monkeypatch.setattr(tempfile, "mkdtemp", fail_stage)
    with pytest.raises(_models.ConversionError, match="preparation failed"):
        staged.publish(paths)
    assert_old(paths)
    assert_no_transfers(paths)


def test_failed_restore_retains_exact_backups_and_reports_both_errors(
    staged: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    def fail_restore(source: Path, target: Path) -> None:
        if source.name == "prepared" and target == paths.pages:
            raise OSError("original publication error")
        if source.name == "backup" and target == paths.images:
            raise OSError("original recovery error")

    on_replace(fail_restore)
    with pytest.raises(_models.ConversionError) as raised:
        staged.publish(paths)
    message = str(raised.value)
    assert "original publication error" in message
    assert "original recovery error" in message
    roots = transfers(paths.images.parent)
    assert len(roots) == 1
    backup = roots[0] / "backup"
    assert str(backup) in message
    assert (backup / "old.svg").read_bytes() == SVG
    assert (roots[0] / "recovery-new" / "new.svg").is_file()
    assert paths.output.read_text(encoding="utf-8") == "old markdown"


def test_failed_vacate_also_retains_recoverable_backup(
    staged: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    def fail_vacate(source: Path, target: Path) -> None:
        if source.name == "prepared" and target == paths.pages:
            raise OSError("publish failed")
        if source == paths.images and target.name == "recovery-new":
            raise OSError("cannot move replacement aside")

    on_replace(fail_vacate)
    with pytest.raises(_models.ConversionError, match="rollback failed") as raised:
        staged.publish(paths)
    roots = transfers(paths.images.parent)
    assert len(roots) == 1
    assert str(roots[0] / "backup") in str(raised.value)
    assert (roots[0] / "backup" / "old.svg").is_file()
    assert (paths.images / "new.svg").is_file()


def test_cleanup_error_after_success_does_not_claim_rollback(
    staged: Staged, paths: _models.ConversionPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = shutil.rmtree

    def fail_cleanup(path: Path) -> None:
        if path.parent == paths.images.parent:
            raise OSError("cleanup failed")
        original(path)

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)
    with pytest.raises(_models.ConversionError, match="All outputs were published"):
        staged.publish(paths)
    assert (paths.images / "new.svg").is_file()
    assert paths.output.read_text(encoding="utf-8") == "new markdown"


def test_renames_are_destination_local_even_with_foreign_scratch(
    staged: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    observed: list[tuple[Path, Path]] = []

    def assert_local(source: Path, target: Path) -> None:
        observed.append((source, target))
        assert source.stat().st_dev == target.parent.stat().st_dev
        assert source != staged.markdown
        assert source != staged.images

    on_replace(assert_local)
    staged.publish(paths)
    assert len(observed) == 6


def test_real_cross_filesystem_scratch_publication(paths: _models.ConversionPaths) -> None:
    # tmp_path cannot be on another filesystem; the scratch goes before the outputs are read.
    with tempfile.TemporaryDirectory(prefix="docx-cross-fs-", dir="/dev/shm") as directory:
        scratch = Path(directory)
        assert scratch.stat().st_dev != paths.output.parent.stat().st_dev
        markdown = scratch / "final.md"
        images = scratch / "images"
        pages = scratch / "pages"
        markdown.write_text("cross filesystem", encoding="utf-8")
        images.mkdir()
        (images / "image.png").write_bytes(PNG)
        pages.mkdir()
        (pages / "01-child.md").write_text("# Child\n", encoding="utf-8")
        _publication.publish_outputs(markdown, images, paths, pages)
    assert paths.output.read_text(encoding="utf-8") == "cross filesystem"
    assert (paths.images / "image.png").is_file()
    assert_no_transfers(paths)


def test_arbitrary_image_destination_cannot_bypass_derivation(
    staged: Staged, paths: _models.ConversionPaths, tmp_path: Path
) -> None:
    arbitrary = replace(paths, images=tmp_path / "arbitrary-images")
    with pytest.raises(_models.ConversionError, match="must derive"):
        staged.publish(arbitrary)
    assert_old(paths)


def test_rollback_restores_absence_of_new_outputs_and_parents(
    staged: Staged, paths: _models.ConversionPaths, tmp_path: Path, on_replace: OnReplace
) -> None:
    fresh = _publication.validate_paths(
        paths.source, tmp_path / "new-parent" / "document.md", split_depth=1
    )

    def fail_last(source: Path, target: Path) -> None:
        if source.name == "prepared" and target == fresh.pages:
            raise OSError("new-page publication failed")

    on_replace(fail_last)
    with pytest.raises(_models.ConversionError, match="previous outputs restored"):
        staged.publish(fresh)
    assert not fresh.output.parent.exists()
    assert_old(paths)


def test_failed_rollback_keeps_backups_for_every_failed_restore(
    staged: Staged, paths: _models.ConversionPaths, on_replace: OnReplace
) -> None:
    def fail_restore(source: Path, target: Path) -> None:
        if source.name == "prepared" and target == paths.pages:
            raise OSError("original error")
        if source.name == "backup":
            raise OSError("recovery error")

    on_replace(fail_restore)
    with pytest.raises(_models.ConversionError) as raised:
        staged.publish(paths)
    roots = transfers(paths.output.parent, paths.images.parent)
    assert len(roots) == 3
    for root in roots:
        assert (root / "backup").exists()
        assert str(root / "backup") in str(raised.value)
