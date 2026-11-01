"""Desktop helpers for portable Windows Store games."""

import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Optional

from pyappxportable.dependencies import OPTIONAL_VCLIBS140, VCLIBS
from pyappxportable.errors import InstallError

# Windows 8/8.1: AppContainer flag

APPCONTAINER = 0x1000  # IMAGE_DLLCHARACTERISTICS_APPCONTAINER


def needs_desktop_copy() -> bool:
    """Windows 8 and 8.1 refuse to start an AppContainer image outside a package."""
    # getattr: the function exists only on Windows, where type checkers skip it.
    version = getattr(sys, "getwindowsversion", lambda: (0, 0))
    return sys.platform == "win32" and tuple(version()[:2]) in ((6, 2), (6, 3))


def appcontainer_offset(data: bytes, dll: bool) -> Optional[int]:
    """The DllCharacteristics offset of an x86 PE32 image, or None for x64."""
    if len(data) < 64 or data[:2] != b"MZ":
        raise InstallError("Expected a DOS/PE image.")
    # Bounds checks before every read: the offsets come from the file.
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if pe < 64 or pe + 24 > len(data) or data[pe : pe + 4] != b"PE\0\0":
        raise InstallError("Invalid or truncated PE header.")
    machine, size, characteristics = struct.unpack_from("<H", data, pe + 4)[
        0
    ], *struct.unpack_from("<HH", data, pe + 20)
    if machine == 0x8664:
        return None
    optional = pe + 24
    if size < 96 or optional + size > len(data):
        raise InstallError("Invalid or truncated PE optional header.")
    if (
        machine != 0x14C
        or struct.unpack_from("<H", data, optional)[0] != 0x10B
        or not characteristics & 0x2
        or bool(characteristics & 0x2000) != dll
    ):
        raise InstallError(
            "Expected an x86 PE32 image of the appropriate EXE/DLL type."
        )
    return optional + 70


def cleared(data: bytes, dll: bool) -> Optional[bytes]:
    """data with only the AppContainer flag cleared, or None when it is not set."""
    offset = appcontainer_offset(data, dll)
    if offset is None or not struct.unpack_from("<H", data, offset)[0] & APPCONTAINER:
        return None
    patched = bytearray(data)
    struct.pack_into(
        "<H",
        patched,
        offset,
        struct.unpack_from("<H", data, offset)[0] & ~APPCONTAINER & 0xFFFF,
    )
    return bytes(patched)


def write_replacing(target: Path, data: bytes) -> None:
    """Write data to target through a temporary file and a rename."""
    fd, temporary = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def make_desktop_copy(game: Path, executable: str) -> str:
    """Write <name>.desktop.exe with the AppContainer flag cleared, and clear it
    in the staged VCLibs DLLs; returns the executable to launch."""
    game = Path(game)
    source = game / executable
    patched = cleared(source.read_bytes(), dll=False)
    if patched is None:
        return executable
    relative = Path(executable)
    desktop = relative.with_name(relative.stem + ".desktop" + relative.suffix)
    # The staged DLLs are copies, so they are patched in place.
    names = [name for names in VCLIBS.values() for name in names] + list(
        OPTIONAL_VCLIBS140
    )
    for name in names:
        if (game / name).is_file():
            image = cleared((game / name).read_bytes(), dll=True)
            if image is not None:
                write_replacing(game / name, image)
    write_replacing(game / desktop, patched)
    return desktop.as_posix()
