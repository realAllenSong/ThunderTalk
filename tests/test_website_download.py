"""The website's Download buttons fetch the app itself, in the version this tree is: the disk
image, which people drag into Applications. The zip beside it in each release is for the in-app
updater and Homebrew, which look for that name."""
import re
from pathlib import Path

import thundertalk

PAGE = (Path(__file__).parent.parent / "docs" / "index.html").read_text(encoding="utf-8")


def test_download_buttons_point_at_this_versions_disk_image():
    v = thundertalk.__version__
    want = f"https://github.com/realAllenSong/ThunderTalk/releases/download/v{v}/ThunderTalk-v{v}-macOS.dmg"
    links = re.findall(r'href="(https://github\.com/realAllenSong/ThunderTalk/releases/[^"]*)"[^>]*class="btn|class="btn[^"]*" href="(https://github\.com/realAllenSong/ThunderTalk/releases/[^"]*)"', PAGE)
    buttons = [a or b for a, b in links]
    assert len(buttons) == 3 and set(buttons) == {want}      # nav, hero, closing call: none sends people to a list of releases
    assert f'"downloadUrl": "{want}"' in PAGE and f'"softwareVersion": "{v}"' in PAGE
    assert want.endswith("-macOS.dmg")
    assert ".zip" not in PAGE.split("<body")[1].split("md.runtime")[0]           # no button left on the zip


def test_the_updater_never_mistakes_the_disk_image_for_its_zip(monkeypatch):
    import io
    import json

    from thundertalk.core import updater
    release = {"tag_name": "v99.0.0", "html_url": "", "body": "", "assets": [
        {"name": "ThunderTalk-v99.0.0-macOS.dmg", "browser_download_url": "https://x/dmg"},   # listed first
        {"name": "ThunderTalk-v99.0.0-macOS.zip", "browser_download_url": "https://x/zip"}]}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *a, **k: Resp(json.dumps(release).encode()))
    assert updater.check_for_update("1.0.0").zip_url == "https://x/zip"
