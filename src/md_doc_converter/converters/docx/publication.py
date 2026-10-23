"""Guard destinations and publish DOCX artifacts with recoverable replacements."""

from __future__ import annotations

import shutil
import stat
import tempfile
from pathlib import Path

from md_doc_converter.converters.docx.models import (
    ConversionError,
    ConversionPaths,
    PreparedArtifact,
)


def requested_output(input_path: Path, output_path: Path | None) -> Path:
    return output_path if output_path is not None else input_path.with_suffix(".md")


def _entry_path(path: Path) -> Path:
    """Resolve parent aliases without following a destination's final symlink."""
    try:
        return path.parent.resolve() / path.name
    except (OSError, RuntimeError) as error:
        raise ConversionError(f"Cannot resolve path {path}: {error}") from error


def overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def same_file(first: Path, second: Path) -> bool:
    return first.exists() and second.exists() and first.samefile(second)


def _protected(path: Path, related: tuple[Path, ...]) -> bool:
    home = Path.home().resolve()
    roots = {Path(path.anchor), home, *home.parents}
    roots.update(
        Path(name)
        for name in (
            "/bin",
            "/boot",
            "/dev",
            "/etc",
            "/home",
            "/lib",
            "/lib64",
            "/media",
            "/mnt",
            "/opt",
            "/proc",
            "/root",
            "/run",
            "/sbin",
            "/srv",
            "/sys",
            "/tmp",
            "/usr",
            "/var",
            "/var/tmp",
        )
    )
    for candidate in (path, *related):
        for parent in (candidate, *candidate.parents):
            if (parent / ".git").exists():
                roots.add(parent)
    return path in roots or any(path in root.parents for root in roots) or path.is_mount()


def _check_parent_types(path: Path) -> None:
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise ConversionError(f"Destination parent is not a directory: {parent}")


def _check_tree(path: Path, source: Path) -> None:
    """Inspect only a selected destination, never follow directory symlinks."""
    if not path.exists():
        return
    pending = [path]
    while pending:
        directory = pending.pop()
        if (directory / ".git").exists():
            raise ConversionError(
                f"Selected destination contains a protected repository: {directory}"
            )
        for entry in directory.iterdir():
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ConversionError(f"Unsafe symlink in selected destination: {entry}")
            if stat.S_ISDIR(mode):
                if entry.is_mount():
                    raise ConversionError(f"Mounted directory inside selected destination: {entry}")
                pending.append(entry)
            elif not stat.S_ISREG(mode):
                raise ConversionError(f"Nonregular entry in selected destination: {entry}")
            elif entry.samefile(source):
                raise ConversionError(f"Selected destination contains a source alias: {entry}")


def validate_paths(
    input_path: Path,
    output_path: Path | None,
    *,
    split_depth: int = 0,
    replace_generated: bool = False,
    keep_intermediates: Path | None = None,
) -> ConversionPaths:
    if not 0 <= split_depth <= 6:
        raise ConversionError(f"Split depth must be between 0 and 6, got {split_depth}")
    if input_path.suffix.casefold() != ".docx":
        raise ConversionError(f"Input must be a DOCX file: {input_path}")
    if not input_path.is_file():
        raise ConversionError(f"Input DOCX is not a regular file: {input_path}")
    source_entry = _entry_path(input_path)
    source = input_path.resolve(strict=True)
    output_entry = _entry_path(requested_output(input_path, output_path))
    if output_entry.suffix.casefold() != ".md" or output_entry.stem in {"", ".", ".."}:
        raise ConversionError(
            f"Output must have a nonempty Markdown filename ending in .md: {output_entry}"
        )
    if output_entry.is_symlink():
        raise ConversionError(f"Unsafe destination symlink: {output_entry}")
    image_parent = output_entry.parent / ".images"
    if image_parent.is_symlink():
        raise ConversionError(f"The .images folder must not be a symbolic link: {image_parent}")
    images = _entry_path(image_parent / output_entry.stem)
    pages = _entry_path(output_entry.with_suffix(""))
    diagnostics = _entry_path(keep_intermediates) if keep_intermediates is not None else None
    paths = ConversionPaths(
        source,
        source_entry,
        output_entry,
        images,
        pages,
        diagnostics,
        output_path is not None,
        split_depth,
        replace_generated,
    )
    _validate_destinations(paths, diagnostics_empty=True)
    return paths


def _validate_destinations(paths: ConversionPaths, *, diagnostics_empty: bool) -> None:
    output, images, pages = paths.output, paths.images, paths.pages
    if images != output.parent / ".images" / output.stem or pages != output.with_suffix(""):
        raise ConversionError("Image and page destinations must derive from the Markdown output")
    related = (paths.source, paths.source_entry, output, images, pages)
    if (output.parent / ".images").is_symlink() or _entry_path(images) != images:
        raise ConversionError(f"The .images folder must not be a symbolic link: {images.parent}")
    if any(_entry_path(path) != path for path in (output, pages)):
        raise ConversionError("Destination parent changed through a symlink")
    if output.exists() and not output.is_file():
        raise ConversionError(f"Markdown destination is not a regular file: {output}")
    if output == paths.source or output == paths.source_entry or same_file(output, paths.source):
        raise ConversionError(f"Markdown destination aliases the source DOCX: {output}")
    if output.exists() and not paths.explicit_output and not paths.replace_generated:
        raise ConversionError(
            f"Markdown output already exists: {output}; name it with -o/--output, or use "
            "--replace-generated, to replace it"
        )
    if overlaps(images, pages):
        raise ConversionError(f"Image and page destinations overlap: {images}, {pages}")
    directories = [images]
    if paths.split_depth:
        directories.append(pages)
    if paths.diagnostics is not None:
        diagnostic = paths.diagnostics
        for protected in related:
            if overlaps(diagnostic, protected):
                raise ConversionError(f"Diagnostic directory overlaps {protected}: {diagnostic}")
        directories.append(diagnostic)
    for path in (output, *directories):
        _check_parent_types(path)
        if path.is_symlink():
            raise ConversionError(f"Unsafe destination symlink: {path}")
        if _protected(path, related):
            raise ConversionError(
                f"Destination is a protected filesystem, home, or repository root: {path}"
            )
        if path != output and overlaps(path, output):
            raise ConversionError(f"Destination overlaps Markdown output: {path}")
    for directory in directories:
        if any(
            source == directory or directory in source.parents
            for source in (paths.source, paths.source_entry)
        ):
            raise ConversionError(f"Destination contains the source DOCX: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ConversionError(f"Destination is not a directory: {directory}")
        if directory.exists() and any(directory.iterdir()):
            if directory == paths.diagnostics:
                if diagnostics_empty:
                    raise ConversionError(f"Intermediate directory is not empty: {directory}")
            elif not paths.replace_generated:
                raise ConversionError(
                    f"Nonempty generated directory requires --replace-generated: {directory}"
                )
        if directory != paths.diagnostics:
            _check_tree(directory, paths.source)


def _mkdir_parents(parent: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise ConversionError(f"Publication parent is not a directory: {current}")
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _cleanup_transfers(roots: list[Path], created: list[Path]) -> list[str]:
    errors: list[str] = []
    for root in reversed(roots):
        try:
            shutil.rmtree(root)
        except OSError as error:
            errors.append(f"Cannot remove staging/recovery directory {root}: {error}")
    for directory in reversed(created):
        try:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError as error:
            errors.append(f"Cannot remove empty parent {directory}: {error}")
    return errors


def publish_outputs(
    staged_markdown: Path,
    staged_images: Path,
    paths: ConversionPaths,
    staged_pages: Path | None = None,
) -> None:
    """Prepare the entire batch locally; retain recovery data if rollback fails.

    A document without images publishes no image directory: a previous one is removed, with
    its .images parent when that leaves the parent empty.
    """
    if (staged_pages is not None) != bool(paths.split_depth):
        raise ConversionError("Staged pages must match the selected split depth")
    _validate_destinations(paths, diagnostics_empty=False)
    if staged_images.is_symlink() or not staged_images.is_dir():
        raise ConversionError(f"Invalid staged artifact: {staged_images}")
    images = staged_images if any(staged_images.iterdir()) else None
    removing_images = images is None and paths.images.exists()
    artifacts: list[tuple[Path | None, Path, bool]] = [(staged_markdown, paths.output, False)]
    if images is not None or removing_images:
        artifacts.append((images, paths.images, True))
    if staged_pages is not None:
        artifacts.append((staged_pages, paths.pages, True))
    roots: list[Path] = []
    created: list[Path] = []
    prepared: list[PreparedArtifact] = []
    try:
        for staged, destination, directory in artifacts:
            if staged is not None and (
                staged.is_symlink() or (not staged.is_dir() if directory else not staged.is_file())
            ):
                raise ConversionError(f"Invalid staged artifact: {staged}")
            _mkdir_parents(destination.parent, created)
            root = Path(tempfile.mkdtemp(prefix=".docx-publish-", dir=destination.parent))
            roots.append(root)
            transfer: Path | None = None
            if staged is not None:
                transfer = root / "prepared"
                if directory:
                    shutil.copytree(staged, transfer)
                else:
                    shutil.copy2(staged, transfer)
            prepared.append(PreparedArtifact(root, destination, transfer, root / "backup"))
    except (OSError, ConversionError) as error:
        cleanup = _cleanup_transfers(roots, created)
        detail = f"; {'; '.join(cleanup)}" if cleanup else ""
        raise ConversionError(
            f"Publication preparation failed; final outputs unchanged: {error}{detail}"
        ) from error
    step = ""
    try:
        for artifact in prepared:
            step = f"backing up {artifact.destination}"
            if artifact.destination.exists() or artifact.destination.is_symlink():
                if artifact.destination.is_symlink():
                    raise ConversionError(f"Destination became a symlink: {artifact.destination}")
                artifact.destination.replace(artifact.backup)
                artifact.backed_up = True
        for artifact in prepared:
            if artifact.prepared is None:
                continue
            step = f"publishing {artifact.destination}"
            artifact.prepared.replace(artifact.destination)
            artifact.published = True
    except (OSError, ConversionError) as error:
        recovery_errors: list[str] = []
        for artifact in reversed(prepared):
            if not artifact.backed_up and not artifact.published:
                continue
            try:
                if artifact.destination.exists() or artifact.destination.is_symlink():
                    artifact.destination.replace(artifact.root / "recovery-new")
                if artifact.backed_up:
                    if not artifact.backup.exists():
                        raise ConversionError(f"Recovery backup is missing: {artifact.backup}")
                    artifact.backup.replace(artifact.destination)
                    artifact.backed_up = False
            except (OSError, ConversionError) as recovery:
                recovery_errors.append(f"Restoring {artifact.destination}: {recovery}")
        if recovery_errors:
            backups = [str(artifact.backup) for artifact in prepared if artifact.backed_up]
            raise ConversionError(
                f"Publication failed while {step}: {error}; rollback failed: "
                f"{'; '.join(recovery_errors)}. Retained backups: "
                f"{', '.join(backups) or '(none; inspect retained recovery directories)'}. "
                f"Recovery directories retained: {', '.join(str(root) for root in roots)}"
            ) from error
        cleanup = _cleanup_transfers(roots, created)
        detail = f"; {'; '.join(cleanup)}" if cleanup else ""
        raise ConversionError(
            f"Publication failed while {step}: {error}; previous outputs restored{detail}"
        ) from error
    # The removed image directory may have been the last one in its .images parent.
    emptied = [paths.images.parent] if removing_images else []
    cleanup = _cleanup_transfers(roots, created + emptied)
    if cleanup:
        raise ConversionError(
            "All outputs were published, but staging/backup cleanup failed: " + "; ".join(cleanup)
        )
