"""Prompts come before the long steps and recover from mistyped answers."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyappxportable import InstallError, InstallOptions, install_game
from pyappxportable.console import choose
from pyappxportable.main import main

MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/2010/manifest">
  <Identity Name="Example.Game" Version="1.0.0.0" ProcessorArchitecture="x86"/>
  <Applications><Application Id="App" Executable="Game.exe"/></Applications>
</Package>
"""
CHOICES = {"none": "none", "trial": "trial", "iap": "iap", "both": "both"}


def answering(*answers):
    """An ask callback giving answers in order and recording the questions."""
    questions, remaining = [], iter(answers)

    def ask(question):
        questions.append(question)
        return next(remaining)

    return ask, questions


class PromptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name) / "Package"
        self.package.mkdir()
        (self.package / "AppxManifest.xml").write_text(MANIFEST)

    def test_choose_accepts_numbers_and_names(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(choose(answering("2")[0], "", CHOICES, "none"), "iap")
            self.assertEqual(choose(answering("Both")[0], "", CHOICES, "none"), "both")
            self.assertIsNone(choose(answering("")[0], "", CHOICES, "none"))

    def test_choose_asks_again_after_a_wrong_answer(self):
        ask, questions = answering("9", "x", "1")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(choose(ask, "Store overrides:", CHOICES, "none"), "trial")
        self.assertEqual(len(questions), 3)
        self.assertIn("Enter a number from 0 to 3", output.getvalue())

    def test_every_question_comes_before_extraction(self):
        missing = str(self.package.parent / "Missing.appx")
        ask, questions = answering(missing, f'"{self.package}"', "", "1", "n")
        options = InstallOptions(yes=False, launcher=None)
        with (
            patch(
                "pyappxportable.installer.extract_game",
                side_effect=InstallError("extracting"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(InstallError, "extracting"):
                install_game(options, ask)
        self.assertEqual(len(questions), 5)
        self.assertIn("does not exist", output.getvalue())
        self.assertIn("Install into", questions[2])
        self.assertIn("Create the", questions[4])

    def test_closed_input_explains_how_to_run_without_prompts(self):
        def closed(_):
            raise EOFError

        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--no-update-check"], ask=closed), 1)
        self.assertIn("--yes", errors.getvalue())

    def test_ctrl_c_cancels(self):
        def interrupted(_):
            raise KeyboardInterrupt

        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--no-update-check"], ask=interrupted), 130)
        self.assertIn("Cancelled.", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
