"""Update checks must never interfere with game installation."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from pyappxportable import updates
from pyappxportable.main import main


class UpdateTests(unittest.TestCase):
    def test_compatible_stable_non_yanked_release(self):
        releases = {
            "0.1.0": [{"requires_python": ">=3.8"}],
            "2026.11.1": [{"requires_python": ">=3.8"}],
            "2026.11.1.1": [{"requires_python": ">=3.8"}],
            "2026.11.2": [{"requires_python": ">=3.12"}],
            "2026.11.3": [{"yanked": True}],
            "2026.12.1rc1": [{}],
            "2027.1.1": [],
            "invalid": [{}],
        }
        self.assertEqual(updates.latest_compatible(releases, "3.8.20"), "2026.11.1.1")

    def test_cached_notice_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            updates.write_cache(
                path,
                {
                    "checked": 1000,
                    "python": updates.platform.python_version(),
                    "latest": "2026.11.1",
                },
            )
            with (
                patch.object(updates, "cache_path", return_value=path),
                patch.object(updates, "installed_version", return_value="0.1.0"),
                patch.object(updates.time, "time", return_value=1001),
                patch.object(updates.urllib.request, "urlopen") as fetch,
            ):
                notice = updates.update_notice()
            fetch.assert_not_called()
            self.assertIn("python -m pip install --upgrade pyappxportable", notice)

    def test_offline_failure_is_silent_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            with (
                patch.object(updates, "cache_path", return_value=path),
                patch.object(updates, "installed_version", return_value="0.1.0"),
                patch.object(
                    updates.urllib.request,
                    "urlopen",
                    side_effect=urllib.error.URLError("offline"),
                ) as fetch,
            ):
                self.assertIsNone(updates.update_notice())
                self.assertIsNone(updates.update_notice())
                self.assertEqual(fetch.call_count, 1)

    def test_fresh_fetch_uses_timeout_and_caches_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            response = io.StringIO(json.dumps({"releases": {"2026.11.1": [{}]}}))
            with (
                patch.object(updates, "cache_path", return_value=path),
                patch.object(updates, "installed_version", return_value="2026.11.1"),
                patch.object(
                    updates.urllib.request, "urlopen", return_value=response
                ) as fetch,
            ):
                self.assertIsNone(updates.update_notice())
                self.assertEqual(fetch.call_args.kwargs["timeout"], 2)
            self.assertEqual(json.loads(path.read_text())["latest"], "2026.11.1")

    def test_expired_and_corrupt_caches_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            for content in ("not json", "[]", '{"checked": "invalid"}'):
                path.write_text(content)
                self.assertIsNone(updates.read_cache(path, 1000))
            updates.write_cache(
                path,
                {
                    "checked": 0,
                    "python": updates.platform.python_version(),
                    "latest": "1.0",
                },
            )
            self.assertIsNone(updates.read_cache(path, updates.CHECK_INTERVAL + 1))

    def test_development_install_does_not_check(self):
        with (
            patch.object(updates, "installed_version", return_value="1.0.dev1"),
            patch.object(updates.urllib.request, "urlopen") as fetch,
        ):
            self.assertIsNone(updates.update_notice())
        fetch.assert_not_called()

    def test_environment_opt_out(self):
        with (
            patch.dict(os.environ, {"PYAPPXPORTABLE_NO_UPDATE_CHECK": "1"}),
            patch.object(updates.threading, "Thread") as thread,
        ):
            updates.start_update_check()(0)
        thread.assert_not_called()

    def test_cli_opt_out_and_help_never_start_check(self):
        with (
            patch("pyappxportable.main.start_update_check") as check,
            patch("pyappxportable.main.install_game", return_value=0),
        ):
            self.assertEqual(main(["--yes", "--no-update-check"]), 0)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as result,
            ):
                main(["--help"])
            self.assertEqual(result.exception.code, 0)
        check.assert_not_called()

    def test_notice_waits_until_the_installer_is_done(self):
        notice = "pyappxportable 9 is available"
        with (
            patch.object(updates, "update_notice", return_value=notice),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            report = updates.start_update_check()
            self.assertEqual(output.getvalue(), "")
            report(5)
        self.assertIn(notice, output.getvalue())

    def test_cli_prints_notice_after_prompts(self):
        events = []

        def install(_options, ask):
            ask("Question: ")
            return 0

        with (
            patch("pyappxportable.main.install_game", side_effect=install),
            patch(
                "pyappxportable.main.start_update_check",
                return_value=lambda wait: events.append("notice"),
            ),
        ):
            self.assertEqual(main([], ask=lambda _: events.append("ask") or ""), 0)
        self.assertEqual(events, ["ask", "notice"])


if __name__ == "__main__":
    unittest.main()
