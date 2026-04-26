"""In-app auto-updater for ThunderTalk.

Workflow:
  1. check_for_update() hits the GitHub Releases API and returns
     UpdateInfo if a strictly-newer version with a macOS zip asset
     is published. None if you're already on the latest, or on any
     network error (silent — we never block the UI).
  2. download_update() streams the asset to ~/Library/Caches/
     thundertalk/updates/ with progress callbacks.
  3. install_update() writes a tiny bash helper next to the zip,
     spawns it detached, and exits the app. The helper waits for
     the running process to terminate, swaps the .app in place,
     strips the quarantine xattr, and relaunches.

Direct overwrite of the running .app fails on macOS (the OS holds
the executable open), so the spawn-helper-then-exit dance is the
standard pattern; Sparkle does the same thing under the hood.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import ssl
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional


_API_URL = "https://api.github.com/repos/realAllenSong/ThunderTalk/releases/latest"
_USER_AGENT = "ThunderTalk-Updater"

_CACHE_DIR = pathlib.Path.home() / "Library" / "Caches" / "thundertalk" / "updates"


@dataclass
class UpdateInfo:
    version: str        # e.g. "1.1.0" (no leading "v")
    zip_url: str        # browser_download_url of the macOS zip asset
    release_url: str    # https://github.com/.../releases/tag/v1.1.0
    notes: str          # Markdown body of the release


# ── version comparison ────────────────────────────────────────────


def _parse_version(v: str) -> tuple[int, ...]:
    """'v1.0.3' / '1.0.3' → (1, 0, 3). Non-numeric segments raise
    ValueError to caller — handled at call site."""
    cleaned = v.lstrip("vV").strip()
    return tuple(int(p) for p in cleaned.split("."))


def _is_newer(remote: str, local: str) -> bool:
    try:
        return _parse_version(remote) > _parse_version(local)
    except (ValueError, AttributeError):
        return False


# ── public API ────────────────────────────────────────────────────


def check_for_update(current_version: str) -> Optional[UpdateInfo]:
    """Hit GitHub Releases API. Returns UpdateInfo if a newer macOS
    release is published, else None. Never raises — network errors
    are reported via stderr and resolved as None so background polls
    don't crash the app."""
    req = urllib.request.Request(_API_URL, headers={"User-Agent": _USER_AGENT})
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[Updater] check failed: {exc}")
        return None

    tag = data.get("tag_name") or ""
    if not tag or not _is_newer(tag, current_version):
        return None

    zip_url = ""
    for asset in data.get("assets", []) or []:
        name = asset.get("name", "")
        if name.endswith("-macOS.zip"):
            zip_url = asset.get("browser_download_url", "")
            break
    if not zip_url:
        # Newer tag exists but no macOS asset — treat as not-yet-published.
        return None

    return UpdateInfo(
        version=tag.lstrip("vV"),
        zip_url=zip_url,
        release_url=data.get("html_url", ""),
        notes=data.get("body", "") or "",
    )


def download_update(
    info: UpdateInfo,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> pathlib.Path:
    """Stream the zip to the cache dir. progress_cb(downloaded, total)
    fires every 64 KB chunk. Returns the path of the saved zip."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / f"ThunderTalk-v{info.version}-macOS.zip"

    req = urllib.request.Request(info.zip_url, headers={"User-Agent": _USER_AGENT})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        tmp = target.with_suffix(target.suffix + ".part")
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)
        tmp.replace(target)
    return target


def install_update(zip_path: pathlib.Path, app_path: pathlib.Path) -> None:
    """Schedule a detached helper that swaps the running .app once
    we exit, then relaunches. After this call returns, the caller
    should immediately QApplication.quit() — the helper is already
    waiting on our PID.

    Layout:
      tmpdir/
        ThunderTalk.app   ← extracted from the zip
        install.sh        ← waits, swaps, relaunches, self-cleans
    """
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="thundertalk-update-"))
    try:
        # IMPORTANT: don't use zipfile.extractall here. Python's zipfile
        # ignores the Unix permission bits stored in the ZIP's extra
        # data, so it strips the +x bit on Contents/MacOS/ThunderTalk
        # and silently drops the AppleDouble (._*) sidecars that carry
        # resource forks for signed Qt frameworks. The result was a
        # bundle that the helper's `ditto` copy faithfully reproduced
        # — broken — at /Applications, with errors:
        #   "code object is not signed at all"
        #   NSPOSIXErrorDomain Code=111 "Launch failed"
        # ditto -x -k is the symmetric Mac-aware extractor for the
        # same archive format we use to create the zip on the build
        # side; it preserves perms, xattrs, and AppleDouble metadata.
        subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(zip_path), str(tmpdir)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    # The zip ditto produces normally has ThunderTalk.app at the
    # root, but accept any depth as a defensive read.
    candidates = list(tmpdir.glob("ThunderTalk.app"))
    if not candidates:
        candidates = list(tmpdir.rglob("ThunderTalk.app"))
    if not candidates:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("ThunderTalk.app not found in update zip")
    new_app = candidates[0]

    # Sanity-check: the extracted binary must be executable, else the
    # spawn helper will faithfully reproduce a broken bundle. If the
    # +x bit is missing we'd rather fail in-process — the user still
    # has the running binary intact — than commit to a broken swap.
    new_binary = new_app / "Contents" / "MacOS" / "ThunderTalk"
    if not new_binary.is_file() or not os.access(new_binary, os.X_OK):
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(
            "Extracted bundle's executable is missing or not +x — refusing "
            "to install a broken bundle. Check /tmp/thundertalk-install.log "
            "or re-download manually from Releases."
        )

    helper = tmpdir / "install.sh"
    helper.write_text(
        "#!/bin/bash\n"
        "set -u\n"
        "# All output goes to /tmp/thundertalk-install.log so post-mortem\n"
        "# debugging works (the parent process is gone by the time the\n"
        "# helper runs, so the user can't see stderr otherwise).\n"
        "LOG=/tmp/thundertalk-install.log\n"
        "exec >> \"$LOG\" 2>&1\n"
        "echo \"=== install start: $(date) ===\"\n"
        f"OLD={shlex_quote(str(app_path))}\n"
        f"NEW={shlex_quote(str(new_app))}\n"
        "echo \"OLD=$OLD\"\n"
        "echo \"NEW=$NEW\"\n"
        "# Wait for the previous ThunderTalk to exit so macOS lets us\n"
        "# overwrite the bundle. Bound at 30 s; if the user refused to\n"
        "# quit, force-kill so the install isn't a permanent hang.\n"
        "for i in {1..60}; do\n"
        "    if ! pgrep -x ThunderTalk > /dev/null; then break; fi\n"
        "    sleep 0.5\n"
        "done\n"
        "pkill -x ThunderTalk 2>/dev/null || true\n"
        "sleep 0.5\n"
        "if [ ! -d \"$NEW\" ]; then\n"
        "    echo 'ERROR: new bundle missing'\n"
        "    exit 1\n"
        "fi\n"
        "BAK=\"${OLD}.update-old.$$\"\n"
        "echo \"backing up old to $BAK\"\n"
        "mv \"$OLD\" \"$BAK\" || { echo 'mv failed'; exit 1; }\n"
        "# IMPORTANT: use `ditto`, not `cp -R`. cp on macOS sometimes\n"
        "# strips the executable bit on the main binary inside\n"
        "# Contents/MacOS/, leaving the .app installed but unlaunchable\n"
        "# (\"can't open\" / \"cannot find code object on disk\"). ditto\n"
        "# is Apple's blessed bundle-aware copy and preserves perms,\n"
        "# extended attrs, code-sign integrity, and resource forks.\n"
        "echo 'copying with ditto'\n"
        "if ! ditto \"$NEW\" \"$OLD\"; then\n"
        "    echo 'ditto failed; rolling back'\n"
        "    rm -rf \"$OLD\" 2>/dev/null || true\n"
        "    mv \"$BAK\" \"$OLD\"\n"
        "    exit 1\n"
        "fi\n"
        "xattr -dr com.apple.quarantine \"$OLD\" 2>/dev/null || true\n"
        "echo 'codesign verify:'\n"
        "codesign --verify --verbose=2 \"$OLD\" 2>&1 | tail -3 || true\n"
        "echo 'removing backup'\n"
        "rm -rf \"$BAK\"\n"
        "echo 'relaunching'\n"
        "open \"$OLD\"\n"
        "sleep 2\n"
        f"rm -rf {shlex_quote(str(tmpdir))}\n"
        "echo \"=== install done: $(date) ===\"\n"
    )
    helper.chmod(0o755)

    # Detach so the helper outlives this Python process.
    subprocess.Popen(
        ["/bin/bash", str(helper)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def shlex_quote(s: str) -> str:
    """Single-quote-escape for bash. Local copy to avoid pulling in shlex
    just for this; we only quote known-safe absolute paths."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def installed_app_path() -> Optional[pathlib.Path]:
    """Resolve the path to the running ThunderTalk.app, if we're in
    one. Returns None when running from source (``.venv/bin/python
    run.py``) — auto-update is meaningless in that case."""
    # PyInstaller-bundled apps put the executable under
    # /path/to/ThunderTalk.app/Contents/MacOS/ThunderTalk and set
    # sys._MEIPASS to the bundle's _internal dir.
    import sys
    meipass = getattr(sys, "_MEIPASS", "")
    if not meipass:
        return None
    p = pathlib.Path(meipass)
    while p != p.parent:
        if p.suffix == ".app":
            return p
        p = p.parent
    return None
