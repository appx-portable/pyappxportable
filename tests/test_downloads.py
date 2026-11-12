"""Downloads resume or try again after dropped connections, and can be retried."""

import contextlib
import io
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from pyappxportable import InstallError, console, downloads

URL = "https://example.com/files/appx-portable-v1-x86.zip"
FILE = bytes(range(256)) * 4096  # 1 MiB
ETAG = '"abc"'


class Response(io.BytesIO):
    """A urlopen response serving body; length is what it claims to send."""

    def __init__(self, body, length=None, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {
            "Content-Length": str(len(body) if length is None else length),
            **(headers or {}),
        }


def whole(cut=None, headers=None):
    """The file, stopping after cut bytes when given."""
    body = FILE if cut is None else FILE[:cut]
    return Response(body, length=len(FILE), headers={"ETag": ETAG, **(headers or {})})


def rest(start, first=None):
    """A 206 answer continuing the file from start (claiming first as its start)."""
    first = start if first is None else first
    return Response(
        FILE[start:],
        status=206,
        headers={"Content-Range": f"bytes {first}-{len(FILE) - 1}/{len(FILE)}"},
    )


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "Error", {}, io.BytesIO())


class DownloadTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(downloads.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.requests = []

    def fetch(self, *answers):
        """fetch(URL), the server giving answers (responses or errors) in order."""
        remaining = list(answers)

        def urlopen(request, timeout):
            self.requests.append(request)
            answer = remaining.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(downloads.urllib.request, "urlopen", side_effect=urlopen):
            return downloads.fetch(URL)

    def ranges(self):
        return [
            (r.get_header("Range"), r.get_header("If-range")) for r in self.requests
        ]

    def test_dropped_connection_is_tried_again(self):
        offline = urllib.error.URLError("connection reset")
        self.assertEqual(self.fetch(offline, http_error(503), whole()), FILE)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual([c.args for c in self.sleep.call_args_list], [(1,), (2,)])
        self.assertIn("trying again (3/3)", self.output.getvalue())

    def test_missing_file_fails_at_once(self):
        with self.assertRaisesRegex(InstallError, "HTTP 404"):
            self.fetch(http_error(404))
        self.assertEqual(len(self.requests), 1)

    def test_attempts_run_out(self):
        offline = urllib.error.URLError("offline")
        with self.assertRaisesRegex(InstallError, "Check the connection"):
            self.fetch(offline, offline, offline)
        self.assertEqual(len(self.requests), 3)

    def test_interrupted_download_resumes(self):
        self.assertEqual(self.fetch(whole(cut=1000), rest(1000)), FILE)
        self.assertEqual(self.ranges(), [(None, None), ("bytes=1000-", ETAG)])
        self.assertIn("resuming at 1.0 kB", self.output.getvalue())

    def test_resume_progress_does_not_use_up_attempts(self):
        cuts = [whole(cut=1000)] + [
            Response(
                FILE[start : start + 1000],
                status=206,
                headers={"Content-Range": f"bytes {start}-{len(FILE) - 1}/{len(FILE)}"},
            )
            for start in range(1000, 6000, 1000)
        ]
        self.assertEqual(self.fetch(*cuts, rest(6000)), FILE)
        self.assertEqual(len(self.requests), 7)

    def test_server_ignoring_the_range_sends_the_whole_file(self):
        self.assertEqual(self.fetch(whole(cut=1000), whole()), FILE)
        self.assertEqual(self.ranges()[1], ("bytes=1000-", ETAG))

    def test_no_validator_starts_over(self):
        first = Response(FILE[:1000], length=len(FILE))
        self.assertEqual(self.fetch(first, Response(FILE)), FILE)
        self.assertEqual(self.ranges(), [(None, None), (None, None)])

    def test_weak_etag_falls_back_to_last_modified(self):
        date = "Wed, 11 Nov 2026 10:00:00 GMT"
        first = whole(cut=1000, headers={"ETag": 'W/"abc"', "Last-Modified": date})
        self.assertEqual(self.fetch(first, rest(1000)), FILE)
        self.assertEqual(self.ranges()[1], ("bytes=1000-", date))

    def test_wrong_resume_place_starts_over(self):
        answers = (whole(cut=1000), rest(1000, first=500), whole())
        self.assertEqual(self.fetch(*answers), FILE)
        self.assertEqual(self.ranges()[2], (None, None))

    def test_changed_file_starts_over_when_if_range_is_ignored(self):
        # GitHub's release storage answers 206 even when If-Range does not match.
        changed = rest(1000)
        changed.headers["ETag"] = '"new"'
        self.assertEqual(self.fetch(whole(cut=1000), changed, whole()), FILE)
        self.assertEqual(self.ranges()[2], (None, None))
        self.assertIn("did not match", self.output.getvalue())

    def test_changed_length_starts_over(self):
        longer = rest(1000)
        longer.headers["Content-Range"] = f"bytes 1000-{len(FILE)}/{len(FILE) + 1}"
        self.assertEqual(self.fetch(whole(cut=1000), longer, whole()), FILE)
        self.assertEqual(self.ranges()[2], (None, None))

    def test_range_not_satisfiable_starts_over(self):
        self.assertEqual(self.fetch(whole(cut=1000), http_error(416), whole()), FILE)
        self.assertEqual(self.ranges()[2], (None, None))

    def test_prompt_offers_another_round(self):
        offline = urllib.error.URLError("offline")
        answers = iter(["", "n"])
        with downloads.retry_prompts(lambda _: next(answers)):
            with self.assertRaises(InstallError):
                self.fetch(*[offline] * 6)
        self.assertEqual(len(self.requests), 6)
        self.assertIsNone(downloads.RETRY_ASK)

    def test_prompt_resumes(self):
        questions = []
        offline = urllib.error.URLError("offline")
        with downloads.retry_prompts(lambda q: questions.append(q) or "y"):
            answers = (whole(cut=1000), offline, offline, rest(1000))
            self.assertEqual(self.fetch(*answers), FILE)
        self.assertEqual(questions, ["Resume the download? [Y/n]: "])
        self.assertEqual(self.ranges()[3], ("bytes=1000-", ETAG))

    def test_progress_bar_for_large_files(self):
        screen = io.StringIO()
        terminal = Console(file=screen, force_terminal=True, width=100)
        with patch.object(console, "OUT", terminal):
            self.assertEqual(self.fetch(whole(cut=1000), rest(1000)), FILE)
        self.assertIn("appx-portable-v1-x86.zip", screen.getvalue())
        self.assertIn("1.0/1.0 MB", screen.getvalue())

    def test_small_download_has_no_bar(self):
        screen = io.StringIO()
        terminal = Console(file=screen, force_terminal=True, width=100)
        with patch.object(console, "OUT", terminal):
            self.assertEqual(self.fetch(Response(b"{}")), b"{}")
        self.assertEqual(screen.getvalue(), "")


class ReleaseTests(unittest.TestCase):
    """Releases are found without the GitHub API, so no rate limit applies."""

    def setUp(self):
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_latest_tag_comes_from_the_redirect(self):
        tag_page = f"{downloads.RELEASES}/tag/v2026.11.12"
        with patch.object(downloads, "final_url", return_value=tag_page) as head:
            self.assertEqual(downloads.resolve_release(None), "v2026.11.12")
            self.assertEqual(downloads.resolve_release("v1"), "v1")
        head.assert_called_once_with(f"{downloads.RELEASES}/latest")

    def test_no_release_yet(self):
        with patch.object(downloads, "final_url", return_value=downloads.RELEASES):
            with self.assertRaisesRegex(InstallError, "no release yet"):
                downloads.resolve_release(None)

    def test_runtime_comes_from_direct_links(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("appxportable.dll", b"dll")
        urls = []

        def read_url(url, _partial):
            urls.append(url)
            if "-wine-" in url:
                raise http_error(404)
            return archive.getvalue()

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(downloads, "read_url", side_effect=read_url),
        ):
            downloads.download_runtime("v1", "x86", Path(folder), wine=True)
            self.assertTrue((Path(folder) / "appxportable.dll").is_file())
        base = f"{downloads.RELEASES}/download/v1/"
        self.assertEqual(
            urls,
            [base + "appx-portable-v1-x86.zip", base + "appx-portable-v1-wine-x86.zip"],
        )
        self.assertIn("no Wine graphics stack", self.output.getvalue())

    def test_missing_runtime_asset(self):
        with patch.object(downloads, "read_url", side_effect=http_error(404)):
            with self.assertRaisesRegex(InstallError, "has no appx-portable-v9-x86"):
                downloads.download_runtime("v9", "x86", Path("unused"))


if __name__ == "__main__":
    unittest.main()
