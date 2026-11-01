"""Patches helpers for portable Windows Store games."""

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from pyappxportable.constants import REPOSITORY
from pyappxportable.downloads import fetch
from pyappxportable.errors import InstallError
from pyappxportable.manifest import Manifest


@dataclass
class KnownPatch:
    """A patch in patch/<folder> and what it is run against."""

    folder: str
    files: Tuple[str, ...]
    target: str  # what the patch is given: "" for the game folder, else a path in it


# Patches in patch/<folder>, for the exact package identity and version each supports.
KNOWN_PATCHES = {
    ("PopCap.BejweledLIVE", "4.0.0.10"): KnownPatch("bejeweled", ("patch.py",), ""),
    ("Microsoft.MicrosoftSudoku", "1.7.10190.0"): KnownPatch(
        "mssudoku", ("patch.py",), ""
    ),
    ("Disney.WheresMyMickey", "1.1.0.30"): KnownPatch(
        "wheresmymickey", ("patch.py",), "WheresMyMickey.Windows8.exe"
    ),
    # Keep the loose caption as the default compatibility route. Embedded PRI
    # has passed Windows 11 and user Windows 10 tests but uses undocumented APIs.
    ("Ubisoft.RaymanFiestaRunWindows10Edition", "1.0.23.0"): KnownPatch(
        "raymanwin10", ("patch.py", "CustomTitleBar.xaml"), ""
    ),
}


def apply_known_patch(
    manifest: Manifest, game: Path, tag: Optional[str], local: Optional[Path]
) -> Optional[str]:
    """Run the patch for a known game; None when there is none.

    local is a repository's patch/ folder; otherwise the patch is downloaded at
    the release tag, so it always matches the runtime."""
    patch = KNOWN_PATCHES.get((manifest.name, manifest.version))
    if patch is None:
        return None
    game = Path(game)
    with tempfile.TemporaryDirectory(prefix="installappx-patch-") as temporary:
        folder = Path(temporary)
        for name in patch.files:
            if local is not None:
                shutil.copyfile(Path(local) / patch.folder / name, folder / name)
            else:
                url = f"https://raw.githubusercontent.com/{REPOSITORY}/{tag}"
                (folder / name).write_bytes(fetch(f"{url}/patch/{patch.folder}/{name}"))
        target = game / patch.target if patch.target else game
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-I", str(folder / "patch.py"), str(target)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=temporary,
            check=False,
        )
    if result.returncode != 0:
        raise InstallError(
            f"The {patch.folder} patch failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()
