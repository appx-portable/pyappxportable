"""Install a Windows Store game as a portable Appx Portable game folder.

Accepts .appxbundle, .appx, or an extracted package folder. Run without
arguments for interactive installation. With --update-runtime, give an
installed game folder to replace only its Appx Portable runtime DLLs."""

import argparse
import sys
from typing import Callable, List, Optional

from pyappxportable.errors import InstallError
from pyappxportable.installer import install_game
from pyappxportable.launchers import LAUNCHER
from pyappxportable.options import InstallOptions
from pyappxportable.settings import STORE_CHOICES
from pyappxportable.updates import installed_version, start_update_check


def parse_arguments(argv: List[str]):
    """The command line; every prompt has an option."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--version", action="version", version=f"pyappxportable {installed_version()}"
    )
    parser.add_argument(
        "--no-update-check",
        dest="check_updates",
        action="store_false",
        help="skip the background package update check",
    )
    parser.add_argument(
        "package", nargs="?", help=".appxbundle, .appx, or extracted package folder"
    )
    parser.add_argument(
        "--dest", help="folder to install the game in (default: beside the package)"
    )
    parser.add_argument(
        "--dependencies",
        action="append",
        default=[],
        help="dependency .appx or folder (VCLibs or .NET Native); repeat for several",
    )
    parser.add_argument(
        "--build",
        help="use the DLLs in this local build folder instead of downloading a release",
    )
    parser.add_argument(
        "--release", help="release tag to download (default: the latest)"
    )
    parser.add_argument(
        "--store", choices=sorted(STORE_CHOICES), help="Store overrides to turn on"
    )
    launcher = parser.add_mutually_exclusive_group()
    launcher.add_argument(
        "--launcher",
        action="store_true",
        default=None,
        help=f"write {LAUNCHER} (the default)",
    )
    launcher.add_argument(
        "--no-launcher",
        dest="launcher",
        action="store_false",
        help=f"do not write {LAUNCHER}",
    )
    parser.add_argument(
        "--no-patch",
        action="store_true",
        help="do not apply the patch for a known game",
    )
    parser.add_argument("--patch-dir", help="use this local appx-portable patch folder")
    parser.add_argument(
        "--desktop-copy",
        action="store_true",
        help="clear the AppContainer flag as Windows 8/8.1 need",
    )
    parser.add_argument(
        "--update-runtime",
        action="store_true",
        help="replace only the Appx Portable runtime in the installed game folder"
        " given as the package; game files, saves and settings are kept",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="never ask; fail when something required is missing",
    )
    return InstallOptions(**vars(parser.parse_args(argv)))


def main(argv: Optional[List[str]] = None, ask: Callable[[str], str] = input) -> int:
    """Install, printing any error as one line; returns the exit code."""
    options = parse_arguments(sys.argv[1:] if argv is None else argv)
    report = start_update_check() if options.check_updates else None
    try:
        code = install_game(options, ask)
    except KeyboardInterrupt:
        # Ctrl+C at a prompt leaves the cursor after the question.
        print("\nCancelled.", file=sys.stderr, flush=True)
        return 130
    except EOFError:
        print(
            "\nError: no answer could be read. Pass the package and options on the"
            " command line, with --yes to never ask.",
            file=sys.stderr,
            flush=True,
        )
        code = 1
    except (InstallError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        code = 1
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"Error: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        code = 1
    if report is not None:
        report(1.0)
    return code
