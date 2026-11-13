"""Settings helpers for portable Windows Store games."""

from pathlib import Path
from typing import Dict, List, Optional

# appx-portable.ini

SETTINGS_TEMPLATE: str = """\
; appx-portable settings. pyappxportable writes this file once; edit it here.

[storage]
; Where the game keeps its saves (<package> is the manifest's identity name):
;   game       SaveData beside the game executable (the default)
;   documents  Documents\\appx-portable\\<package>
;   appdata    %LOCALAPPDATA%\\appx-portable\\<package>
;   or a folder path; relative paths start beside the game, and %VARIABLES% expand.
; If the folder cannot be written (for example under Program Files), saves go
; to documents, then appdata. Changing the location does not move saves.
SaveLocation=game

[options]
; Store overrides, off with 0 and on with 1. Restart the game after a change.
;   UnlockTrial  report a full license, for games with a trial mode
;   StubIap      complete in-app purchases locally, without the Store
UnlockTrial=0
StubIap=0
; With StubIap, games that ship no product catalog (such as Unity IAP games) find
; no products unless their IDs are listed here, separated by commas; for example
; StubIapProducts=com.example.levels,com.example.coins
; Most Store games run at the 60 frames a second they were made for; 0 removes
; the cap, which suits games that keep their speed at higher frame rates.
FrameRate=60
; Games run offline: they are told there is no internet connection, so they do
; not wait on servers that have since closed. 1 gives the game Windows' own
; connection status and HTTP.
Network=0
"""
STORE_CHOICES = {
    "none": (False, False),
    "trial": (True, False),
    "iap": (False, True),
    "both": (True, True),
}
STORE_KEYS = ("UnlockTrial", "StubIap")


def settings_encoding(data: bytes) -> str:
    """The encoding to read and write an appx-portable.ini back in."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"


def settings_lines(game: Path) -> List[str]:
    """appx-portable.ini's lines; ANSI bytes survive as surrogates, written back."""
    path = game / "appx-portable.ini"
    if not path.is_file():
        return []
    data = path.read_bytes()
    return data.decode(settings_encoding(data), errors="surrogateescape").splitlines()


def option_values(lines: List[str]) -> Dict[str, str]:
    """The [options] keys (lower case) and values."""
    values, section = {}, ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.strip("[]").strip().lower()
        elif section == "options" and "=" in stripped and not stripped.startswith(";"):
            key, value = stripped.split("=", 1)
            values[key.strip().lower()] = value.strip()
    return values


def read_store(game: Path) -> str:
    """The Store choice appx-portable.ini holds: none, trial, iap or both."""
    values = option_values(settings_lines(Path(game)))
    on = tuple(values.get(key.lower(), "0") not in ("", "0") for key in STORE_KEYS)
    return next(name for name, choice in STORE_CHOICES.items() if choice == on)


def write_settings(game: Path, store: Optional[str]) -> None:
    """Create appx-portable.ini once, and set the Store choice unless store is
    None. Only the UnlockTrial and StubIap lines change; the rest is kept."""
    game = Path(game)
    path = game / "appx-portable.ini"
    lines = settings_lines(game) or SETTINGS_TEMPLATE.splitlines()
    if store is not None:
        wanted = {
            key: "1" if on else "0" for key, on in zip(STORE_KEYS, STORE_CHOICES[store])
        }
        start = next(
            (i for i, line in enumerate(lines) if line.strip().lower() == "[options]"),
            None,
        )
        if start is None:
            lines += ["", "[options]"] + [
                f"{key}={value}" for key, value in wanted.items()
            ]
        else:
            end = next(
                (
                    i
                    for i in range(start + 1, len(lines))
                    if lines[i].strip().startswith("[")
                ),
                len(lines),
            )
            for i in range(start + 1, end):
                key = lines[i].split("=", 1)[0].strip()
                match = next((k for k in wanted if k.lower() == key.lower()), None)
                if "=" in lines[i] and not lines[i].strip().startswith(";") and match:
                    lines[i] = f"{match}={wanted.pop(match)}"
            lines[start + 1 : start + 1] = [
                f"{key}={value}" for key, value in wanted.items()
            ]
    elif path.is_file():
        return
    encoding = settings_encoding(path.read_bytes()) if path.is_file() else "utf-8"
    errors = "strict" if encoding == "utf-16" else "surrogateescape"
    # CRLF, so Notepad on Windows 8 shows the lines.
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode(encoding, errors))
