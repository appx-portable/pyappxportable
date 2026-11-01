"""Best-effort, cached PyPI update notices for the command line."""

import http.client
import json
import os
import platform
import sys
import tempfile
import threading
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

CHECK_INTERVAL = 24 * 60 * 60
PYPI_URL = "https://pypi.org/pypi/pyappxportable/json"


def installed_version() -> str:
    """Read the installed distribution version, including editable installs."""
    try:
        return version("pyappxportable")
    except PackageNotFoundError:
        return "0+unknown"


def cache_path() -> Path:
    """A user-writable cache outside the game and installation directories."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "pyappxportable" / "update.json"


def latest_compatible(releases: dict, python_version: str) -> Optional[str]:
    """Newest stable, non-yanked release installable with this Python."""
    candidates = []
    for name, files in releases.items():
        try:
            candidate = Version(name)
            if candidate.is_prerelease or candidate.is_devrelease:
                continue
            if any(
                not file.get("yanked", False)
                and python_version in SpecifierSet(file.get("requires_python") or "")
                for file in files
            ):
                candidates.append(candidate)
        except (InvalidVersion, InvalidSpecifier):
            continue
    return str(max(candidates)) if candidates else None


def read_cache(path: Path, now: float) -> Optional[dict]:
    """Return a fresh cache record; ignore stale, corrupt, or unreadable data."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("python") == platform.python_version()
            and 0 <= now - float(data["checked"]) < CHECK_INTERVAL
            and (data.get("latest") is None or isinstance(data["latest"], str))
        ):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_cache(path: Path, data: dict) -> None:
    """Atomically replace the cache; inability to cache never blocks a game."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def check_for_update() -> None:
    """Print an upgrade command when appropriate; network failures stay silent."""
    try:
        current = Version(installed_version())
        if current.local or current.is_devrelease or current.is_prerelease:
            return
        path, now = cache_path(), time.time()
        data = read_cache(path, now)
        if data is None:
            data = {"checked": now, "python": platform.python_version(), "latest": None}
            request = urllib.request.Request(
                PYPI_URL, headers={"User-Agent": "pyappxportable"}
            )
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    releases = json.load(response)["releases"]
                data["latest"] = latest_compatible(releases, platform.python_version())
            finally:
                # Cache failures too, but let an interrupted CLI retry next time.
                write_cache(path, data)
        latest = data["latest"]
        if latest and Version(latest) > current:
            print(
                f"pyappxportable {latest} is available (installed: {current}). "
                "Update with: python -m pip install --upgrade pyappxportable",
                file=sys.stderr,
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        http.client.HTTPException,
    ):
        pass


def start_update_check() -> None:
    """Run in the background without delaying startup or keeping the CLI open."""
    if os.environ.get("PYAPPXPORTABLE_NO_UPDATE_CHECK", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    threading.Thread(
        target=check_for_update, name="pyappxportable-update", daemon=True
    ).start()
