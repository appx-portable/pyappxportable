"""Updating the runtime of an installed game keeps the game and its settings."""

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyappxportable import InstallError, InstallOptions, install_game
from pyappxportable.constants import PROXIES
from pyappxportable.main import main

MANIFEST = """\
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/2010/manifest">
  <Identity Name="Example.Game" Version="1.0.0.0" ProcessorArchitecture="x86"/>
  <Applications><Application Id="App" Executable="Game.exe"/></Applications>
</Package>
"""


def pe_image(imports=(), dll=False, marker=b"") -> bytes:
    """A minimal x86 PE image importing imports, its table inside the headers."""
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<H", data, 0x44, 0x14C)  # machine
    struct.pack_into("<H", data, 0x54, 224)  # optional header size
    struct.pack_into("<H", data, 0x56, 0x2102 if dll else 0x102)
    optional = 0x58
    struct.pack_into("<H", data, optional, 0x10B)
    struct.pack_into("<I", data, optional + 60, 0x400)  # SizeOfHeaders
    if imports:
        struct.pack_into("<I", data, optional + 96 + 8, 0x200)  # import table
        for i, name in enumerate(imports):
            struct.pack_into("<I", data, 0x200 + 20 * i + 12, 0x300 + 32 * i)
            data[0x300 + 32 * i : 0x300 + 32 * i + len(name)] = name.encode()
    return bytes(data) + marker


class RuntimeUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.game, self.build = root / "Game", root / "build"
        self.game.mkdir()
        self.build.mkdir()
        (self.game / "AppxManifest.xml").write_text(MANIFEST)
        (self.game / "Game.exe").write_bytes(pe_image(["d3dcompiler_47.dll"]))
        (self.game / "appx-portable.ini").write_bytes(b"[options]\r\nStubIap=1\r\n")
        (self.game / "SaveData").mkdir()
        (self.game / "SaveData" / "save.dat").write_bytes(b"progress")
        for name in ("appxportable.dll", *PROXIES):
            (self.game / name).write_bytes(pe_image(dll=True, marker=b"old"))
            (self.build / name).write_bytes(pe_image(dll=True, marker=b"new"))

    def run_main(self, *arguments):
        with contextlib.redirect_stdout(
            io.StringIO()
        ) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = main([*arguments, "--no-update-check"])
        return code, out.getvalue() + err.getvalue()

    def test_replaces_runtime_and_keeps_game(self):
        code, output = self.run_main(
            str(self.game), "--update-runtime", "--build", str(self.build), "--yes"
        )
        self.assertEqual(code, 0, output)
        for name in ("appxportable.dll", *PROXIES):
            self.assertTrue((self.game / name).read_bytes().endswith(b"new"), name)
        self.assertEqual(
            (self.game / "appx-portable.ini").read_bytes(),
            b"[options]\r\nStubIap=1\r\n",
        )
        self.assertEqual(
            (self.game / "SaveData" / "save.dat").read_bytes(), b"progress"
        )
        self.assertFalse(list(self.game.glob("00_click2startme*")))

    def test_refuses_a_folder_that_is_not_installed(self):
        (self.game / "appxportable.dll").unlink()
        code, output = self.run_main(
            str(self.game), "--update-runtime", "--build", str(self.build), "--yes"
        )
        self.assertEqual(code, 1)
        self.assertIn("not an installed game", output)
        self.assertFalse((self.game / PROXIES[0]).read_bytes().endswith(b"new"))

    def test_refuses_installation_options(self):
        code, output = self.run_main(
            str(self.game), "--update-runtime", "--dest", "elsewhere", "--yes"
        )
        self.assertEqual(code, 1)
        self.assertIn("--dest", output)

    def test_wrong_architecture_build_is_refused(self):
        x64 = bytearray(pe_image(dll=True))
        struct.pack_into("<H", x64, 0x44, 0x8664)
        (self.build / "appxportable.dll").write_bytes(bytes(x64))
        with self.assertRaises(InstallError), contextlib.redirect_stdout(io.StringIO()):
            install_game(
                InstallOptions(
                    package=str(self.game), build=str(self.build), update_runtime=True
                )
            )
        self.assertTrue((self.game / "appxportable.dll").read_bytes().endswith(b"old"))

    def test_interactive_install_offers_runtime_update(self):
        answers = iter([str(self.game), ""])
        options = InstallOptions(build=str(self.build), yes=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(install_game(options, lambda _: next(answers)), 0)
        self.assertTrue((self.game / "appxportable.dll").read_bytes().endswith(b"new"))
        self.assertFalse((self.game.parent / "Example.Game").exists())

    def test_declined_offer_installs_a_copy(self):
        answers = iter([str(self.game), "n"])
        options = InstallOptions(build=str(self.build), yes=False)
        with patch(
            "pyappxportable.installer.choose_folders",
            side_effect=InstallError("installing"),
        ), self.assertRaisesRegex(InstallError, "installing"):
            install_game(options, lambda _: next(answers))
        self.assertTrue((self.game / "appxportable.dll").read_bytes().endswith(b"old"))


if __name__ == "__main__":
    unittest.main()
