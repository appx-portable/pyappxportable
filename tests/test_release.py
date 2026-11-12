"""Check release eligibility and PyPI-compatible, monotonic version selection."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release


class ReleaseTests(unittest.TestCase):
    def test_conventional_commit_triggers(self):
        for message in (
            "fix: repair",
            "feat(cli): add option",
            "refactor: split",
            "perf: speed",
            "build!: break compatibility",
            "chore: upgrade\n\nBREAKING CHANGE: new API",
        ):
            with self.subTest(message=message):
                self.assertTrue(release.releasable(message))
        for message in (
            "docs: update",
            "test: cover",
            "chore: format",
            "prefix fix: repair",
            "fixed it",
        ):
            with self.subTest(message=message):
                self.assertFalse(release.releasable(message))

    def test_calendar_and_same_day_releases(self):
        self.assertEqual(release.next_version("2026.11.1", ["0.1.0"]), "2026.11.1")
        self.assertEqual(
            release.next_version("2026.11.1", ["v2026.11.1"]), "2026.11.1.1"
        )
        self.assertEqual(
            release.next_version("2026.11.1", ["2026.11.1.2"]), "2026.11.1.3"
        )
        self.assertEqual(
            release.next_version("2026.11.2", ["2026.11.1.2"]), "2026.11.2"
        )

    def test_future_versions_and_unrelated_tags(self):
        self.assertEqual(
            release.next_version("2026.11.1", ["v2026.11.2", "nonsense"]), "2026.11.2.1"
        )

    def test_push_uses_every_commit_and_manual_run_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            output = Path(directory) / "output"
            environment = {
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REPOSITORY": "yell0wsuit/pyappxportable",
            }
            with (
                patch.dict(os.environ, environment),
                patch.object(release, "github_versions", return_value=[]) as github,
                patch.object(release, "pypi_versions", return_value=[]),
            ):
                event.write_text(
                    json.dumps(
                        {
                            "commits": [
                                {"message": "fix: repair"},
                                {"message": "docs: update"},
                            ]
                        }
                    )
                )
                release.main()
                self.assertIn("release=true", output.read_text())
                output.unlink()
                event.write_text(json.dumps({"commits": [{"message": "docs: update"}]}))
                github.reset_mock()
                release.main()
                self.assertEqual(output.read_text(), "release=false\n")
                github.assert_not_called()
                output.unlink()
                os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
                release.main()
                self.assertIn("release=true", output.read_text())


if __name__ == "__main__":
    unittest.main()
