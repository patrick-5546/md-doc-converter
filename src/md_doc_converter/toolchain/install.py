"""Install the pinned toolchain: Pandoc, and the npm packages with their browser."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from md_doc_converter.toolchain.checks import (
    NODE_FIX,
    check_browser,
    check_node,
    installed_version,
)
from md_doc_converter.toolchain.environment import (
    BROWSER,
    MANIFEST_DIR,
    MANIFESTS,
    PANDOC_VERSION,
    Toolchain,
    packages_current,
    toolchain,
    write_stamp,
)

DOWNLOAD_TIMEOUT = 120


@dataclass(frozen=True)
class Archive:
    """A pinned release archive holding one executable that `setup` installs into bin/."""

    name: str
    version: str
    file: str
    url: str
    sha256: str
    member: PurePosixPath

    @property
    def executable(self) -> str:
        return self.member.name


PANDOC = Archive(
    "Pandoc",
    PANDOC_VERSION,
    f"pandoc-{PANDOC_VERSION}-linux-amd64.tar.gz",
    f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/"
    f"pandoc-{PANDOC_VERSION}-linux-amd64.tar.gz",
    "d0c90410e90204c9ca83b8539fac5c7aed01fd537207e4585849f8abc5df20b8",
    PurePosixPath(f"pandoc-{PANDOC_VERSION}/bin/pandoc"),
)
NO_RENDER_HINT = (
    "or run `md-doc-converter setup --no-render` to install only Pandoc, for conversion"
)


class SetupError(Exception):
    """The toolchain could not be installed."""


def supported_platform() -> bool:
    return sys.platform.startswith("linux") and platform.machine().lower() in {"x86_64", "amd64"}


def install_packages(tools: Toolchain, npm: str, *, force: bool) -> bool:
    """Install the bundled npm lockfile; return whether anything was installed."""
    if packages_current(tools) and not force:
        print(f"npm packages: up to date in {tools.node_modules}")
        return False
    tools.node_dir.mkdir(parents=True, exist_ok=True)
    tools.stamp.unlink(missing_ok=True)
    for name in MANIFESTS:
        shutil.copyfile(MANIFEST_DIR / name, tools.node_dir / name)
    print(f"npm packages: installing into {tools.node_modules}", flush=True)
    # No package needs its install scripts; Puppeteer's would download browsers that
    # rendering does not use.
    environment = {**os.environ, "PUPPETEER_CACHE_DIR": str(tools.puppeteer_cache)}
    command = [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    try:
        subprocess.run(command, cwd=tools.node_dir, env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise SetupError(f"npm ci failed: {error}") from error
    install_browser(tools, environment)
    write_stamp(tools)
    return True


def install_browser(tools: Toolchain, environment: dict[str, str]) -> None:
    """Install the chrome-headless-shell build that the installed Puppeteer pins.

    Mermaid and the PDF printer share it; builds from earlier installations are removed.
    """
    shutil.rmtree(tools.puppeteer_cache, ignore_errors=True)
    print(f"browser: installing {BROWSER} into {tools.puppeteer_cache}", flush=True)
    command = [str(tools.node_executable("puppeteer")), "browsers", "install", BROWSER]
    try:
        subprocess.run(command, cwd=tools.node_dir, env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise SetupError(f"browser install failed: {error}") from error
    if check_browser(tools).status != "ok":
        raise SetupError(
            f"Puppeteer did not install {BROWSER} into {tools.puppeteer_cache}; check that "
            "PUPPETEER_SKIP_DOWNLOAD is not set"
        )


def _download(archive: Archive, destination: Path) -> None:
    request = urllib.request.Request(archive.url, headers={"User-Agent": "md-doc-converter-setup"})
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
            destination.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    except OSError as error:  # Includes URLError and timeouts.
        raise SetupError(
            f"cannot download {archive.url}: {error}. Download {archive.file} another way, put "
            "it in a folder and run `md-doc-converter setup --downloads FOLDER`"
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_executable(archive: Archive, tarball: Path, destination: Path) -> None:
    """Copy only the expected executable out of the archive, whatever else it contains."""
    try:
        with tarfile.open(tarball, "r:gz") as bundle:
            for member in bundle.getmembers():
                if member.isfile() and PurePosixPath(member.name) == archive.member:
                    stream = bundle.extractfile(member)
                    if stream is None:
                        break
                    with stream, destination.open("wb") as output:
                        shutil.copyfileobj(stream, output)
                    return
    except (OSError, tarfile.TarError) as error:
        raise SetupError(f"cannot read {tarball}: {error}") from error
    raise SetupError(f"{archive.file} does not contain {archive.member}")


def install_executable(
    tools: Toolchain, archive: Archive, *, force: bool, downloads: Path | None = None
) -> bool:
    """Install the checksum-pinned executable of *archive*; return whether it was installed."""
    target = tools.bin_dir / archive.executable
    label = archive.name.lower()
    if installed_version(target) == archive.version and not force:
        print(f"{label}: up to date at {target}")
        return False
    tools.bin_dir.mkdir(parents=True, exist_ok=True)
    local = downloads / archive.file if downloads is not None else None
    with tempfile.TemporaryDirectory(prefix=f".{archive.executable}-", dir=tools.bin_dir) as work:
        tarball = Path(work) / archive.file
        if local is not None and local.is_file():
            print(f"{label}: using {local}")
            try:
                shutil.copyfile(local, tarball)
            except OSError as error:
                raise SetupError(f"cannot read {local}: {error}") from error
        else:
            print(f"{label}: downloading {archive.url}")
            _download(archive, tarball)
        digest = _sha256(tarball)
        if digest != archive.sha256:
            raise SetupError(f"{archive.file} has SHA-256 {digest}, expected {archive.sha256}")
        staged = Path(work) / archive.executable
        _extract_executable(archive, tarball, staged)
        staged.chmod(0o755)
        staged.replace(target)
    if installed_version(target) != archive.version:
        raise SetupError(f"the installed {target} does not report version {archive.version}")
    print(f"{label}: installed {archive.version} at {target}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter setup",
        description=(
            "Install the pinned toolchain into MD_DOC_CONVERTER_HOME, by default "
            "~/.local/share/md-doc-converter: Pandoc, and for rendering, Mermaid, KaTeX and "
            "Paged.js from npm with the headless browser they share. Rendering needs Node.js "
            "22.13+ with npm."
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="Reinstall even when the toolchain is up to date"
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Install only Pandoc, for Word conversion without Node.js",
    )
    parser.add_argument(
        "--downloads",
        type=Path,
        metavar="DIR",
        help=(
            f"Use the pinned Pandoc archive, {PANDOC.file}, from DIR when present instead "
            "of downloading it; its checksum is still verified"
        ),
    )
    arguments = parser.parse_args(argv)
    tools = toolchain()
    render = not arguments.no_render
    try:
        if not supported_platform():
            raise SetupError(
                f"setup supports Linux x86_64 only, not {sys.platform} {platform.machine()}"
            )
        downloads = arguments.downloads
        if downloads is not None and not downloads.is_dir():
            raise SetupError(f"--downloads must be a folder: {downloads}")
        npm = None
        if render:
            node = check_node()
            if node.status != "ok":
                raise SetupError(f"node: {node.detail}. {node.fix}, {NO_RENDER_HINT}.")
            npm = shutil.which("npm")
            if npm is None:
                raise SetupError(f"npm was not found on PATH. {NODE_FIX}, {NO_RENDER_HINT}.")
        print(f"Toolchain: {tools.root}")
        install_executable(tools, PANDOC, force=arguments.force, downloads=downloads)
        if npm is not None:
            install_packages(tools, npm, force=arguments.force)
    except SetupError as error:
        print(f"md-doc-converter setup: {error}", file=sys.stderr)
        return 1
    if render:
        print("Done. Run `md-doc-converter doctor` to check every prerequisite.")
    else:
        print("Done: Pandoc is installed for conversion. Rendering needs a full `setup`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
