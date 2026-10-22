"""Where the toolchain lives and the environment its subprocesses need.

``md-doc-converter setup`` installs the pinned Pandoc, the pinned npm packages and the
chrome-headless-shell build that their Puppeteer pins under one directory, by default
``$XDG_DATA_HOME/md-doc-converter`` (``~/.local/share/md-doc-converter``).
``MD_DOC_CONVERTER_HOME`` selects another directory.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from md_doc_converter.resources import asset_path

HOME_VARIABLE = "MD_DOC_CONVERTER_HOME"
MANIFEST_DIR = Path(__file__).resolve().parent
MANIFESTS = ("package.json", "package-lock.json")
STAMP_NAME = ".md-doc-converter-stamp"
# mermaid-cli 12 needs Node.js 22.13.
NODE_MINIMUM = (22, 13, 0)
# Converted Markdown depends on the Pandoc version; the tests expect this one.
PANDOC_VERSION = "3.12.1"
# The one browser that setup installs, shared by Mermaid and the PDF printer.
BROWSER = "chrome-headless-shell"


def default_home(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    configured = environment.get(HOME_VARIABLE, "").strip()
    if configured:
        # Unlike Path.resolve, abspath keeps the configured spelling of symlinked folders.
        return Path(os.path.abspath(Path(configured).expanduser()))  # noqa: PTH100
    data_home = environment.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home) if data_home and Path(data_home).is_absolute() else None
    return (base or Path.home() / ".local" / "share") / "md-doc-converter"


@dataclass(frozen=True)
class Toolchain:
    root: Path

    @property
    def node_dir(self) -> Path:
        return self.root / "node"

    @property
    def node_modules(self) -> Path:
        return self.node_dir / "node_modules"

    @property
    def node_bin(self) -> Path:
        return self.node_modules / ".bin"

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def puppeteer_cache(self) -> Path:
        return self.root / "puppeteer"

    @property
    def stamp(self) -> Path:
        return self.node_dir / STAMP_NAME

    @property
    def pandoc(self) -> Path:
        return self.bin_dir / "pandoc"

    def node_executable(self, name: str) -> Path:
        return self.node_bin / name

    def browser_builds(self) -> list[Path]:
        """The installed chrome-headless-shell executables, in Puppeteer's cache layout."""
        return sorted(self.puppeteer_cache.glob(f"{BROWSER}/linux-*/{BROWSER}-linux64/{BROWSER}"))


def toolchain(environment: Mapping[str, str] | None = None) -> Toolchain:
    return Toolchain(default_home(environment))


def prefer_installed_tools(environment: MutableMapping[str, str] | None = None) -> None:
    """Put the toolchain's ``bin/`` first on PATH, so its pinned Pandoc is the one used."""
    environment = os.environ if environment is None else environment
    bin_dir = toolchain(environment).bin_dir
    if not bin_dir.is_dir():
        return
    entries = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
    environment["PATH"] = os.pathsep.join(
        [str(bin_dir), *(entry for entry in entries if entry != str(bin_dir))]
    )


def manifest_digest() -> str:
    """Identify the bundled npm manifests, so an upgrade can tell stale packages apart."""
    digest = hashlib.sha256()
    for name in MANIFESTS:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((MANIFEST_DIR / name).read_bytes())
    return digest.hexdigest()


def packages_current(tools: Toolchain) -> bool:
    try:
        recorded = tools.stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return recorded == manifest_digest() and tools.node_modules.is_dir()


def write_stamp(tools: Toolchain) -> None:
    tools.stamp.write_text(manifest_digest() + "\n", encoding="utf-8")


def render_environment(tools: Toolchain, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment for Pandoc, its filters and the PDF printer."""
    environment = dict(os.environ if base is None else base)
    search = [str(tools.node_bin), str(tools.bin_dir)]
    if environment.get("PATH"):
        search.append(environment["PATH"])
    environment["PATH"] = os.pathsep.join(search)
    environment["PUPPETEER_CACHE_DIR"] = str(tools.puppeteer_cache)
    # For assets/mermaid.lua and assets/katex.js.
    environment["MD_DOC_CONVERTER_MMDC"] = str(tools.node_executable("mmdc"))
    environment["MD_DOC_CONVERTER_NODE_MODULES"] = str(tools.node_modules)
    environment["MD_DOC_CONVERTER_MERMAID_CONFIG"] = str(asset_path("mermaid-config.json"))
    return environment
