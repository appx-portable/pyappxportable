"""Extraction helpers for portable Windows Store games."""

import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

from pyappxportable.constants import (
    BUNDLE_ARCHITECTURES,
    INVALID_NAME,
    PACKAGE_METADATA,
    UNIVERSAL_BUNDLE_ARCHITECTURES,
)
from pyappxportable.errors import InstallError
from pyappxportable.manifest import Manifest, local_name, read_manifest

# Package extraction


def target_parts(name: str) -> Optional[List[str]]:
    """The staged path of a package entry, or None for one never staged.

    A raw .appx keeps OPC percent-encoded names (%21_Name.xml, User%20Controls);
    installed packages have them decoded, and games open the decoded names."""
    decoded = urllib.parse.unquote(name)
    if decoded.endswith(("/", "\\")):
        return None  # a folder entry
    parts = re.split(r"[\\/]", decoded)
    if (
        decoded.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", decoded)
        or any(part in ("", ".", "..") or INVALID_NAME.search(part) for part in parts)
    ):
        raise InstallError(f"Unsafe file name in package: {name}")
    first = parts[0].lower()
    if first in PACKAGE_METADATA:
        return None
    # Saves, settings and logs belong to the player, not the package.
    if first in ("savedata", "appx-portable.ini") or (
        first.startswith("appx-portable") and first.endswith(".log")
    ):
        return None
    return parts


@dataclass
class Entry:
    """A package file: where it is staged, and how to read it."""

    parts: List[str]
    open: Callable


def zip_entries(archive: zipfile.ZipFile) -> List[Entry]:
    """The staged files of a package zip, every name checked."""
    entries = []
    for info in archive.infolist():
        parts = target_parts(info.filename)
        if parts is not None:
            entries.append(Entry(parts, lambda info=info: archive.open(info)))
    return entries


def folder_entries(folder: Path) -> List[Entry]:
    """The staged files of an extracted package folder; links are refused."""
    entries = []
    for parent, directories, files in os.walk(folder):
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                raise InstallError(
                    f"Links are not allowed in the package folder: {path}"
                )
        for name in files:
            path = Path(parent) / name
            parts = target_parts(path.relative_to(folder).as_posix())
            if parts is not None:
                entries.append(Entry(parts, lambda path=path: path.open("rb")))
    return entries


def write_entries(entries: Iterable[Entry], destination: Path, overwrite: bool) -> None:
    """Write entries under destination; overwrite=False keeps existing files."""
    for entry in entries:
        target = destination.joinpath(*entry.parts)
        if not overwrite and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with entry.open() as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def windows_is_32bit() -> bool:
    """32-bit Windows, which cannot run an x64 game; 32-bit Python reports the OS's
    architecture in PROCESSOR_ARCHITEW6432."""
    machine = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get(
        "PROCESSOR_ARCHITECTURE", ""
    )
    return sys.platform == "win32" and machine.upper() == "X86"


def select_bundle_packages(bundle: zipfile.ZipFile) -> Tuple[str, List[str]]:
    """The bundle's game package entry and its resource package entries."""
    try:
        root = ElementTree.fromstring(
            bundle.read("AppxMetadata/AppxBundleManifest.xml")
        )
    except KeyError:
        raise InstallError(
            "Not a recognized .appxbundle: it has no AppxMetadata/AppxBundleManifest.xml."
        ) from None
    except ElementTree.ParseError as error:
        raise InstallError(f"Unreadable AppxBundleManifest.xml: {error}") from error
    names = set(bundle.namelist())

    def entry(package) -> str:
        name = package.get("FileName", "")
        for candidate in (name, urllib.parse.quote(name)):
            if candidate in names:
                return candidate
        raise InstallError(f"The bundle lists {name}, but does not contain it.")

    packages = [e for e in root.iter() if local_name(e) == "Package"]
    applications = {
        e.get("Architecture", "neutral").lower(): e
        for e in packages
        if e.get("Type", "application").lower() == "application"
    }
    resources = [entry(e) for e in packages if e.get("Type", "").lower() == "resource"]
    found = [a for a in BUNDLE_ARCHITECTURES if a in applications]
    if found:
        # Every application package of a bundle targets the same Windows.
        with (
            bundle.open(entry(applications[found[0]])) as stream,
            zipfile.ZipFile(stream) as inner,
        ):
            universal = package_manifest(zip_entries(inner)).universal
        order = (
            UNIVERSAL_BUNDLE_ARCHITECTURES
            if universal and not windows_is_32bit()
            else BUNDLE_ARCHITECTURES
        )
        best = next(a for a in order if a in applications)
        return entry(applications[best]), resources
    if any(a.startswith("arm") for a in applications):
        raise InstallError(
            "This bundle only has ARM packages; Appx Portable supports x86 and x64 games."
        )
    raise InstallError("Could not find an x86 or x64 game package in the bundle.")


def package_manifest(entries: List[Entry]) -> Manifest:
    """The manifest among entries, checked to name a file the package has."""
    found = next(
        (e for e in entries if [p.lower() for p in e.parts] == ["appxmanifest.xml"]),
        None,
    )
    if found is None:
        raise InstallError(
            "Missing AppxManifest.xml. Give the whole .appx or .appxbundle,"
            " or a complete extraction."
        )
    with found.open() as stream:
        manifest = read_manifest(stream.read())
    wanted = manifest.executable.lower().split("/")
    if not any([p.lower() for p in e.parts] == wanted for e in entries):
        raise InstallError(f"The package has no {manifest.executable}.")
    return manifest


def extract_game(source: Path, destination: Path) -> Manifest:
    """Stage a package's files into destination; returns its manifest.

    Every name is checked before anything is written. Re-running replaces the
    package's files and keeps saves, appx-portable.ini and logs."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source.is_dir():
        if (
            source == destination
            or source in destination.parents
            or destination in source.parents
        ):
            raise InstallError(
                "The package folder and the game folder must not overlap."
            )
        entries = folder_entries(source)
        manifest = package_manifest(entries)
        destination.mkdir(parents=True, exist_ok=True)
        write_entries(entries, destination, overwrite=True)
        return manifest
    try:
        with zipfile.ZipFile(source) as archive:
            if (
                source.suffix.lower() == ".appxbundle"
                or "AppxMetadata/AppxBundleManifest.xml" in archive.namelist()
            ):
                return extract_bundle(archive, destination)
            entries = zip_entries(archive)
            manifest = package_manifest(entries)
            destination.mkdir(parents=True, exist_ok=True)
            write_entries(entries, destination, overwrite=True)
            return manifest
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise InstallError(f"Cannot read {source} as a package: {error}") from error


def copy_entry(bundle: zipfile.ZipFile, name: str, path: Path) -> Path:
    """Copy one bundle entry out: seeking inside a compressed entry restarts it."""
    with bundle.open(name) as stream, path.open("wb") as output:
        shutil.copyfileobj(stream, output)
    return path


def extract_bundle(bundle: zipfile.ZipFile, destination: Path) -> Manifest:
    """Stage a bundle's game package, then add its resource packages' files."""
    application, resources = select_bundle_packages(bundle)
    # The zips close before their temporary folder is removed.
    with (
        tempfile.TemporaryDirectory(prefix="installappx-") as temporary,
        ExitStack() as stack,
    ):
        try:
            archives = [
                stack.enter_context(
                    zipfile.ZipFile(
                        copy_entry(bundle, name, Path(temporary) / f"{i}.appx")
                    )
                )
                for i, name in enumerate([application] + resources)
            ]
            game_entries = zip_entries(archives[0])
            manifest = package_manifest(game_entries)
            extra = [zip_entries(archive) for archive in archives[1:]]
        except zipfile.BadZipFile as error:
            raise InstallError(
                f"A package inside the bundle is unreadable: {error}"
            ) from error
        destination.mkdir(parents=True, exist_ok=True)
        write_entries(game_entries, destination, overwrite=True)
        # Resource packages add scale and language files; the main
        # package's own files, resources.pri among them, always win.
        for entries in extra:
            write_entries(entries, destination, overwrite=False)
        return manifest


def peek_manifest(source: Path) -> Manifest:
    """The package's manifest, read without extracting anything."""
    source = Path(source)
    if source.is_dir():
        path = source / "AppxManifest.xml"
        if not path.is_file():
            raise InstallError(
                f"Missing {path}. Give the whole .appx or .appxbundle,"
                " or a complete extraction."
            )
        return read_manifest(path.read_bytes())
    try:
        with zipfile.ZipFile(source) as archive:
            if (
                source.suffix.lower() == ".appxbundle"
                or "AppxMetadata/AppxBundleManifest.xml" in archive.namelist()
            ):
                application, _ = select_bundle_packages(archive)
                with (
                    archive.open(application) as stream,
                    zipfile.ZipFile(stream) as inner,
                ):
                    return package_manifest(zip_entries(inner))
            return package_manifest(zip_entries(archive))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise InstallError(f"Cannot read {source} as a package: {error}") from error
    except FileNotFoundError:
        raise InstallError(f"Not found: {source}") from None
