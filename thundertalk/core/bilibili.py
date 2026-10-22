"""Bilibili audio through its signed web API, without yt-dlp.

Since autumn 2026 Bilibili video pages no longer embed ``__INITIAL_STATE__``
and the unsigned ``/x/web-interface/view`` answers HTTP 412, so yt-dlp's
extractor fails ("Unable to extract initial state"). The web player itself
uses WBI-signed endpoints (``/wbi/view``, ``/player/wbi/playurl``) with an
anonymous visitor cookie; doing the same returns the title, the parts and the
DASH audio streams of any public video.

Links understood: /video/BV…, /video/av…, watch-later and list pages that
carry ``bvid=``, ``?p=`` part numbers, and b23.tv short links.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
REFERER = "https://www.bilibili.com/"
_MIXIN = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19,
          29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
          22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)
_BV_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b")
_AV_RE = re.compile(r"/video/av(\d+)", re.IGNORECASE)
_HOSTS = ("bilibili.com", "b23.tv")


class BilibiliError(RuntimeError):
    """``code`` matches links.LinkError codes (login, unavailable, blocked, …)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def is_bilibili(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _HOSTS)


def parse_ids(url: str) -> tuple[Optional[str], Optional[int], int]:
    """(bvid, aid, part) from a Bilibili URL; part is 1-based."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    bvid = (query.get("bvid") or [None])[0]
    if not bvid:
        m = _BV_RE.search(parsed.path)
        bvid = m.group(1) if m else None
    aid = None
    if not bvid:
        m = _AV_RE.search(parsed.path)
        aid = int(m.group(1)) if m else None
    try:
        part = max(1, int((query.get("p") or ["1"])[0]))
    except ValueError:
        part = 1
    return bvid, aid, part


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi                      # bundled with yt-dlp's requirements
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class _Client:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=_ssl_context()))
        self._mixin = ""

    def open(self, url: str, timeout: float = 20):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
        return self.opener.open(req, timeout=timeout)

    def json(self, url: str) -> dict:
        try:
            with self.open(url) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            raise BilibiliError("blocked" if exc.code in (403, 412, 429) else "network",
                                f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BilibiliError("network", str(exc)[:200]) from None
        return data

    def prime(self) -> None:
        """Visitor cookie and the WBI keys the web player signs requests with."""
        try:
            self.open("https://www.bilibili.com/").close()
        except Exception:
            pass                                # cookies are a courtesy; the keys are what count
        nav = self.json("https://api.bilibili.com/x/web-interface/nav")
        wbi = (nav.get("data") or {}).get("wbi_img") or {}
        keys = [os.path.splitext(u.rsplit("/", 1)[-1])[0] for u in (wbi.get("img_url"), wbi.get("sub_url")) if u]
        if len(keys) != 2:
            raise BilibiliError("blocked", "no WBI keys")
        raw = keys[0] + keys[1]
        self._mixin = "".join(raw[i] for i in _MIXIN if i < len(raw))[:32]

    def signed(self, base: str, params: dict) -> dict:
        params = dict(params, wts=int(time.time()))
        clean = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(params.items())}
        query = urllib.parse.urlencode(clean)
        w_rid = hashlib.md5((query + self._mixin).encode()).hexdigest()
        return self.json(f"{base}?{query}&w_rid={w_rid}")


def _check(resp: dict, what: str) -> dict:
    code = resp.get("code", -1)
    if code == 0:
        return resp.get("data") or {}
    message = str(resp.get("message") or "")
    if code in (-101, -403, 87008) or "登录" in message or "充电" in message:
        raise BilibiliError("login", message)
    if code in (-404, 62002, 62004, 62012):
        raise BilibiliError("unavailable", message)
    if code in (-412, -352, -799):
        raise BilibiliError("blocked", message)
    raise BilibiliError("other", f"{what} {code} {message}".strip())


def resolve_short(client: _Client, url: str) -> str:
    """b23.tv → the full bilibili.com URL it redirects to."""
    if (urllib.parse.urlparse(url).hostname or "").lower().endswith("b23.tv"):
        try:
            with client.open(url) as r:
                return r.geturl()
        except Exception as exc:
            raise BilibiliError("network", str(exc)[:200]) from None
    return url


def fetch(url: str, workdir: str, progress: Optional[Callable[[int, int, int], None]] = None,
          cancel: Optional[threading.Event] = None,
          on_title: Optional[Callable[[str], None]] = None) -> tuple[str, str, float]:
    """Download the audio of one Bilibili video part into ``workdir``.

    Returns (path, title, duration_seconds). Raises BilibiliError."""
    client = _Client()
    url = resolve_short(client, url)
    bvid, aid, part = parse_ids(url)
    if not bvid and not aid:
        raise BilibiliError("unsupported", "no video id in the link")
    client.prime()
    ident = {"bvid": bvid} if bvid else {"aid": aid}
    view = _check(client.signed("https://api.bilibili.com/x/web-interface/wbi/view", ident), "view")
    pages = view.get("pages") or []
    page = next((p for p in pages if p.get("page") == part), pages[0] if pages else None)
    cid = (page or {}).get("cid") or view.get("cid")
    if not cid:
        raise BilibiliError("unavailable", "no playable part")
    title = str(view.get("title") or bvid or aid)
    if len(pages) > 1 and page and page.get("part"):
        title = f"{title} – P{page.get('page')} {page['part']}"
    duration = float((page or {}).get("duration") or view.get("duration") or 0)
    if on_title:
        on_title(title)
    play = _check(client.signed("https://api.bilibili.com/x/player/wbi/playurl",
                                dict(ident, cid=cid, fnval=16, fourk=0)), "playurl")
    if play.get("is_preview") or play.get("v_voucher"):
        raise BilibiliError("login", "this video plays only a preview without signing in")
    audio = ((play.get("dash") or {}).get("audio") or [])
    if not audio:
        raise BilibiliError("no_audio", "no audio stream")
    best = max(audio, key=lambda a: (a.get("bandwidth") or 0, a.get("id") or 0))
    urls = [best.get("baseUrl") or best.get("base_url")] + list(best.get("backupUrl") or best.get("backup_url") or [])
    path = os.path.join(workdir, f"{bvid or aid}-p{part}.m4a")
    last: Exception | None = None
    for media in (u for u in urls if u):
        try:
            _download(client, media, path, progress, cancel)
            return path, title, duration
        except BilibiliError:
            raise
        except Exception as exc:            # try the mirror
            last = exc
    raise BilibiliError("network", str(last)[:200] if last else "download failed")


def _download(client: _Client, media: str, path: str, progress, cancel) -> None:
    try:
        with client.open(media, timeout=30) as r, open(path, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise BilibiliError("cancelled")
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(int(done * 100 / total) if total else -1, done, total)
    except urllib.error.HTTPError as exc:
        raise BilibiliError("blocked" if exc.code in (403, 412, 429) else "network", f"HTTP {exc.code}") from None
    if os.path.getsize(path) == 0:
        raise BilibiliError("no_audio", "empty audio file")
