"""Constants helpers for portable Windows Store games."""

import re

ARCHITECTURES = {0x14C: "x86", 0x8664: "x64"}
# Package files the game never reads.
PACKAGE_METADATA = {
    "appxsignature.p7x",
    "appxblockmap.xml",
    "[content_types].xml",
    "appxmetadata",
    "microsoft.system.package.metadata",
}
# Bundle application packages in order of preference: Windows 8 and 8.1 games
# are tested as x86, Windows 10 games as x64.
BUNDLE_ARCHITECTURES = ("x86", "neutral", "x64")
UNIVERSAL_BUNDLE_ARCHITECTURES = ("x64", "neutral", "x86")
IDENTITY_NAME = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9.-]{3,50}$")
INVALID_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')

PROXIES = ("d3dcompiler_47.dll", "XAudio2_8.dll", "DWrite.dll")
REPOSITORY = "yell0wsuit/appx-portable"
