"""Downloads helpers for portable Windows Store games."""

import hashlib
import http.client
import io
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Generator, Optional, Tuple, TypeVar, cast

from rich.filesize import decimal

from pyappxportable.console import Ask, confirm, download_progress, note, warn
from pyappxportable.constants import REPOSITORY
from pyappxportable.errors import InstallError

# Tries before a failed download is reported, waiting 1 s, then 2 s, between them.
ATTEMPTS = 3
# Server answers worth trying again; other HTTP errors (404) fail at once.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
# Smaller downloads (patch files) show no progress bar.
PROGRESS_SIZE = 512 * 1024
CHUNK = 64 * 1024
USER_AGENT = "pyappxportable"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
BUILD_INSTEAD = "pass --build with a local build folder"
T = TypeVar("T")
DOWNLOAD_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException)

# Asks whether to try a failed download again; None (the default, and --yes)
# reports the failure instead.
RETRY_ASK: Optional[Ask] = None


@contextmanager
def retry_prompts(ask: Optional[Ask]) -> Generator[None, None, None]:
    """Offer to try failed downloads again with ask while inside the block."""
    global RETRY_ASK  # pylint: disable=global-statement
    previous, RETRY_ASK = RETRY_ASK, ask
    try:
        yield
    finally:
        RETRY_ASK = previous


@dataclass
class Partial:
    """What a download has received so far, kept so a retry can resume it."""

    data: bytearray = field(default_factory=bytearray)
    # The whole file's length; 0 when the server did not say.
    size: int = 0
    # The ETag or Last-Modified date that pins the file being resumed.
    validator: Optional[str] = None

    def headers(self) -> Dict[str, str]:
        """Request headers; with bytes in hand, a range asking for the rest. If-Range
        makes a server whose file changed send the whole new file instead."""
        headers = {"User-Agent": USER_AGENT}
        if self.data and self.validator:
            headers["Range"] = f"bytes={len(self.data)}-"
            headers["If-Range"] = self.validator
        return headers

    def continues(self, response) -> bool:
        """Whether a 206 response carries on from the bytes in hand. Some servers
        (GitHub's release storage) ignore If-Range, so the file is checked here
        too: the same length, and the same ETag when the validator is one."""
        start, total = content_range(response.headers.get("Content-Range"))
        if start != len(self.data) or (self.size and total and total != self.size):
            return False
        etag = response.headers.get("ETag")
        strong = (self.validator or "").startswith('"')
        return not (etag and strong and etag != self.validator)

    def begin(self, response) -> None:
        """Keep the bytes so far when response continues them; else start over."""
        if response.status == 206:
            _, total = content_range(response.headers.get("Content-Range"))
            if not self.continues(response):
                self.data.clear()
                self.validator = None
                raise http.client.HTTPException(
                    "the resumed download did not match the file"
                )
            self.size = total or self.size
            return
        # A whole file: the first answer, or a server that ignored the range.
        self.data.clear()
        self.size = int(response.headers.get("Content-Length") or 0)
        etag = response.headers.get("ETag")
        # If-Range only accepts a strong ETag.
        if etag and not etag.startswith("W/"):
            self.validator = etag
        else:
            self.validator = response.headers.get("Last-Modified")


def content_range(header: Optional[str]) -> Tuple[int, int]:
    """The first byte and whole length in a Content-Range header such as
    "bytes 100-199/200"; -1 for a start that cannot be read, 0 for an unknown length."""
    try:
        unit, _, rest = (header or "").partition(" ")
        span, _, total = rest.partition("/")
        if unit.strip().lower() != "bytes":
            return -1, 0
        return int(span.split("-", 1)[0]), int(total) if total.isdigit() else 0
    except ValueError:
        return -1, 0


def read_url(url: str, partial: Partial) -> bytes:
    """Download url into partial, resuming what it holds, with a progress bar for
    a large file; a download that stops short raises IncompleteRead."""
    request = urllib.request.Request(url, headers=partial.headers())
    with urllib.request.urlopen(request, timeout=60) as response:
        partial.begin(response)
        name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        if partial.size >= PROGRESS_SIZE:
            progress = download_progress(name, partial.size, len(partial.data))
        else:
            progress = nullcontext(lambda size: None)
        with progress as advance:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                partial.data += chunk
                advance(len(chunk))
    if partial.size and len(partial.data) < partial.size:
        raise http.client.IncompleteRead(b"", partial.size - len(partial.data))
    return bytes(partial.data)


def failure_reason(error: Exception) -> str:
    """A short reason for a failed download."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    if isinstance(error, http.client.IncompleteRead):
        return "the download stopped before the end"
    return str(getattr(error, "reason", error))


def transient(error: Exception) -> bool:
    """Whether trying again may help: a dropped connection, a busy server, or a
    resume range that no longer fits the file (which then starts over)."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRY_STATUS or error.code == 416
    return True


def retrying(
    url: str,
    instead: str,
    attempt: Callable[[Partial], T],
    missing_ok: bool = False,
) -> Optional[T]:
    """Run attempt, which reads url into the Partial it is given, until it works.

    After a dropped connection or a busy server it tries again, resuming from
    the bytes it has when the server allows; an attempt that got further does
    not count against the limit. Then, with prompts on, it offers to try again;
    a request that still fails is an InstallError naming url and what to do
    instead. With missing_ok, a 404 returns None at once."""
    partial, tries = Partial(), 1
    while True:
        received = len(partial.data)
        try:
            return attempt(partial)
        except urllib.error.HTTPError as error:
            failure = error
            error.close()  # it holds the server's error response open
            if error.code == 404 and missing_ok:
                return None
            if error.code == 416:  # the resume range no longer fits the file
                partial = Partial()
        except DOWNLOAD_ERRORS as error:
            failure = error
        if len(partial.data) > received:
            tries = 1
        reason = failure_reason(failure)
        resumable = bool(partial.data and partial.validator)
        if tries < ATTEMPTS and transient(failure):
            again = (
                f"resuming at {decimal(len(partial.data))}"
                if resumable
                else "trying again"
            )
            note(f"{reason}; {again} ({tries + 1}/{ATTEMPTS})")
            time.sleep(tries)
            tries += 1
            continue
        message = f"Could not download {url}: {reason}."
        if RETRY_ASK is not None:
            warn(message)
            question = "Resume the download?" if resumable else "Try again?"
            if confirm(RETRY_ASK, question):
                tries = 1
                continue
        raise InstallError(
            f"{message} Check the connection, or {instead}."
        ) from failure


def fetch(url: str, instead: str = BUILD_INSTEAD) -> bytes:
    """Download url, resuming and trying again as retrying does."""
    return cast(bytes, retrying(url, instead, lambda partial: read_url(url, partial)))


def final_url(url: str) -> str:
    """Where url redirects to, asked with HEAD so no page is downloaded."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="HEAD"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.geturl()


def resolve_release(tag: Optional[str]) -> str:
    """tag, or the latest release's when it is None. The latest release page
    redirects to the release's tag; reading that uses no GitHub API call, so the
    API's rate limit for callers without a token never applies."""
    if tag:
        return tag
    latest = f"{RELEASES}/latest"
    path = urllib.parse.urlsplit(
        cast(str, retrying(latest, BUILD_INSTEAD, lambda _: final_url(latest)))
    ).path
    if "/releases/tag/" not in path:
        raise InstallError(f"{RELEASES} has no release yet; {BUILD_INSTEAD}.")
    return urllib.parse.unquote(path.rsplit("/releases/tag/", 1)[1])


def release_asset(tag: str, name: str) -> Optional[bytes]:
    """Download the release asset name from its direct link; None when the
    release does not have it."""
    url = f"{RELEASES}/download/{urllib.parse.quote(tag, safe='')}/{name}"
    return retrying(
        url, BUILD_INSTEAD, lambda partial: read_url(url, partial), missing_ok=True
    )


def download_runtime(
    tag: str, architecture: str, folder: Path, wine: bool = False
) -> Path:
    """Download and unpack appx-portable-<tag>-<architecture>.zip into folder.

    Under Wine, the release's patched graphics stack is unpacked beside it."""
    name = f"appx-portable-{tag}-{architecture}.zip"
    data = release_asset(tag, name)
    if data is None:
        raise InstallError(
            f"Release {tag} does not exist or has no {name}. See {RELEASES}."
        )
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    unpack_release_zip(name, data, folder)
    if wine:
        graphics = f"appx-portable-{tag}-wine-{architecture}.zip"
        data = release_asset(tag, graphics)
        if data is None:
            warn(
                f"release {tag} has no Wine graphics stack; games may render black."
                " Build one with scripts/build-wined3d.sh and pass --build."
            )
        else:
            unpack_release_zip(graphics, data, folder)
    return folder


def unpack_release_zip(name: str, data: bytes, folder: Path) -> None:
    """Unpack the plain files of a release zip into folder."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.namelist():
                # The release zip holds plain DLLs; anything in a folder is ignored.
                if (
                    "/" not in entry
                    and "\\" not in entry
                    and entry not in ("", ".", "..")
                ):
                    (folder / entry).write_bytes(archive.read(entry))
    except zipfile.BadZipFile as error:
        raise InstallError(f"{name} is not a readable zip.") from error


def download_verified(url: str, sha256: str) -> bytes:
    """Download url, refusing data whose SHA-256 is not the pinned one."""
    data = fetch(url, "install it yourself")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise InstallError(f"{url} does not have the expected SHA-256 {sha256}.")
    return data
