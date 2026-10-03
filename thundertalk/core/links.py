"""Audio from a web link (YouTube, Bilibili, … anything yt-dlp supports).

Only the audio track is fetched, into a private temp folder the caller deletes
when done. The preferred format is AAC/M4A because macOS `afconvert` decodes
it without ffmpeg; sites that only offer Opus/WebM audio then need ffmpeg (see
audio_io). yt-dlp is used as a library, not as a command, so it ships inside
the app bundle.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

TEMP_PREFIX = "thundertalk-link-"
# AAC first: afconvert reads it natively. Then anything audio-only, then a
# small muxed file as the last resort (afconvert reads the audio of an mp4).
FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio[ext=mp3]/bestaudio/best[ext=mp4]/best"

_URL_RE = re.compile(r"https?://[^\s<>\"'，。、「」【】（）]+", re.I)
_BARE_RE = re.compile(r"^(?:www\.|m\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*$", re.I)
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

DownloadCB = Callable[[int, int, int], None]      # percent (-1 = unknown), bytes done, bytes total


class LinkError(RuntimeError):
    """A link could not be fetched. ``code`` is one of: missing, unsupported,
    playlist, live, private, login, geo, unavailable, no_audio, blocked,
    network, other."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


class LinkCancelled(Exception):
    pass


@dataclass
class Fetched:
    path: str              # downloaded audio file (inside ``workdir``)
    title: str
    duration: float
    url: str
    workdir: str           # temp folder to delete when finished


# ── recognising links ────────────────────────────────────────────────────

def find_urls(text: str) -> list[str]:
    """Links in pasted text, in order. Accepts bare ``youtu.be/…`` style links
    and share blurbs such as "【Title-哔哩哔哩】 https://b23.tv/xyz"."""
    out: list[str] = []
    for m in _URL_RE.findall(text or ""):
        u = m.rstrip(".,;:!?)]}>")
        if u not in out:
            out.append(u)
    if not out:
        for word in (text or "").split():
            if _BARE_RE.match(word):
                out.append("https://" + word)
    return out


def is_url(text: str) -> bool:
    return bool(find_urls(text))


def site_name(url: str) -> str:
    """"youtube.com" for "https://www.youtube.com/watch?v=…"."""
    host = re.sub(r"^[a-z]+://", "", url, flags=re.I).split("/")[0].split(":")[0].lower()
    for pre in ("www.", "m."):
        if host.startswith(pre):
            host = host[len(pre):]
    return host


def safe_name(title: str, fallback: str = "transcript") -> str:
    """A title usable as a file name on macOS (no slashes/colons, ≤ 80 chars)."""
    name = _BAD_NAME.sub(" ", title or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:80].rstrip()
    return name or fallback


# ── errors ───────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("private", ("private video", "video is private", "this video is private")),
    ("login", ("sign in", "log in", "login", "logged-in", "cookies", "members-only", "member-only",
               "registered users", "premium", "confirm your age", "age-restricted", "大会员")),
    ("blocked", ("http error 412", "precondition failed", "http error 429", "too many requests",
                 "http error 403")),
    ("unavailable", ("may be deleted",)),                # Bilibili: "deleted or geo-restricted"
    ("geo", ("not available in your country", "geo restrict", "geo-restrict", "your region",
             "not made this video available")),
    ("no_audio", ("no video formats", "requested format is not available")),
    ("unavailable", ("video unavailable", "has been removed", "does not exist", "http error 404")),
    ("unsupported", ("unsupported url", "is not a valid url")),
    ("network", ("urlopen error", "nodename nor servname", "name or service not known",
                 "temporary failure in name resolution", "failed to resolve", "getaddrinfo",
                 "timed out", "network is unreachable", "connection refused", "connection reset",
                 "remote end closed", "no route to host", "ssl", "unable to download webpage")),
]


def classify(message: str) -> str:
    low = (message or "").lower()
    for code, needles in _PATTERNS:
        if any(n in low for n in needles):
            return code
    return "other"


def _short(message: str) -> str:
    msg = re.sub(r"^ERROR:\s*", "", (message or "").strip())
    msg = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", msg)      # "[youtube] abc123: "
    return msg.split("\n")[0][:200]


# ── download ─────────────────────────────────────────────────────────────

class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        print(f"[Link] {msg}")


def _ydl_options(workdir: str, hook) -> dict:
    return {
        "format": FORMAT,
        "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "progress_hooks": [hook],
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "overwrites": True,
        "nopart": False,
        "cachedir": False,
        "postprocessors": [],
    }


def fetch_audio(
    url: str,
    progress: Optional[DownloadCB] = None,
    cancel: Optional[threading.Event] = None,
    on_title: Optional[Callable[[str], None]] = None,
    workdir: Optional[str] = None,
) -> Fetched:
    """Download the audio of ``url`` into a new temp folder.

    Raises LinkError (with a code the UI translates) or LinkCancelled. On any
    failure the temp folder is removed; on success the caller owns it."""
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:                        # pragma: no cover - packaging error
        raise LinkError("missing", str(exc)) from exc

    own_dir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix=TEMP_PREFIX)

    def hook(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise LinkCancelled()
        if d.get("status") == "downloading" and progress:
            done = int(d.get("downloaded_bytes") or 0)
            total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            progress(int(done * 100 / total) if total else -1, done, total)

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise LinkCancelled()

    try:
        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(_ydl_options(workdir, hook)) as ydl:
                    info = ydl.extract_info(url, download=False)
                    check()
                    if info.get("_type") == "playlist" or info.get("entries") is not None:
                        raise LinkError("playlist")
                    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                        raise LinkError("live")
                    title = str(info.get("title") or info.get("id") or site_name(url))
                    if on_title:
                        on_title(title)
                    info = ydl.process_ie_result(info, download=True)
                break
            except DownloadError as exc:
                code = classify(str(exc))
                # Bilibili answers bursts of requests with HTTP 412; one calm retry usually passes.
                if code == "blocked" and attempt == 0 and not (cancel is not None and cancel.is_set()):
                    time.sleep(2.0)
                    continue
                raise LinkError(code, _short(str(exc))) from None
        check()
        path = _downloaded_file(info, workdir)
        if not path:
            raise LinkError("no_audio", "no audio file was produced")
        return Fetched(path, title, float(info.get("duration") or 0.0), url, workdir)
    except BaseException:
        if own_dir:
            shutil.rmtree(workdir, ignore_errors=True)
        raise


def _downloaded_file(info: dict, workdir: str) -> str:
    for req in info.get("requested_downloads") or []:
        p = req.get("filepath") or req.get("_filename")
        if p and os.path.isfile(p):
            return p
    files = [p for p in Path(workdir).iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
    return str(max(files, key=lambda p: p.stat().st_size)) if files else ""


def cleanup_stale(max_age_s: float = 6 * 3600) -> int:
    """Delete link temp folders left behind by a crash. Returns how many."""
    n = 0
    root = Path(tempfile.gettempdir())
    now = time.time()
    try:
        for p in root.glob(TEMP_PREFIX + "*"):
            try:
                if p.is_dir() and now - p.stat().st_mtime > max_age_s:
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n
