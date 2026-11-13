"""Updating the runtime of an installed game keeps the game and its settings."""

import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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


XAML_APPLICATION = "Windows.UI.Xaml.Application".encode("utf-16-le")


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
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
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
        with (
            patch(
                "pyappxportable.installer.choose_folders",
                side_effect=InstallError("installing"),
            ),
            self.assertRaisesRegex(InstallError, "installing"),
        ):
            install_game(options, lambda _: next(answers))
        self.assertTrue((self.game / "appxportable.dll").read_bytes().endswith(b"old"))

    def update(self):
        code, output = self.run_main(
            str(self.game), "--update-runtime", "--build", str(self.build), "--yes"
        )
        self.assertEqual(code, 0, output)
        return output

    @unittest.skipUnless(sys.platform == "win32", "XAML Islands need Windows")
    def test_native_xaml_game_gets_island_manifest(self):
        # Windows 8.0 games ship source XAML; it runs on Windows' XAML in an island.
        (self.game / "MainPage.xaml").write_text("<Page/>")
        self.update()
        manifest = self.game / "Game.exe.manifest"
        self.assertIn('maxversiontested Id="10.0.18362.0"', manifest.read_text())

    @unittest.skipUnless(sys.platform == "win32", "XAML Islands need Windows")
    def test_code_only_xaml_game_gets_island_manifest(self):
        # Asphalt 8 builds its XAML in code, in a DLL the game imports, and
        # ships no XAML files.
        (self.game / "Game.exe").write_bytes(
            pe_image(["d3dcompiler_47.dll", "toolkit.dll"])
        )
        (self.game / "Toolkit.dll").write_bytes(
            pe_image(dll=True, marker=XAML_APPLICATION)
        )
        self.update()
        self.assertTrue((self.game / "Game.exe.manifest").is_file())

    def test_corewindow_game_gets_no_manifest(self):
        self.update()
        self.assertFalse((self.game / "Game.exe.manifest").exists())

    def test_shim_naming_xaml_does_not_make_a_xaml_game(self):
        # The game imports a proxy, which imports the shim; both name XAML
        # classes themselves.
        for name in ("appxportable.dll", *PROXIES):
            (self.build / name).write_bytes(
                pe_image(["appxportable.dll"], dll=True, marker=XAML_APPLICATION)
            )
        self.update()
        self.assertFalse((self.game / "Game.exe.manifest").exists())

    def test_known_portable_xaml_game_gets_no_manifest(self):
        (self.game / "AppxManifest.xml").write_text(
            MANIFEST.replace(
                "Example.Game", "Ubisoft.RaymanFiestaRunWindows10Edition"
            ).replace("1.0.0.0", "1.0.23.0")
        )
        (self.game / "MainPage.xaml").write_text("<Page/>")
        self.update()
        self.assertFalse((self.game / "Game.exe.manifest").exists())

    def update_on(self, platform):
        """Update the runtime as the installer would on platform; off Windows
        it stages for Wine."""
        with patch("pyappxportable.installer.sys", SimpleNamespace(platform=platform)):
            return self.update()

    def with_components(self):
        (self.game / "AppxManifest.xml").write_text(
            MANIFEST.replace("</Package>", COMPONENTS)
        )

    def test_csharp_components_get_the_clr_bridge(self):
        self.with_components()
        (self.build / "appx-host.exe").write_bytes(pe_image(marker=b"bridge"))
        self.update_on("win32")
        host = self.game / "appx-host.exe"
        self.assertTrue(host.is_file() and host.read_bytes().endswith(b"bridge"))

    def test_csharp_components_without_bridge_in_runtime(self):
        self.with_components()
        self.assertIn("no appx-host.exe", self.update_on("win32"))
        self.assertFalse((self.game / "appx-host.exe").exists())

    def test_csharp_components_get_no_bridge_under_wine(self):
        self.with_components()
        (self.build / "appx-host.exe").write_bytes(pe_image(marker=b"bridge"))
        self.assertNotIn("no appx-host.exe", self.update_on("linux"))
        self.assertFalse((self.game / "appx-host.exe").exists())


COMPONENTS = """\
  <Extensions><Extension Category="windows.activatableClass.inProcessServer">
    <InProcessServer><Path>CLRHost.dll</Path>
      <ActivatableClass ActivatableClassId="Analytics.Api" ThreadingModel="both"/>
    </InProcessServer></Extension></Extensions>
</Package>
"""


if __name__ == "__main__":
    unittest.main()
