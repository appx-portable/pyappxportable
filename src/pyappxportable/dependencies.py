"""Dependencies helpers for portable Windows Store games."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List
from xml.etree import ElementTree

from pyappxportable.console import say, step
from pyappxportable.downloads import fetch
from pyappxportable.errors import InstallError
from pyappxportable.manifest import Manifest, local_name
from pyappxportable.paths import absolute
from pyappxportable.pe import architecture_of
from pyappxportable.runtime import replace_file

# Visual C++ runtime (VCLibs)

# The runtime DLLs each VCLibs framework package provides to Store apps.
VCLIBS = {
    "Microsoft.VCLibs.110.00": ("msvcr110.dll", "msvcp110.dll", "vccorlib110.dll"),
    "Microsoft.VCLibs.120.00": (
        "msvcr120_app.dll",
        "msvcp120_app.dll",
        "vccorlib120_app.dll",
    ),
    "Microsoft.VCLibs.140.00": (
        "vcruntime140_app.dll",
        "msvcp140_app.dll",
        "vccorlib140_app.dll",
    ),
}
OPTIONAL_VCLIBS140 = (
    "concrt140_app.dll",
    "vcruntime140_1_app.dll",
    "msvcp140_1_app.dll",
    "msvcp140_2_app.dll",
    "msvcp140_atomic_wait_app.dll",
    "msvcp140_codecvt_ids_app.dll",
    "vcamp140_app.dll",
    "vcomp140_app.dll",
)
VCLIBS_RELEASE = "https://github.com/appx-portable/vclibs-appx/releases/download/v1/"
# The Microsoft-signed package for each dependency and architecture in
# VCLIBS_RELEASE, with its SHA-256 so a replaced release asset is refused.
VCLIBS_PACKAGES = {
    ("Microsoft.VCLibs.110.00", "x86"): (
        "Microsoft.VCLibs.110.00_11.0.51106.1_x86__8wekyb3d8bbwe.Appx",
        "22d49fa295486e0955a305bea65cfa4ff5d154d296878a1c577ec5507b602c9c",
    ),
    ("Microsoft.VCLibs.110.00", "x64"): (
        "Microsoft.VCLibs.110.00_11.0.51106.1_x64__8wekyb3d8bbwe.Appx",
        "b3383e6543e9ad90968143347f2ec5cb7b1e24acdd1c797af4a1ba98df98ae85",
    ),
    ("Microsoft.VCLibs.120.00", "x86"): (
        "Microsoft.VCLibs.120.00_12.0.21005.1_x86__8wekyb3d8bbwe.Appx",
        "f1302e899f015818b1b807f7a1dc44c221c184e3f34a19fe81b5874d4ab974bd",
    ),
    ("Microsoft.VCLibs.120.00", "x64"): (
        "Microsoft.VCLibs.120.00_12.0.21005.1_x64__8wekyb3d8bbwe.Appx",
        "64dd765b88d1983c09ebb33dc631bdfefb415f45f29177ab423bbc12c506e64f",
    ),
    ("Microsoft.VCLibs.140.00", "x86"): (
        "Microsoft.VCLibs.140.00_14.0.30704.0_x86__8wekyb3d8bbwe.Appx",
        "397b610554d67abaddfdc287ced6a9bf1ada4d4c9ddf53e01c6a061bed2860ef",
    ),
    ("Microsoft.VCLibs.140.00", "x64"): (
        "Microsoft.VCLibs.140.00_14.0.30704.0_x64__8wekyb3d8bbwe.Appx",
        "009f7db134c6061fe8f260e075374a28abbbc44e6cf23de107f93ec8b8c59816",
    ),
}
VCLIBS_DOWNLOADS = {
    key: VCLIBS_RELEASE + name for key, (name, _) in VCLIBS_PACKAGES.items()
}


def missing_vclibs(manifest: Manifest, game: Path) -> List[str]:
    """The manifest's VCLibs dependencies whose DLLs are not in game yet."""
    return [
        dependency
        for dependency in manifest.dependencies
        if dependency in VCLIBS
        and not all((game / name).is_file() for name in VCLIBS[dependency])
    ]


def add_vclibs(dependency: str, source: Path, game: Path, architecture: str) -> None:
    """Copy one dependency's DLLs from a VCLibs .appx or a folder into game."""
    source = Path(source)
    names = VCLIBS[dependency] + (
        OPTIONAL_VCLIBS140 if dependency.startswith("Microsoft.VCLibs.140.") else ()
    )
    with tempfile.TemporaryDirectory(prefix="installappx-vclibs-") as temporary:
        folder = source
        if source.is_file():
            # An .appx is a zip with the DLLs at its root. Only those names are
            # extracted, so entry paths never reach the file system.
            folder = Path(temporary)
            try:
                with zipfile.ZipFile(source) as archive:
                    wanted = {name.lower(): name for name in names}
                    for entry in archive.namelist():
                        if entry.lower() in wanted:
                            (folder / wanted[entry.lower()]).write_bytes(
                                archive.read(entry)
                            )
            except zipfile.BadZipFile as error:
                raise InstallError(
                    f"Cannot read {source} as an .appx package."
                ) from error
        elif not source.is_dir():
            raise InstallError(f"Not found: {source}")
        missing = [name for name in VCLIBS[dependency] if not (folder / name).is_file()]
        if missing:
            raise InstallError(
                f"{source} does not have {', '.join(missing)} ({dependency})."
            )
        found = [name for name in names if (folder / name).is_file()]
        for name in found:
            actual = architecture_of(folder / name)
            if actual != architecture:
                raise InstallError(
                    f"{name} in {source} is {actual}, but the game is {architecture}."
                    f" Use the {architecture} {dependency}."
                )
        for name in found:
            shutil.copyfile(folder / name, game / name)


def download_vclibs(dependency: str, game: Path, architecture: str) -> None:
    """Add one dependency's DLLs from its pinned package in VCLIBS_RELEASE."""
    name, sha256 = VCLIBS_PACKAGES[(dependency, architecture)]
    data = fetch(VCLIBS_RELEASE + name, "pass it with --dependencies")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise InstallError(
            f"{VCLIBS_RELEASE + name} does not have the expected SHA-256 {sha256}."
        )
    with tempfile.TemporaryDirectory(prefix="installappx-vclibs-") as temporary:
        package = Path(temporary) / name
        package.write_bytes(data)
        add_vclibs(dependency, package, game, architecture)


# .NET Native frameworks, supplied by the player or an installed package.
NATIVE_FRAMEWORKS = {
    "Microsoft.NET.Native.Framework.1.3": ("SharedLibrary.dll",),
    "Microsoft.NET.Native.Runtime.1.4": ("mrt100_app.dll", "clrcompression.dll"),
}
NATIVE_RELEASE = "https://github.com/appx-portable/vclibs-appx/releases/download/v2/"
NATIVE_PACKAGES = {
    ("Microsoft.NET.Native.Framework.1.3", "x86"): (
        "Microsoft.NET.Native.Framework.1.3_1.3.24211.0_x86__8wekyb3d8bbwe.appx",
        "03360086ecb34c84f4277fb273523c8d9a9229ea8506952e62267d1b1f0102ef",
    ),
    ("Microsoft.NET.Native.Framework.1.3", "x64"): (
        "Microsoft.NET.Native.Framework.1.3_1.3.24211.0_x64__8wekyb3d8bbwe.appx",
        "f956d085df3132aa3df3048acef088836c1fb0f2b47a1af3d08cfc3f56ffc849",
    ),
    ("Microsoft.NET.Native.Runtime.1.4", "x86"): (
        "Microsoft.NET.Native.Runtime.1.4_1.4.24201.0_x86__8wekyb3d8bbwe.appx",
        "39b2cb3559c17bd50b2da905d2c16d95155b6ae43f70299f74a0ea94ba6fb35f",
    ),
    ("Microsoft.NET.Native.Runtime.1.4", "x64"): (
        "Microsoft.NET.Native.Runtime.1.4_1.4.24201.0_x64__8wekyb3d8bbwe.appx",
        "b7abb5b5dedd4d6c2d2a635dbff39656a0edcaec7d216debaaea5914c9d454f4",
    ),
}


def installed_native_frameworks() -> List[Path]:
    """Ask Windows for readable framework folders belonging to this user."""
    if sys.platform != "win32":
        return []
    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; "
        "Get-AppxPackage -Name 'Microsoft.NET.Native.*' | "
        "Select-Object -ExpandProperty InstallLocation | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        locations = json.loads(result.stdout) if result.stdout.strip() else []
        return [
            Path(p) for p in ([locations] if isinstance(locations, str) else locations)
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def add_native_framework(
    dependency: str, source: Path, game: Path, architecture: str
) -> None:
    """Add a matching .NET Native framework, preserving package-owned DLLs."""
    names = NATIVE_FRAMEWORKS[dependency]
    with tempfile.TemporaryDirectory(prefix="installappx-native-") as temporary:
        folder = source
        if source.is_file():
            folder = Path(temporary)
            try:
                with zipfile.ZipFile(source) as archive:
                    (folder / "AppxManifest.xml").write_bytes(
                        archive.read("AppxManifest.xml")
                    )
                    wanted = {name.lower(): name for name in names}
                    for entry in archive.namelist():
                        if entry.lower() in wanted:
                            (folder / wanted[entry.lower()]).write_bytes(
                                archive.read(entry)
                            )
            except (zipfile.BadZipFile, KeyError) as error:
                raise InstallError(f"Not a framework .appx: {source}") from error
        try:
            root = ElementTree.fromstring((folder / "AppxManifest.xml").read_bytes())
            identity = next(e for e in root.iter() if local_name(e) == "Identity")
            if (
                identity.get("Name") != dependency
                or identity.get("ProcessorArchitecture", "").lower() != architecture
            ):
                raise InstallError(f"{source} is not {dependency} ({architecture}).")
        except (OSError, ElementTree.ParseError, StopIteration) as error:
            raise InstallError(f"Unreadable framework manifest: {source}") from error
        for name in names:
            path = folder / name
            if not path.is_file() or architecture_of(path) != architecture:
                raise InstallError(f"{source} lacks {name} ({architecture}).")
        for name in names:
            if not (game / name).is_file():
                replace_file(folder / name, game / name)


def download_native_framework(dependency: str, game: Path, architecture: str) -> None:
    """Download a pinned .NET Native package and verify it before staging."""
    name, expected = NATIVE_PACKAGES[(dependency, architecture)]
    url = NATIVE_RELEASE + name
    data = fetch(url, "pass it with --dependencies")
    if hashlib.sha256(data).hexdigest() != expected:
        raise InstallError(f"{url} does not have the expected SHA-256 {expected}.")
    with tempfile.TemporaryDirectory(
        prefix="installappx-native-download-"
    ) as temporary:
        package = Path(temporary) / name
        package.write_bytes(data)
        add_native_framework(dependency, package, game, architecture)


def add_native_frameworks(
    manifest: Manifest, game: Path, architecture: str, sources: List[str]
) -> None:
    """Stage the native runtime dependencies before writing a usable launcher."""
    missing = [
        dependency
        for dependency in manifest.dependencies
        if dependency in NATIVE_FRAMEWORKS
        and not all((game / name).is_file() for name in NATIVE_FRAMEWORKS[dependency])
    ]
    if not missing:
        return
    candidates = [
        absolute(source) for source in sources
    ] + installed_native_frameworks()
    for dependency in missing:
        for candidate in candidates:
            try:
                add_native_framework(dependency, candidate, game, architecture)
                say(f"Added {dependency} ({architecture}) from {candidate}")
                break
            except (InstallError, OSError):
                continue
        else:
            step(f"Downloading {dependency} ({architecture})")
            try:
                download_native_framework(dependency, game, architecture)
            except InstallError as error:
                raise InstallError(
                    f"{error} Missing {dependency} ({architecture})."
                    " Pass its .appx or installed folder with --dependencies."
                ) from error
