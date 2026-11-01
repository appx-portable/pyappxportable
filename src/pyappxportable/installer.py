"""Installer helpers for portable Windows Store games."""

import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from pyappxportable.console import (
    Ask,
    choose,
    confirm,
    done,
    note,
    prompt,
    say,
    step,
    warn,
)
from pyappxportable.dependencies import (
    VCLIBS_DOWNLOADS,
    add_native_frameworks,
    add_vclibs,
    download_vclibs,
    missing_vclibs,
)
from pyappxportable.desktop import make_desktop_copy, needs_desktop_copy
from pyappxportable.downloads import download_runtime, resolve_release, retry_prompts
from pyappxportable.errors import InstallError
from pyappxportable.extraction import extract_game, peek_manifest
from pyappxportable.launchers import (
    LAUNCHER,
    MANAGED_UNDER_WINE,
    launch_executable,
    plays_media,
    write_launcher,
)
from pyappxportable.macos import add_macos_dependencies, install_managed_wine
from pyappxportable.manifest import Manifest, read_manifest
from pyappxportable.options import InstallOptions
from pyappxportable.patches import KNOWN_PATCHES, apply_known_patch
from pyappxportable.paths import absolute, path_warning, unquote_path
from pyappxportable.pe import (
    architecture_of,
    native_compiled_application,
    native_host_application,
    package_has_xaml,
    pe_info,
    write_process_manifest,
)
from pyappxportable.runtime import stage_runtime
from pyappxportable.settings import read_store, write_settings

STORE_LABELS = {
    "none": "none",
    "trial": "unlock the full game in trial mode",
    "iap": "complete in-app purchases without the Store",
    "both": "both",
}


def choose_package(args, ask: Ask) -> Path:
    """The package or installed game folder, from the arguments or the prompt;
    the prompt asks again for a path that does not exist."""
    if args.package or args.yes:
        if not unquote_path(args.package or ""):
            raise InstallError("No game package given.")
        return absolute(args.package)
    while True:
        answer = ask("Game package (.appxbundle, .appx or extracted folder): ")
        if not unquote_path(answer):
            raise InstallError("No game package given.")
        package = absolute(answer)
        if package.exists():
            return package
        warn(f"{package} does not exist. Drag the package here, or paste its path.")


def choose_folders(
    args, ask: Ask, source: Optional[Path] = None
) -> Tuple[Path, Path, Manifest]:
    """The package, the folder to install it in, and the package's manifest."""
    source = choose_package(args, ask) if source is None else source
    manifest = peek_manifest(source)
    default = source.parent / manifest.name
    if default == source:
        default = source.parent / (manifest.name + "-portable")
    destination = args.dest
    if destination is None and not args.yes:
        say(f"Found {manifest.name} {manifest.version}.")
        destination = ask(f"Install into [Enter for {default}]: ")
    chosen = unquote_path(destination or "")
    return source, absolute(chosen) if chosen else default, manifest


def try_vclibs(
    dependency: str, sources: List[str], game: Path, architecture: str
) -> List[str]:
    """Add dependency from the first source that has it; why the others did not."""
    refused = []
    for source in sources:
        try:
            add_vclibs(dependency, absolute(source), game, architecture)
            return []
        except InstallError as error:
            refused.append(str(error))
    return refused


def add_missing_vclibs(
    args, ask: Ask, manifest: Manifest, game: Path, architecture: str
) -> None:
    """Add each VCLibs dependency the game lacks: --dependencies, download, then ask."""
    for dependency in missing_vclibs(manifest, game):
        refused = try_vclibs(dependency, args.dependencies, game, architecture)
        if dependency in missing_vclibs(manifest, game):
            step(f"Downloading {dependency} ({architecture})")
            try:
                download_vclibs(dependency, game, architecture)
            except InstallError as error:
                refused.append(str(error))
        link = VCLIBS_DOWNLOADS[(dependency, architecture)]
        missing = f"Missing {dependency} ({architecture}). Download: {link}."
        if args.yes and dependency in missing_vclibs(manifest, game):
            raise InstallError(
                " ".join(refused + [missing, "Pass it with --dependencies."])
            )
        while dependency in missing_vclibs(manifest, game):
            for message in refused:
                warn(message)
            warn(missing)
            answer = ask(f"{dependency} .appx or folder: ")
            if not unquote_path(answer):
                raise InstallError(f"Missing {dependency} ({architecture}).")
            refused = try_vclibs(dependency, [answer], game, architecture)


def stage_dlls(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    args,
    game: Path,
    architecture: str,
    managed: bool,
    native_host: bool = False,
    clr_components: bool = False,
) -> Optional[str]:
    """Copy the runtime from --build or the release; returns the release tag used."""
    wine = sys.platform != "win32"
    if args.build:
        stage_runtime(
            absolute(args.build),
            game,
            architecture,
            managed,
            wine=wine,
            native_host=native_host,
            clr_components=clr_components,
        )
        return args.release
    tag = resolve_release(args.release)
    step(f"Downloading Appx Portable {tag} ({architecture})")
    with tempfile.TemporaryDirectory(prefix="installappx-runtime-") as temporary:
        stage_runtime(
            download_runtime(tag, architecture, Path(temporary), wine=wine),
            game,
            architecture,
            managed,
            wine=wine,
            native_host=native_host,
            clr_components=clr_components,
        )
    return tag


def patch_known_game(
    manifest: Manifest, game: Path, tag: Optional[str], patch_dir: Optional[str] = None
) -> None:
    """Apply a known game's patch, from a checkout's patch/ or at the release tag."""
    local = absolute(patch_dir) if patch_dir else None
    if local is not None and not local.is_dir():
        raise InstallError(f"Patch directory does not exist: {local}")
    if (
        local is None
        and tag is None
        and (manifest.name, manifest.version) in KNOWN_PATCHES
    ):
        tag = resolve_release(None)
    output = apply_known_patch(manifest, game, tag, local)
    if output:
        say(output)


def choose_store(args, ask: Ask, game: Path) -> Optional[str]:
    """The Store choice from --store or the prompt; None keeps the current one."""
    if args.store is not None or args.yes:
        return args.store
    return choose(ask, "Store overrides:", STORE_LABELS, read_store(game))


def choose_launcher(args, ask: Ask) -> bool:
    """Whether to write the launcher, from --launcher or the prompt."""
    if args.launcher is not None:
        return args.launcher
    return args.yes or confirm(ask, f"Create the {LAUNCHER} launcher?")


@dataclass
class Hosting:
    """How a staged game starts, read from its executable and folder."""

    architecture: str
    managed: bool
    native_compiled: bool
    native_host: bool


def game_hosting(game: Path, executable: Path) -> Hosting:
    """The game's architecture and how it is hosted; refuses what cannot run here."""
    architecture = architecture_of(executable)
    managed = pe_info(executable).managed
    native_compiled = native_compiled_application(executable)
    # .NET Native games and native games that import no proxy (such as C++/CX
    # Unity players) start through the native host.
    native_host = native_compiled or (
        not managed and native_host_application(game, executable)
    )
    if native_host and sys.platform != "win32":
        raise InstallError("Native hosting requires Windows.")
    return Hosting(architecture, managed, native_compiled, native_host)


# Known native XAML games that keep the portable XAML backend: Rayman's patch
# binds its loose caption XAML to that backend.
PORTABLE_XAML = {("Ubisoft.RaymanFiestaRunWindows10Edition", "1.0.23.0")}


def stage_process_manifest(game: Path, manifest: Manifest, hosting: Hosting) -> None:
    """Native XAML games run Windows' XAML in an island, which needs the
    manifest; CoreWindow, managed and .NET Native games do not. Without it, a
    game that imports a proxy keeps the portable XAML backend."""
    # Wine has no XAML Islands.
    if sys.platform != "win32" or hosting.managed or hosting.native_compiled:
        return
    if not package_has_xaml(game):
        return
    if (manifest.name, manifest.version) in PORTABLE_XAML:
        return
    warning = write_process_manifest(game, manifest.executable)
    if warning:
        warn(warning)


def installed_game(folder: Path) -> bool:
    """Whether folder is a game pyappxportable installed: a staged package
    beside the Appx Portable shim."""
    return (folder / "AppxManifest.xml").is_file() and (
        folder / "appxportable.dll"
    ).is_file()


# Options that only apply to a full installation.
INSTALL_ONLY_OPTIONS = {
    "dest": "--dest",
    "dependencies": "--dependencies",
    "store": "--store",
    "patch_dir": "--patch-dir",
    "desktop_copy": "--desktop-copy",
}


def update_runtime(args, game: Path) -> int:
    """Replace the Appx Portable runtime of the game installed in game.

    Only the shim, proxies, hosts and Wine graphics stack are staged again, from
    --build or the release; game files, saves, settings, the launcher and
    framework packages stay as they are."""
    given = [
        flag for name, flag in INSTALL_ONLY_OPTIONS.items() if getattr(args, name, None)
    ]
    if given:
        raise InstallError(
            f"{', '.join(given)} cannot be used to update the runtime; they only"
            " apply to installing from a package."
        )
    if not installed_game(game):
        raise InstallError(
            f"{game} is not an installed game: it needs AppxManifest.xml and"
            " appxportable.dll. Install the game from its package first."
        )
    manifest = read_manifest((game / "AppxManifest.xml").read_bytes())
    executable = game / manifest.executable
    if not executable.is_file():
        raise InstallError(f"The game folder has no {manifest.executable}.")
    step(
        f"Updating the Appx Portable runtime of {manifest.name} {manifest.version}"
        f" in {game}"
    )
    hosting = game_hosting(game, executable)
    tag = stage_dlls(
        args,
        game,
        hosting.architecture,
        hosting.managed,
        hosting.native_host,
        manifest.clr_components,
    )
    stage_process_manifest(game, manifest, hosting)
    if (manifest.name, manifest.version) in KNOWN_PATCHES:
        # Patches change game files, which are not extracted again here.
        note(
            "this game has a compatibility patch, which is not applied again."
            " If the game misbehaves, reinstall it from its package."
        )
    done(
        f"Updated the runtime{f' to {tag}' if tag else ''}. Saves and settings were kept."
    )
    return 0


def offer_runtime_update(args, ask: Ask, source: Path) -> bool:
    """Whether to only update the runtime of an installed game given as the package."""
    if args.yes or args.dest is not None or not installed_game(source):
        return False
    return confirm(
        ask, f"{source} is an installed game. Update only its Appx Portable runtime?"
    )


def install_game(args: InstallOptions, ask: Ask = prompt) -> int:
    """Install the game the arguments and answers describe, or with update_runtime,
    update the runtime of an installed game. Every question comes before the long
    steps start, except for a framework package that cannot be found and a
    download that keeps failing."""
    with retry_prompts(None if args.yes else ask):
        return install_or_update(args, ask)


def install_or_update(  # pylint: disable=too-many-locals
    args: InstallOptions, ask: Ask
) -> int:
    """install_game, with download retry prompts set up."""
    source = choose_package(args, ask)
    if getattr(args, "update_runtime", False) or offer_runtime_update(
        args, ask, source
    ):
        return update_runtime(args, source)
    source, destination, manifest = choose_folders(args, ask, source)
    store = choose_store(args, ask, destination)
    launcher = choose_launcher(args, ask)
    step(f"Installing {manifest.name} {manifest.version} into {destination}")
    manifest = extract_game(source, destination)
    executable = destination / manifest.executable
    hosting = game_hosting(destination, executable)
    architecture, managed = hosting.architecture, hosting.managed
    native_host = hosting.native_host
    add_missing_vclibs(args, ask, manifest, destination, architecture)
    add_native_frameworks(manifest, destination, architecture, args.dependencies)
    tag = stage_dlls(
        args, destination, architecture, managed, native_host, manifest.clr_components
    )
    stage_process_manifest(destination, manifest, hosting)
    if not args.no_patch:
        patch_known_game(manifest, destination, tag, args.patch_dir)
    launch = launch_executable(manifest.executable, managed, native_host)
    if not managed and not native_host and (args.desktop_copy or needs_desktop_copy()):
        launch = make_desktop_copy(destination, manifest.executable)
    write_settings(destination, store)
    managed_wine = None
    if (  # pylint: disable=too-many-boolean-expressions
        managed
        and architecture == "x86"
        and sys.platform == "darwin"
        and platform.machine() == "arm64"
        and not os.environ.get("WINE")
        and tag
    ):
        managed_wine = install_managed_wine(tag)
    if launcher:
        launch = str(
            write_launcher(
                destination,
                launch,
                windows=sys.platform == "win32",
                macos=sys.platform == "darwin",
                media=plays_media(destination, executable),
                wine_runtime=managed_wine,
            )
        )
    if sys.platform == "darwin":
        add_macos_dependencies(args, ask, wine_ready=managed_wine is not None)
    if managed and sys.platform != "win32":
        warn(
            "managed (.NET) games still need the WinRT projection and XAML engine;"
            " the patched Wine runtime fixes CLR startup only."
            if managed_wine is not None
            else MANAGED_UNDER_WINE
        )
    warning = path_warning(
        destination, [str(p) for p in destination.rglob("*") if p.is_file()]
    )
    if warning:
        warn(warning)
    say()
    done(f"Done. Start the game with {destination / launch}")
    say(
        f"Store overrides: {read_store(destination)}."
        f" Settings: {destination / 'appx-portable.ini'}"
    )
    return 0
