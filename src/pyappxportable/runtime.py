"""Runtime helpers for portable Windows Store games."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import List

from pyappxportable.console import note
from pyappxportable.constants import PROXIES
from pyappxportable.errors import InstallError
from pyappxportable.pe import architecture_of

# The patched Wine graphics stack from scripts/build-wined3d.sh, with the
# manifest naming the hashes of DLLs built together.
WINE_GRAPHICS = ("d3d11.dll", "dxgi.dll", "wined3d.dll")
WINE_GRAPHICS_MANIFEST = "wine-graphics.json"


def replace_file(source: Path, target: Path) -> None:
    """Replace target by rename, never in place, so it is never half-written."""
    fd, temporary = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_wine_graphics(build: Path) -> None:
    """Refuse a Wine graphics stack whose DLLs were not built together."""
    path = build / WINE_GRAPHICS_MANIFEST
    try:
        hashes = json.loads(path.read_text())["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InstallError(f"Unreadable {path}: {error}") from error
    for name in WINE_GRAPHICS:
        if not (build / name).is_file():
            raise InstallError(f"Build output missing: {build / name}")
        if hashlib.sha256((build / name).read_bytes()).hexdigest() != hashes.get(name):
            raise InstallError(
                f"{build / name} does not match {path}; rerun scripts/build-wined3d.sh."
            )


# The bridge the shim loads into the desktop CLR for a native game's C#
# components.
COMPONENT_BRIDGE = "appx-components.dll"


def host_files(
    build: Path, managed: bool, wine: bool, native_host: bool, clr_components: bool
) -> List[str]:
    """The host files the game needs: the CLR host for a managed game, the
    component bridge for a native game's C# components, and the native host."""
    names = []
    if managed:
        names.append("appx-host.exe")
    elif clr_components and not wine:
        # The bridge is a DLL the shim loads, so players have nothing to start
        # by mistake. Runtimes before it carry the bridge in appx-host.exe, and
        # releases before the bridge have neither.
        bridge = next(
            (
                name
                for name in (COMPONENT_BRIDGE, "appx-host.exe")
                if (build / name).is_file()
            ),
            None,
        )
        if bridge:
            names.append(bridge)
        else:
            note(
                f"this runtime has no {COMPONENT_BRIDGE}; the game's C# components"
                " stay unavailable."
            )
    if native_host:
        if wine:
            raise InstallError("Native hosting requires Windows.")
        names.append("appx-native-host.exe")
    return names


# Architecture and hosting/graphics choices independently select runtime files.
def stage_runtime(  # pylint: disable=too-many-arguments
    build: Path,
    game: Path,
    architecture: str,
    managed: bool,
    *,
    wine: bool = False,
    native_host: bool = False,
    clr_components: bool = False,
) -> List[str]:
    """Copy the shim, proxies and the selected CLR or native host into game.

    A native game with C# components also gets appx-components.dll, the bridge
    the shim calls to host them on the desktop CLR. Under Wine, a build's
    patched graphics stack goes beside them too."""
    if managed and architecture == "x64":
        raise InstallError(
            "Managed x64 games are not supported; managed hosting is x86 only."
        )
    build = Path(build)
    names = ["appxportable.dll", *PROXIES]
    if (build / "XAudio2_9.dll").is_file():
        names.append("XAudio2_9.dll")
    names += host_files(build, managed, wine, native_host, clr_components)
    graphics = wine and (build / WINE_GRAPHICS_MANIFEST).is_file()
    if graphics:
        check_wine_graphics(build)
        names.extend(WINE_GRAPHICS)
    for name in names:
        if not (build / name).is_file():
            raise InstallError(f"Build output missing: {build / name}")
        actual = architecture_of(build / name)
        if actual != architecture:
            raise InstallError(
                f"{build / name} is {actual}, but the game is {architecture}."
            )
    if graphics:
        names.append(WINE_GRAPHICS_MANIFEST)
    for name in names:
        replace_file(build / name, game / name)
    # A native game's bridge used to be appx-host.exe, which players could
    # start by mistake; only managed games start from it.
    leftover = Path(game) / "appx-host.exe"
    if not managed and COMPONENT_BRIDGE in names and leftover.is_file():
        leftover.unlink()
    # The WebView2 static loader's redistribution notice travels with the DLL.
    # Older releases/local builds do not have this dependency.
    notice = build / "WebView2-LICENSE.txt"
    if notice.is_file():
        replace_file(notice, game / notice.name)
    return names
