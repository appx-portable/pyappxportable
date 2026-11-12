"""Manifest helpers for portable Windows Store games."""

from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import List
from xml.etree import ElementTree

from pyappxportable.constants import IDENTITY_NAME
from pyappxportable.errors import InstallError

# Package manifest


@dataclass
class Manifest:
    """The parts of AppxManifest.xml the installer uses."""

    name: str
    version: str
    architecture: str
    executable: str
    dependencies: List[str]
    universal: bool = False  # a Windows 10 package: it names a TargetDeviceFamily
    # C# Windows Runtime components, which Windows serves through CLRHost.dll;
    # the shim hosts them on the desktop CLR through appx-host.exe.
    clr_components: bool = False


def local_name(element) -> str:
    """An XML element's tag without its namespace."""
    return element.tag.rsplit("}", 1)[-1]


def read_manifest(data: bytes) -> Manifest:
    """Parse AppxManifest.xml, refusing names that are unsafe as paths."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise InstallError(f"Unreadable AppxManifest.xml: {error}") from error
    # By local name: Windows 8, 8.1 and 10 manifests use different namespaces.
    elements = list(root.iter())
    identity = next((e for e in elements if local_name(e) == "Identity"), None)
    application = next((e for e in elements if local_name(e) == "Application"), None)
    if identity is None or application is None:
        raise InstallError("AppxManifest.xml declares no Identity or Application.")
    executable = PureWindowsPath(application.get("Executable", ""))
    if not executable.parts or executable.anchor or ".." in executable.parts:
        raise InstallError(
            f"AppxManifest.xml has an unusable Executable: {application.get('Executable')!r}"
        )
    name = identity.get("Name", "")
    # The identity name becomes a folder name; hold it to the appx schema.
    if not IDENTITY_NAME.match(name):
        raise InstallError(f"AppxManifest.xml has an unusable Identity Name: {name!r}")
    return Manifest(
        name=name,
        version=identity.get("Version", ""),
        architecture=identity.get("ProcessorArchitecture", "neutral").lower(),
        executable=executable.as_posix(),
        dependencies=[
            e.get("Name", "") for e in elements if local_name(e) == "PackageDependency"
        ],
        universal=any(local_name(e) == "TargetDeviceFamily" for e in elements),
        clr_components=any(
            local_name(e) == "Path" and (e.text or "").strip().lower() == "clrhost.dll"
            for e in elements
        ),
    )
