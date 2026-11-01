"""Choose an automatic PyPI release version for GitHub Actions."""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version

CONVENTIONAL = re.compile(r"^(?P<type>[a-z][a-z0-9-]*)(?:\([^)]+\))?(?P<breaking>!)?: ")


def releasable(message: str) -> bool:
    """Release fixes, features, performance, refactors, and breaking changes."""
    match = CONVENTIONAL.match(message)
    return bool(
        match
        and (
            match["type"] in {"feat", "fix", "perf", "refactor"}
            or match["breaking"]
            or re.search(r"(?m)^BREAKING[ -]CHANGE: ", message)
        )
    )


def next_version(day: str, used: list) -> str:
    """Use UTC calendar versions, with an increasing fourth release segment."""
    known = []
    for value in used:
        try:
            parsed = Version(value)
            if not parsed.is_prerelease and not parsed.is_devrelease:
                known.append(parsed)
        except InvalidVersion:
            continue
    # Stay monotonic even if a future-dated release or newer manual tag exists.
    highest = max(known, default=Version("0"))
    base = max(Version(day), Version(".".join(map(str, highest.release[:3]))))
    result, revision = str(base), 0
    while Version(result) <= highest:
        revision += 1
        result = f"{base}.{revision}"
    return result


def github_versions(repository: str) -> list:
    """Include tags without releases and draft release names to avoid reuse."""
    versions = []
    for endpoint, field in (("tags", "name"), ("releases", "tag_name")):
        result = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{repository}/{endpoint}",
                "--jq",
                f".[].{field}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        versions.extend(result.stdout.splitlines())
    return versions


def pypi_versions() -> list:
    """Include PyPI uploads whose GitHub release may have failed afterward."""
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/pyappxportable/json", timeout=30
        ) as response:
            return list(json.load(response)["releases"])
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise


def main() -> None:
    """Write the version and release decision to the Actions output file."""
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    release = os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch" or any(
        releasable(commit["message"]) for commit in event.get("commits", [])
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"release={str(release).lower()}\n")
        if release:
            date = datetime.now(timezone.utc)
            day = f"{date.year}.{date.month}.{date.day}"
            used = github_versions(os.environ["GITHUB_REPOSITORY"]) + pypi_versions()
            selected = next_version(day, used)
            output.write(f"version={selected}\ntag=v{selected}\n")
            print(f"Publishing pyappxportable {selected}")
        else:
            print("No releasable commits; validating without publishing.")


if __name__ == "__main__":
    main()
