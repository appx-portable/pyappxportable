"""Downloads helpers for portable Windows Store games."""

import hashlib
import http.client
import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from pyappxportable.constants import REPOSITORY
from pyappxportable.errors import InstallError


def fetch(url: str, instead: str = "pass --build with a local build folder") -> bytes:
    """Download url; any failure is an InstallError naming it and what to do instead."""
    request = urllib.request.Request(url, headers={"User-Agent": "pyappxportable"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", error)
        raise InstallError(
            f"Could not download {url}: {reason}. Check the connection, or {instead}."
        ) from error


def resolve_release(tag: Optional[str]) -> str:
    """tag, or the latest release's when it is None."""
    if tag:
        return tag
    try:
        return json.loads(
            fetch(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
        )["tag_name"]
    except (ValueError, KeyError) as error:
        raise InstallError("Could not read the latest release from GitHub.") from error


def download_runtime(
    tag: str, architecture: str, folder: Path, wine: bool = False
) -> Path:
    """Download and unpack appx-portable-<tag>-<architecture>.zip into folder.

    Under Wine, the release's patched graphics stack is unpacked beside it."""
    try:
        release = json.loads(
            fetch(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{tag}")
        )
        assets = {
            asset["name"]: asset["browser_download_url"]
            for asset in release.get("assets", [])
        }
    except (ValueError, KeyError, TypeError) as error:
        raise InstallError(f"Could not read release {tag} from GitHub.") from error
    name = f"appx-portable-{tag}-{architecture}.zip"
    if name not in assets:
        raise InstallError(f"Release {tag} has no {name}.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    unpack_release_zip(name, fetch(assets[name]), folder)
    graphics = f"appx-portable-{tag}-wine-{architecture}.zip"
    if wine and graphics in assets:
        unpack_release_zip(graphics, fetch(assets[graphics]), folder)
    elif wine:
        print(
            f"Warning: release {tag} has no Wine graphics stack; games may render black."
            " Build one with scripts/build-wined3d.sh and pass --build."
        )
    return folder


def unpack_release_zip(name: str, data: bytes, folder: Path) -> None:
    """Unpack the plain files of a release zip into folder."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.namelist():
                # The release zip holds plain DLLs; anything in a folder is ignored.
                if (
                    "/" not in entry
                    and "\\" not in entry
                    and entry not in ("", ".", "..")
                ):
                    (folder / entry).write_bytes(archive.read(entry))
    except zipfile.BadZipFile as error:
        raise InstallError(f"{name} is not a readable zip.") from error


def download_verified(url: str, sha256: str) -> bytes:
    """Download url, refusing data whose SHA-256 is not the pinned one."""
    data = fetch(url, "install it yourself")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise InstallError(f"{url} does not have the expected SHA-256 {sha256}.")
    return data
