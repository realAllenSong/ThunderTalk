"""Burn a transcript into a copy of a video as on-screen subtitles.

Subtitles are drawn by Qt (CoreText, so PingFang SC and every other system
font render exactly as in the app) into transparent PNGs, one per cue, and
ffmpeg lays them over the picture with its ``overlay`` filter, fed by a concat
list that holds each image for its cue's duration. This needs only the PNG
decoder and ``overlay``, which every ffmpeg build has — Homebrew's current
ffmpeg ships without libass/freetype, so the usual ``subtitles=``/``drawtext``
filters are not available there. Optionally the same cues are also added as a
selectable (soft) subtitle track.

ffmpeg itself is not bundled with the app: ``audio_io.find_ffmpeg()`` locates
an installed one, and the UI explains how to get it when missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from thundertalk.core import audio_io
from thundertalk.core.transcribe import Segment, Transcript, cues_to_srt, subtitle_cues

VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})
_KEEP_CONTAINER = (".mp4", ".mov", ".m4v")
_FONT_FAMILIES = ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Helvetica Neue", "Arial Unicode MS"]

ProgressCB = Callable[[int, str], None]


class BurnError(RuntimeError):
    """``code``: no_ffmpeg | no_video | failed (``detail`` has ffmpeg's last words)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


class BurnCancelled(Exception):
    pass


@dataclass
class SubStyle:
    """Readable defaults: white text, dark outline, bottom-centre."""
    families: tuple[str, ...] = tuple(_FONT_FAMILIES)
    size_frac: float = 0.052          # font pixel size / shorter side of the picture
    color: str = "#FFFFFF"
    outline: str = "#000000"
    outline_frac: float = 0.13        # outline width / font size
    margin_frac: float = 0.06         # bottom margin / picture height
    max_lines: int = 3


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool
    audio_codec: str = ""


@dataclass
class BurnResult:
    path: str
    seconds: float
    size: int
    cues: int


# ── probing ──────────────────────────────────────────────────────────────

_VIDEO_RE = re.compile(r"Stream #\d+:\d+[^:]*: Video: (\w+)(.*)")
_SIZE_RE = re.compile(r"[ ,](\d{2,5})x(\d{2,5})[ ,\[]")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+[^:]*: Audio: (\w+)")
_DUR_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_ROT_RE = re.compile(r"rotation of (-?\d+(?:\.\d+)?) degrees")


def parse_probe(text: str) -> Optional[VideoInfo]:
    """Video facts from ``ffmpeg -i <file>`` output (ffprobe isn't always there)."""
    w = h = 0
    for m in _VIDEO_RE.finditer(text):
        if "attached pic" in m.group(2):
            continue                                   # cover art in an audio file
        sm = _SIZE_RE.search(m.group(2) + " ")
        if sm:
            w, h = int(sm.group(1)), int(sm.group(2))
            break
    if not w or not h:
        return None
    rm = _ROT_RE.search(text)
    if rm and int(round(abs(float(rm.group(1))))) % 180 == 90:
        w, h = h, w                                   # phone video shot upright
    dm = _DUR_RE.search(text)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)) if dm else 0.0
    am = _AUDIO_RE.search(text)
    return VideoInfo(w, h, dur, am is not None, am.group(1) if am else "")


def probe(ffmpeg: str, path: str) -> Optional[VideoInfo]:
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", path],
                       capture_output=True, text=True, timeout=60)
    return parse_probe(r.stderr)


def is_video(path: str) -> bool:
    """Cheap check by extension (the UI uses it to offer burning at all)."""
    return Path(path).suffix.lower() in VIDEO_EXTS


def default_output(src: str) -> str:
    p = Path(src)
    ext = p.suffix.lower() if p.suffix.lower() in _KEEP_CONTAINER else ".mp4"
    return str(p.with_name(f"{p.stem} (subtitled){ext}"))


_ENCODERS: dict[str, str] = {}


def pick_encoder(ffmpeg: str) -> str:
    """libx264 (best size/quality), else Apple's hardware H.264 encoder."""
    if ffmpeg not in _ENCODERS:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                 text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        names = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 1}
        _ENCODERS[ffmpeg] = next((e for e in ("libx264", "h264_videotoolbox") if e in names), "mpeg4")
    return _ENCODERS[ffmpeg]


# ── rendering cue images ─────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]|[^\s\u3000-\u9fff\uff00-\uffef]+|\s+")


def wrap_text(text: str, width_of: Callable[[str], float], max_width: float) -> list[str]:
    """Greedy line wrap: between words for Latin text, between characters for CJK."""
    lines: list[str] = []
    cur = ""
    for tok in _TOKEN_RE.findall(text):
        cand = cur + tok
        if cur and not tok.isspace() and width_of(cand.rstrip()) > max_width:
            lines.append(cur.rstrip())
            cur = tok
        else:
            cur = cand if (cur or not tok.isspace()) else ""
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


class CueRenderer:
    """Draws cues as transparent bands the width of the video."""

    def __init__(self, width: int, height: int, style: Optional[SubStyle] = None) -> None:
        from PySide6.QtGui import QFont, QFontMetricsF
        self.style = style or SubStyle()
        self.w, self.h = width, height
        px = max(14, int(round(min(width, height) * self.style.size_frac)))
        f = QFont()
        f.setFamilies(list(self.style.families))
        f.setPixelSize(px)
        f.setWeight(QFont.Weight.DemiBold)
        self.font = f
        self.metrics = QFontMetricsF(f)
        self.px = px
        self.stroke = max(2.0, px * self.style.outline_frac)
        self.line_h = self.metrics.lineSpacing()
        self.margin = int(round(height * self.style.margin_frac))
        self.band_h = int(self.margin + self.style.max_lines * self.line_h + 2 * self.stroke + 4)
        self.band_h += self.band_h % 2                 # yuv420 likes even sizes

    def lines(self, text: str) -> list[str]:
        out = wrap_text(text.replace("\n", " "), self.metrics.horizontalAdvance, self.w * 0.9)
        if len(out) > self.style.max_lines:
            out = out[: self.style.max_lines]
            out[-1] = self.metrics.elidedText(out[-1] + "…", _elide_right(), self.w * 0.9)
        return out

    def render(self, text: str, path: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
        img = QImage(self.w, self.band_h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        lines = self.lines(text) if text else []
        if lines:
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path_ = QPainterPath()
            base = self.band_h - self.margin - self.metrics.descent() - self.stroke
            for i, line in enumerate(lines):
                y = base - (len(lines) - 1 - i) * self.line_h
                path_.addText((self.w - self.metrics.horizontalAdvance(line)) / 2, y, self.font, line)
            edge = QColor(self.style.outline)
            edge.setAlpha(235)
            p.setPen(QPen(edge, self.stroke * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path_)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.style.color))
            p.drawPath(path_)
            p.end()
        if not img.save(path, "PNG"):
            raise BurnError("failed", f"couldn't write {path}")


def _elide_right():
    from PySide6.QtCore import Qt
    return Qt.TextElideMode.ElideRight


def concat_list(cues: list[Segment], images: list[str], blank: str, duration: float) -> str:
    """An ffconcat script showing each cue image for its cue's time and the
    blank image in between. ``images[i]`` belongs to ``cues[i]``."""
    rows = ["ffconcat version 1.0"]
    t = 0.0

    def hold(name: str, secs: float) -> None:
        if secs >= 0.001:
            rows.append(f"file '{name}'")
            rows.append(f"duration {secs:.3f}")

    for cue, img in zip(cues, images):
        start = max(cue.start, t)
        end = min(max(cue.end, start + 0.3), duration) if duration > 0 else max(cue.end, start + 0.3)
        if end <= start:
            continue
        hold(blank, start - t)
        hold(img, end - start)
        t = end
    hold(blank, max(duration - t, 0.04))
    rows.append(f"file '{blank}'")                    # the last entry's duration needs a follower
    return "\n".join(rows) + "\n"


# ── the ffmpeg command ───────────────────────────────────────────────────

def subtitle_language(tr: Transcript) -> str:
    text = "".join(s.text for s in tr.segments)[:2000]
    cjk = sum(1 for c in text if "\u3400" <= c <= "\u9fff")
    return "chi" if text and cjk > len(text) * 0.3 else ("eng" if text.isascii() else "und")


def build_command(ffmpeg: str, src: str, concat: str, out: str, info: VideoInfo, encoder: str,
                  soft_srt: str = "", language: str = "und") -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-i", src, "-f", "concat", "-safe", "0", "-i", concat]
    if soft_srt:
        cmd += ["-i", soft_srt]
    cmd += ["-filter_complex",
            "[0:v:0][1:v]overlay=x=(W-w)/2:y=H-h:eof_action=pass:format=auto,format=yuv420p[v]",
            "-map", "[v]"]
    if info.has_audio:
        cmd += ["-map", "0:a?"]
    if soft_srt:
        cmd += ["-map", "2:s", "-c:s", "mov_text", "-metadata:s:s:0", f"language={language}"]
    if encoder == "libx264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    elif encoder == "h264_videotoolbox":
        cmd += ["-c:v", "h264_videotoolbox", "-q:v", "62"]
    else:
        cmd += ["-c:v", encoder, "-q:v", "3"]
    if info.has_audio:
        keep = Path(src).suffix.lower() in _KEEP_CONTAINER and info.audio_codec in ("aac", "mp3", "alac", "ac3")
        cmd += ["-c:a", "copy"] if keep else ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out]
    return cmd


def parse_progress_time(line: str) -> Optional[float]:
    """Seconds written so far from an ``-progress`` line, if it carries one."""
    key, _, val = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms") and val.strip().lstrip("-").isdigit():
        return max(0, int(val)) / 1_000_000          # both are microseconds, despite the name
    return None


# ── run ──────────────────────────────────────────────────────────────────

def burn(
    src: str,
    tr: Transcript,
    out: str,
    soft: bool = False,
    style: Optional[SubStyle] = None,
    progress: Optional[ProgressCB] = None,
    cancel: Optional[threading.Event] = None,
    ffmpeg: Optional[str] = None,
) -> BurnResult:
    """Write ``out``: ``src`` with ``tr`` drawn on screen (and, with ``soft``,
    also as a subtitle track). The partial file is removed on failure/cancel."""
    t0 = time.monotonic()
    ffmpeg = ffmpeg or audio_io.find_ffmpeg()
    if not ffmpeg:
        raise BurnError("no_ffmpeg")

    def _p(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    def _cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    _p(-1, "probe")
    info = probe(ffmpeg, src)
    if info is None:
        raise BurnError("no_video")
    cues = subtitle_cues(tr)
    work = tempfile.mkdtemp(prefix="thundertalk-burn-")
    proc: Optional[subprocess.Popen] = None
    done = False
    try:
        _p(0, "render")
        r = CueRenderer(info.width, info.height, style)
        r.render("", os.path.join(work, "blank.png"))
        images = []
        for i, c in enumerate(cues):
            if _cancelled():
                raise BurnCancelled()
            name = f"c{i:05d}.png"
            r.render(c.text, os.path.join(work, name))
            images.append(name)
        concat = os.path.join(work, "cues.ffconcat")
        Path(concat).write_text(concat_list(cues, images, "blank.png", info.duration), encoding="utf-8")
        srt = ""
        if soft:
            srt = os.path.join(work, "subs.srt")
            Path(srt).write_text(cues_to_srt(cues), encoding="utf-8")
        cmd = build_command(ffmpeg, src, concat, out, info, pick_encoder(ffmpeg), srt, subtitle_language(tr))
        err_path = os.path.join(work, "ffmpeg.log")
        with open(err_path, "w") as err:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, cwd=work)
            assert proc.stdout is not None
            _p(0, "encode")
            for line in proc.stdout:
                if _cancelled():
                    proc.terminate()
                    proc.wait(10)
                    raise BurnCancelled()
                secs = parse_progress_time(line)
                if secs is not None and info.duration > 0:
                    _p(min(99, int(secs * 100 / info.duration)), "encode")
            rc = proc.wait()
        if _cancelled():
            raise BurnCancelled()
        if rc != 0 or not os.path.isfile(out):
            tail = Path(err_path).read_text(errors="replace").strip().splitlines()[-3:]
            raise BurnError("failed", " ".join(tail)[-300:] or f"ffmpeg exited with {rc}")
        _p(100, "done")
        done = True
        return BurnResult(out, time.monotonic() - t0, os.path.getsize(out), len(cues))
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if not done and proc is not None:
            try:
                os.unlink(out)
            except OSError:
                pass
        shutil.rmtree(work, ignore_errors=True)
