"""Paths of files shipped inside the package, and the installed package version."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DISTRIBUTION = "md-doc-converter"


def asset_path(name: str) -> Path:
    return PACKAGE_DIR / "assets" / name


def package_version() -> str:
    # Every command imports this module, but only --version and doctor need the version, and
    # importing importlib.metadata takes a noticeable part of a command's start on Windows.
    from importlib import metadata

    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "(not installed)"
