"""ThunderTalk - Lightning-fast voice-to-text for every desktop."""

import os
import sys

__version__ = "0.2.0"


def asset_path(filename: str) -> str:
    """Resolve a path inside the assets/ directory, works in dev and PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", filename)
