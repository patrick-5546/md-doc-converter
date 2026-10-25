r"""Where the toolchain lives and the environment its subprocesses need.

``md-doc-converter setup`` installs the pinned Pandoc, the pinned npm packages and the
chrome-headless-shell build that their Puppeteer pins under one directory: by default
``%LOCALAPPDATA%\md-doc-converter`` on Windows, and elsewhere ``$XDG_DATA_HOME/md-doc-converter``
(``~/.local/share/md-doc-converter``). ``MD_DOC_CONVERTER_HOME`` selects another directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from md_doc_converter import executables
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
# The npm package of Mermaid's command line, mmdc.
MERMAID_CLI = "@mermaid-js/mermaid-cli"


@dataclass(frozen=True)
class Platform:
    """A platform that setup supports, and how the toolchain differs on it."""

    name: str
    windows: bool
    # Puppeteer's name for the platform, which begins the folder of each browser build in its
    # cache, and the end of the name of the folder in a build that holds the browser.
    browser_platform: str
    browser_folder: str

    def executable(self, name: str) -> str:
        """The file name of the executable *name*."""
        return f"{name}.exe" if self.windows else name


LINUX = Platform("Linux x86_64", windows=False, browser_platform="linux", browser_folder="linux64")
WINDOWS = Platform("Windows x86_64", windows=True, browser_platform="win64", browser_folder="win64")
# The supported platforms, by sys.platform and machine.
PLATFORMS = {("linux", "x86_64"): LINUX, ("win32", "x86_64"): WINDOWS}


def current_platform(system: str = sys.platform, machine: str | None = None) -> Platform | None:
    """This computer's platform, or the one named by *system* and *machine*, if supported."""
    machine = (platform.machine() if machine is None else machine).lower()
    return PLATFORMS.get((system, "x86_64" if machine == "amd64" else machine))


def host_platform() -> Platform:
    """This computer's platform, or on another one, the supported platform of its kind.

    Each supported system has one platform, so the system picks it without the machine type.
    Every command finds its toolchain with this, and on Windows, platform.machine() makes a
    WMI query that takes a noticeable part of a command's start.
    """
    return WINDOWS if sys.platform == "win32" else LINUX


def default_home(
    environment: Mapping[str, str] | None = None, system: Platform | None = None
) -> Path:
    environment = os.environ if environment is None else environment
    configured = environment.get(HOME_VARIABLE, "").strip()
    if configured:
        # Unlike Path.resolve, abspath keeps the configured spelling of symlinked folders.
        return Path(os.path.abspath(Path(configured).expanduser()))  # noqa: PTH100
    if (system or host_platform()).windows:
        data_home = environment.get("LOCALAPPDATA", "").strip()
        fallback = Path.home() / "AppData" / "Local"
    else:
        data_home = environment.get("XDG_DATA_HOME", "").strip()
        fallback = Path.home() / ".local" / "share"
    base = Path(data_home) if data_home and Path(data_home).is_absolute() else fallback
    return base / "md-doc-converter"


@dataclass(frozen=True)
class Toolchain:
    root: Path
    system: Platform = field(default_factory=host_platform)

    @property
    def node_dir(self) -> Path:
        return self.root / "node"

    @property
    def node_modules(self) -> Path:
        return self.node_dir / "node_modules"

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
        return self.bin_dir / self.system.executable("pandoc")

    def node_script(self, package: str, command: str) -> Path | None:
        """The script of the command *command* of the installed npm package *package*.

        Node.js runs it, as npm's wrappers in node_modules/.bin would; Windows cannot run
        the shell scripts among them. The package's package.json names the script.
        """
        folder = self.node_modules / package
        try:
            manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        commands = manifest.get("bin") if isinstance(manifest, dict) else None
        # npm's "bin" is a script by command name, or one script named after the package.
        if isinstance(commands, str) and command == package.rpartition("/")[2]:
            commands = {command: commands}
        script = commands.get(command) if isinstance(commands, dict) else None
        if not isinstance(script, str) or not (folder / script).is_file():
            return None
        return folder / script

    def browser_builds(self) -> list[Path]:
        """The installed chrome-headless-shell executables, in Puppeteer's cache layout."""
        system = self.system
        build = f"{system.browser_platform}-*/{BROWSER}-{system.browser_folder}"
        return sorted(self.puppeteer_cache.glob(f"{BROWSER}/{build}/{system.executable(BROWSER)}"))


def toolchain(environment: Mapping[str, str] | None = None) -> Toolchain:
    return Toolchain(default_home(environment))


def _same_folder(first: str, second: str) -> bool:
    """Whether two PATH entries name the same folder, ignoring case on Windows."""
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def prefer_installed_tools(environment: MutableMapping[str, str] | None = None) -> None:
    """Put the toolchain's ``bin/`` first on PATH, so its pinned Pandoc is the one used."""
    environment = os.environ if environment is None else environment
    bin_dir = toolchain(environment).bin_dir
    if not bin_dir.is_dir():
        return
    entries = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
    environment["PATH"] = os.pathsep.join(
        [str(bin_dir), *(entry for entry in entries if not _same_folder(entry, str(bin_dir)))]
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
    tools.stamp.write_text(manifest_digest() + "\n", encoding="utf-8", newline="\n")


def render_environment(tools: Toolchain, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment for Pandoc, its filters and the PDF printer."""
    environment = dict(os.environ if base is None else base)
    search = [str(tools.bin_dir)]
    if environment.get("PATH"):
        search.append(environment["PATH"])
    environment["PATH"] = os.pathsep.join(search)
    environment["PUPPETEER_CACHE_DIR"] = str(tools.puppeteer_cache)
    # For assets/mermaid.lua, which runs mermaid-cli's script with Node.js, and assets/katex.lua,
    # which runs assets/katex-render.mjs with it.
    environment["MD_DOC_CONVERTER_NODE"] = executables.which("node", environment["PATH"]) or ""
    mmdc = tools.node_script(MERMAID_CLI, "mmdc")
    environment["MD_DOC_CONVERTER_MMDC"] = "" if mmdc is None else str(mmdc)
    environment["MD_DOC_CONVERTER_NODE_MODULES"] = str(tools.node_modules)
    environment["MD_DOC_CONVERTER_MERMAID_CONFIG"] = str(asset_path("mermaid-config.json"))
    return environment
