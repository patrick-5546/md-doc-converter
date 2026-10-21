"""Checkout locations used by the tests; the package itself does not know its checkout."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
