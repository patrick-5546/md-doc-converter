"""Paths of files shipped inside the package, and the installed package version."""

from importlib import metadata
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DISTRIBUTION = "md-doc-converter"


def asset_path(name: str) -> Path:
    return PACKAGE_DIR / "assets" / name


def package_version() -> str:
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "(not installed)"
