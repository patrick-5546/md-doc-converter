"""Install the pinned renderer toolchain: npm packages, their browsers and pandoc-katex."""

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
from pathlib import Path, PurePosixPath

from md_doc_converter.toolchain.checks import NODE_FIX, check_node, check_pandoc_katex
from md_doc_converter.toolchain.environment import (
    MANIFEST_DIR,
    MANIFESTS,
    PANDOC_KATEX_VERSION,
    Toolchain,
    packages_current,
    toolchain,
    write_stamp,
)

PANDOC_KATEX_ARCHIVE = f"pandoc-katex-{PANDOC_KATEX_VERSION}-x86_64-unknown-linux-musl.tar.gz"
PANDOC_KATEX_URL = (
    "https://github.com/xu-cheng/pandoc-katex/releases/download/"
    f"{PANDOC_KATEX_VERSION}/{PANDOC_KATEX_ARCHIVE}"
)
PANDOC_KATEX_SHA256 = "5c7b5b45df27d3fe2af2fd48ef9408b43844a59c8229cfdf5f8379a388e7052a"
PANDOC_KATEX_MEMBER = PurePosixPath("bin/pandoc-katex")
DOWNLOAD_TIMEOUT = 120


class SetupError(Exception):
    """The renderer toolchain could not be installed."""


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
    print(f"npm packages: installing into {tools.node_modules}")
    # Puppeteer downloads its browsers during installation and finds them again when
    # rendering through the same cache directory.
    environment = {**os.environ, "PUPPETEER_CACHE_DIR": str(tools.puppeteer_cache)}
    try:
        subprocess.run(
            [npm, "ci", "--no-audit", "--no-fund"], cwd=tools.node_dir, env=environment, check=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SetupError(f"npm ci failed: {error}") from error
    write_stamp(tools)
    return True


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "md-doc-converter-setup"})
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
            destination.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    except OSError as error:  # Includes URLError and timeouts.
        raise SetupError(
            f"cannot download {url}: {error}. Download it another way and run "
            "`md-doc-converter setup --pandoc-katex-archive PATH`"
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_binary(archive: Path, destination: Path) -> None:
    """Copy only the expected executable out of the archive, whatever else it contains."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if member.isfile() and PurePosixPath(member.name) == PANDOC_KATEX_MEMBER:
                    stream = bundle.extractfile(member)
                    if stream is None:
                        break
                    with stream, destination.open("wb") as output:
                        shutil.copyfileobj(stream, output)
                    return
    except (OSError, tarfile.TarError) as error:
        raise SetupError(f"cannot read {archive}: {error}") from error
    raise SetupError(f"{archive} does not contain {PANDOC_KATEX_MEMBER}")


def install_pandoc_katex(tools: Toolchain, *, force: bool, archive: Path | None = None) -> bool:
    """Install the checksum-pinned pandoc-katex binary; return whether it was installed."""
    if check_pandoc_katex(tools).status == "ok" and not force and archive is None:
        print(f"pandoc-katex: up to date at {tools.pandoc_katex}")
        return False
    tools.bin_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pandoc-katex-", dir=tools.bin_dir) as scratch:
        tarball = Path(scratch) / PANDOC_KATEX_ARCHIVE
        if archive is None:
            print(f"pandoc-katex: downloading {PANDOC_KATEX_URL}")
            _download(PANDOC_KATEX_URL, tarball)
        else:
            try:
                shutil.copyfile(archive, tarball)
            except OSError as error:
                raise SetupError(f"cannot read {archive}: {error}") from error
        digest = _sha256(tarball)
        if digest != PANDOC_KATEX_SHA256:
            raise SetupError(
                f"{PANDOC_KATEX_ARCHIVE} has SHA-256 {digest}, expected {PANDOC_KATEX_SHA256}"
            )
        staged = Path(scratch) / "pandoc-katex"
        _extract_binary(tarball, staged)
        staged.chmod(0o755)
        staged.replace(tools.pandoc_katex)
    installed = check_pandoc_katex(tools)
    if installed.status != "ok":
        raise SetupError(f"installed pandoc-katex does not run: {installed.detail}")
    print(f"pandoc-katex: installed {PANDOC_KATEX_VERSION} at {tools.pandoc_katex}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="md-doc-converter setup",
        description=(
            "Install the renderer toolchain (Mermaid, Paged.js and their browsers from npm, "
            "and pandoc-katex) into MD_DOC_CONVERTER_HOME, by default "
            "~/.local/share/md-doc-converter. Requires Node.js 22.12+ with npm."
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="Reinstall even when the toolchain is up to date"
    )
    parser.add_argument(
        "--pandoc-katex-archive",
        type=Path,
        metavar="PATH",
        help=f"Install from a local copy of {PANDOC_KATEX_ARCHIVE} (checksum still verified)",
    )
    arguments = parser.parse_args(argv)
    tools = toolchain()
    try:
        if not supported_platform():
            raise SetupError(
                f"setup supports Linux x86_64 only, not {sys.platform} {platform.machine()}"
            )
        node = check_node()
        if node.status != "ok":
            raise SetupError(f"node: {node.detail}. {node.fix}")
        npm = shutil.which("npm")
        if npm is None:
            raise SetupError(f"npm was not found on PATH. {NODE_FIX}")
        print(f"Renderer toolchain: {tools.root}")
        install_packages(tools, npm, force=arguments.force)
        install_pandoc_katex(tools, force=arguments.force, archive=arguments.pandoc_katex_archive)
    except SetupError as error:
        print(f"md-doc-converter setup: {error}", file=sys.stderr)
        return 1
    print("Done. Run `md-doc-converter doctor` to check every prerequisite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
