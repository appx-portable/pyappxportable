"""Paths helpers for portable Windows Store games."""

import os
from pathlib import Path
from typing import Iterable, Optional

# Folder depth

# Store games keep paths in MAX_PATH buffers, so they cannot open a file whose
# path is longer; some (Fruit Ninja) crash in any game folder near 180 characters.
MAX_PATH = 260
LONG_FOLDER = 150


def path_warning(game: Path, files: Iterable[str]) -> Optional[str]:
    """Why the game may fail in its folder, given its files' paths; None if not."""
    longest = max(files, key=len, default="")
    if len(longest) >= MAX_PATH:
        return (
            f"{longest} is {len(longest)} characters long, and games cannot open"
            f" a file past {MAX_PATH - 1}. Install into a shorter folder."
        )
    if len(str(game)) > LONG_FOLDER:
        return (
            f"{game} is {len(str(game))} characters long, and some games crash in"
            " a folder this deep. If the game does not start, install into a shorter folder."
        )
    return None


def unquote_path(text: str) -> str:
    """A typed path without the quotes "Copy as path" and dragging add."""
    return text.strip().strip('"').strip()


def absolute(path: str) -> Path:
    """An absolute path: Python 3.9 on Windows resolves a missing one to itself."""
    return Path(os.path.abspath(Path(unquote_path(path)).expanduser())).resolve()
