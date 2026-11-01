"""Pe helpers for portable Windows Store games."""

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pyappxportable.constants import ARCHITECTURES, PROXIES
from pyappxportable.errors import InstallError

# PE images


@dataclass
class PEInfo:
    """What the installer reads from a PE header."""

    machine: int
    managed: bool
    dll: bool


def pe_info(path: Path) -> PEInfo:
    """Machine, CLR header and DLL flag of a PE image."""
    data = Path(path).read_bytes()[:4096]
    if len(data) < 64 or data[:2] != b"MZ":
        raise InstallError(f"Not a Windows executable or DLL: {path}")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 24 + 96 > len(data) or data[offset : offset + 4] != b"PE\0\0":
        raise InstallError(f"Invalid PE header: {path}")
    machine, characteristics = (
        struct.unpack_from("<H", data, offset + 4)[0],
        struct.unpack_from("<H", data, offset + 22)[0],
    )
    optional = offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    # The CLR runtime header is data directory 14.
    clr = optional + (112 if magic == 0x20B else 96) + 14 * 8
    managed = clr + 4 <= len(data) and struct.unpack_from("<I", data, clr)[0] != 0
    return PEInfo(machine, managed, bool(characteristics & 0x2000))


def pe_imports(path: Path) -> List[str]:
    """The DLLs a PE image imports, lowercase; none for an image it cannot read."""
    data = Path(path).read_bytes()
    names: List[str] = []
    try:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset : offset + 4] != b"PE\0\0":
            return names
        count, size = (
            struct.unpack_from("<H", data, offset + 6)[0],
            struct.unpack_from("<H", data, offset + 20)[0],
        )
        optional = offset + 24
        magic, headers = (
            struct.unpack_from("<H", data, optional)[0],
            struct.unpack_from("<I", data, optional + 60)[0],
        )
        # The import table is data directory 1.
        table = struct.unpack_from(
            "<I", data, optional + (112 if magic == 0x20B else 96) + 8
        )[0]
        sections = [
            struct.unpack_from("<IIII", data, optional + size + 40 * i + 8)
            for i in range(count)
        ]

        def file_offset(rva: int) -> Optional[int]:
            if rva < headers:  # the headers are mapped at RVA 0
                return rva
            for virtual_size, address, raw_size, raw in sections:
                if address <= rva < address + max(virtual_size, raw_size):
                    return rva - address + raw
            return None

        descriptor = file_offset(table) if table else None
        while descriptor is not None:
            # The descriptor list ends with one whose name RVA is zero.
            name_rva = struct.unpack_from("<I", data, descriptor + 12)[0]
            name = file_offset(name_rva) if name_rva else None
            if name is None:
                break
            names.append(data[name : data.index(b"\0", name)].decode("latin-1").lower())
            descriptor += 20
    except (struct.error, ValueError):
        pass
    return names


def architecture_of(path: Path) -> str:
    """ "x86" or "x64" for a PE image; any other machine is an error."""
    machine = pe_info(path).machine
    if machine not in ARCHITECTURES:
        raise InstallError(f"Expected an x86 or x64 image: {path}")
    return ARCHITECTURES[machine]


def native_compiled_application(executable: Path) -> bool:
    """A .NET Native forwarding EXE imports its companion native assembly."""
    companion = executable.with_suffix(".dll")
    return (
        companion.name.lower() in pe_imports(executable)
        and companion.is_file()
        and bool({"sharedlibrary.dll", "mrt100_app.dll"} & set(pe_imports(companion)))
    )


def native_host_application(game: Path, executable: Path) -> bool:
    """A native game that would start before the shim: nothing it imports from
    its folder, directly or through its own DLLs, is a proxy.

    C++/CX Unity players, XAML or CoreWindow ones, are such games."""
    proxies = {name.lower() for name in (*PROXIES, "XAudio2_9.dll")}
    seen = set()
    pending = [Path(executable)]
    while pending:
        for name in pe_imports(pending.pop()):
            if name in proxies:
                return False
            local = Path(game) / name
            if name not in seen and local.is_file():
                seen.add(name)
                pending.append(local)
    return True


def package_has_xaml(game: Path) -> bool:
    """Compiled XAML: .xbf files, loose or named in resources.pri. The shim's
    native host makes the same check to choose XAML Islands over a CoreWindow."""
    if any(Path(game).rglob("*.xbf")):
        return True
    pri = Path(game) / "resources.pri"
    data = pri.read_bytes() if pri.is_file() else b""
    return b".xbf" in data or ".xbf".encode("utf-16-le") in data


# Real XAML runs native XAML games in an island, which Windows allows only in a
# process whose manifest declares Windows 10 1903 (10.0.18362) as tested.
PROCESS_MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
      <maxversiontested Id="10.0.18362.0"/>
    </application>
  </compatibility>
</assembly>
"""


def write_process_manifest(game: Path, executable: str) -> Optional[str]:
    """Write <executable>.manifest beside a native XAML game; returns a warning
    when Windows will not read it."""
    path = Path(game) / executable
    path.with_name(path.name + ".manifest").write_text(
        PROCESS_MANIFEST, encoding="utf-8"
    )
    # Windows caches whether an executable has a manifest until its timestamp changes.
    os.utime(path)
    if b"urn:schemas-microsoft-com:asm.v1" in path.read_bytes():
        return (
            f"{executable} embeds its own manifest, so Windows ignores"
            f" {path.name}.manifest and XAML may refuse to start."
        )
    return None
