"""The website's Download buttons fetch the app itself, in the version this tree is."""
import re
from pathlib import Path

import thundertalk

PAGE = (Path(__file__).parent.parent / "docs" / "index.html").read_text(encoding="utf-8")


def test_download_buttons_point_at_this_versions_zip():
    v = thundertalk.__version__
    want = f"https://github.com/realAllenSong/ThunderTalk/releases/download/v{v}/ThunderTalk-v{v}-macOS.zip"
    links = re.findall(r'href="(https://github\.com/realAllenSong/ThunderTalk/releases/[^"]*)"[^>]*class="btn|class="btn[^"]*" href="(https://github\.com/realAllenSong/ThunderTalk/releases/[^"]*)"', PAGE)
    buttons = [a or b for a, b in links]
    assert len(buttons) == 3 and set(buttons) == {want}      # nav, hero, closing call: none sends people to a list of releases
    assert f'"downloadUrl": "{want}"' in PAGE and f'"softwareVersion": "{v}"' in PAGE
    assert f"New in v{v}" in PAGE
    # The file name is what the in-app updater and the Homebrew cask look for too.
    assert want.endswith("-macOS.zip") and "macos" in want.lower()
