"""Options shared by the command line and Python API."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class InstallOptions:  # pylint: disable=too-many-instance-attributes
    """Installation inputs; Python API calls run without prompts by default.

    Paths are strings. Set yes=False and pass an ask callback to install_game
    for interactive use. A missing store value preserves existing settings.
    With update_runtime, package is an installed game folder whose Appx
    Portable runtime is replaced; its game files and settings are kept.
    """

    package: Optional[str] = None
    dest: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    build: Optional[str] = None
    release: Optional[str] = None
    store: Optional[str] = None
    launcher: Optional[bool] = True
    no_patch: bool = False
    desktop_copy: bool = False
    patch_dir: Optional[str] = None
    yes: bool = True
    check_updates: bool = True
    update_runtime: bool = False
