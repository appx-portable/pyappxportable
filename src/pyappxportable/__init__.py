"""Install Windows Store games as portable desktop applications."""

from pyappxportable.errors import InstallError
from pyappxportable.installer import install_game
from pyappxportable.manifest import Manifest
from pyappxportable.options import InstallOptions

__all__ = ["InstallError", "InstallOptions", "Manifest", "install_game"]
