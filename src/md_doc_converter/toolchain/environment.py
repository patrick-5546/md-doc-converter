"""Where the renderer toolchain lives and the environment its subprocesses need.

``md-doc-converter setup`` installs the pinned npm packages (with the browsers that
Puppeteer downloads) and pandoc-katex under one directory, by default
``$XDG_DATA_HOME/md-doc-converter`` (``~/.local/share/md-doc-converter``).
``MD_DOC_CONVERTER_HOME`` selects another directory.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from md_doc_converter.resources import asset_path

HOME_VARIABLE = "MD_DOC_CONVERTER_HOME"
MANIFEST_DIR = Path(__file__).resolve().parent
MANIFESTS = ("package.json", "package-lock.json")
STAMP_NAME = ".md-doc-converter-stamp"
NODE_MINIMUM = (22, 12, 0)
PANDOC_KATEX_VERSION = "0.1.11"


def default_home(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    configured = environment.get(HOME_VARIABLE, "").strip()
    if configured:
        return Path(os.path.abspath(Path(configured).expanduser()))
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
    def pandoc_katex(self) -> Path:
        return self.bin_dir / "pandoc-katex"

    def node_executable(self, name: str) -> Path:
        return self.node_bin / name


def toolchain(environment: Mapping[str, str] | None = None) -> Toolchain:
    return Toolchain(default_home(environment))


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
    environment["MERMAID_FILTER_CMD_MMDC"] = str(tools.node_executable("mmdc"))
    environment.setdefault("MERMAID_FILTER_FORMAT", "svg")
    environment["MERMAID_FILTER_MERMAID_CONFIG"] = str(asset_path("mermaid-config.json"))
    # SCALE only affects PNG output; SVGs are scaled with CSS `zoom` on the images tagged
    # with IMAGE_CLASS (see assets/style-overrides.css).
    environment["MERMAID_FILTER_SCALE"] = "0.75"
    environment["MERMAID_FILTER_IMAGE_CLASS"] = "mermaid-diagram"
    return environment
