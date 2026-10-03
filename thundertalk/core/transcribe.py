"""File transcription: fast single-speaker, or multi-speaker with MOSS.

  * decoding uses macOS `afconvert` (see audio_io) — no ffmpeg required;
  * fast path: the audio is cut at natural pauses into 10–30 s utterances and
    each is recognised by the active dictation model, which also gives
    sentence-level timestamps for free;
  * speaker path: MOSS-Transcribe-Diarize returns speaker turns with
    timestamps in a single pass (up to ~90 minutes);
  * web links (YouTube, Bilibili, …) are fetched audio-only via links.py;
  * results export to TXT / Markdown / SRT / VTT / JSON.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from thundertalk.core import audio_io
from thundertalk.core.gpu_lock import GPU_LOCK

SR = 16000
ProgressCB = Callable[[int, str], None]


class TranscribeCancelled(Exception):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


@dataclass
class Transcript:
    segments: list[Segment]
    duration: float
    engine: str
    seconds_taken: float = 0.0
    has_speakers: bool = False
    speaker_names: dict[str, str] = field(default_factory=dict)
    title: str = ""                       # e.g. the video title for a link
    source_url: str = ""
    expected_duration: float = 0.0        # length the site advertised for a link
    notes: str = ""                       # optional AI meeting notes (Markdown)

    # -- derived -------------------------------------------------------
    @property
    def is_partial(self) -> bool:
        """The site served noticeably less than the advertised length (e.g. a
        30 s preview of a members-only Bilibili video)."""
        return self.expected_duration > 0 and self.duration < 0.8 * self.expected_duration - 1.0

    @property
    def realtime_factor(self) -> float:
        """How many times faster than real time (e.g. 18.0 = 18×)."""
        return self.duration / self.seconds_taken if self.seconds_taken > 0 else 0.0

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for s in self.segments:
            if s.speaker and s.speaker not in seen:
                seen.append(s.speaker)
        return seen

    def label(self, speaker: str) -> str:
        return self.speaker_names.get(speaker) or speaker

    def rename_speaker(self, speaker: str, name: str) -> None:
        if name.strip():
            self.speaker_names[speaker] = name.strip()
        else:
            self.speaker_names.pop(speaker, None)

    def turns(self) -> list[Segment]:
        """Consecutive segments by the same speaker merged into one turn."""
        out: list[Segment] = []
        for s in self.segments:
            if out and out[-1].speaker == s.speaker:
                sep = "" if _ends_cjk(out[-1].text) or _starts_cjk(s.text) else " "
                out[-1] = Segment(out[-1].start, s.end, out[-1].text + sep + s.text, s.speaker)
            else:
                out.append(Segment(s.start, s.end, s.text, s.speaker))
        return out

    # -- exports -------------------------------------------------------
    def to_text(self, timestamps: bool = False) -> str:
        if self.has_speakers:
            lines = []
            for t in self.turns():
                head = f"{self.label(t.speaker)}" if t.speaker else ""
                if timestamps:
                    head = f"[{fmt_time(t.start)}] {head}".strip()
                lines.append(f"{head}: {t.text}" if head else t.text)
            return "\n\n".join(lines)
        if timestamps:
            return "\n".join(f"[{fmt_time(s.start)}] {s.text}" for s in self.segments)
        out = ""
        for s in self.segments:
            sep = "" if not out or _ends_cjk(out) or _starts_cjk(s.text) else " "
            out += sep + s.text
        return out

    def to_markdown(self, title: str = "") -> str:
        title = title or self.title or "Transcript"
        meta = f"*{fmt_time(self.duration)} · {self.engine}*"
        if self.source_url:
            meta += f"  \n<{self.source_url}>"
        if self.has_speakers:
            body = "\n\n".join(f"**{self.label(t.speaker)}** ({fmt_time(t.start)})  \n{t.text}" for t in self.turns())
        else:
            body = "\n\n".join(f"`{fmt_time(s.start)}` {s.text}" for s in self.segments)
        notes = f"\n\n---\n\n{self.notes.strip()}" if self.notes else ""
        return f"# {title}\n\n{meta}\n\n{body}{notes}\n"

    def to_srt(self) -> str:
        blocks = []
        for i, s in enumerate(self.segments, 1):
            text = f"{self.label(s.speaker)}: {s.text}" if s.speaker else s.text
            blocks.append(f"{i}\n{srt_time(s.start)} --> {srt_time(max(s.end, s.start + 0.5))}\n{text}\n")
        return "\n".join(blocks)

    def to_vtt(self) -> str:
        blocks = ["WEBVTT\n"]
        for s in self.segments:
            text = f"<v {self.label(s.speaker)}>{s.text}" if s.speaker else s.text
            blocks.append(f"{srt_time(s.start, '.')} --> {srt_time(max(s.end, s.start + 0.5), '.')}\n{text}\n")
        return "\n".join(blocks)

    def to_json(self) -> str:
        head = {k: v for k, v in (("title", self.title), ("source_url", self.source_url)) if v}
        return json.dumps({
            **head,
            "duration": round(self.duration, 3), "engine": self.engine, "speakers": self.speakers,
            "speaker_names": self.speaker_names,
            "segments": [{"start": round(s.start, 3), "end": round(s.end, 3), "speaker": s.speaker, "text": s.text}
                         for s in self.segments],
        }, ensure_ascii=False, indent=2)

    def export(self, fmt: str) -> str:
        return {"txt": self.to_text, "md": self.to_markdown, "srt": self.to_srt,
                "vtt": self.to_vtt, "json": self.to_json}[fmt]()


def _ends_cjk(s: str) -> bool:
    return bool(s) and "　" <= s[-1] <= "鿿"


def _starts_cjk(s: str) -> bool:
    return bool(s) and "　" <= s[0] <= "鿿"


def fmt_time(secs: float) -> str:
    secs = max(0.0, secs)
    h, m, s = int(secs // 3600), int(secs % 3600 // 60), int(secs % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def srt_time(secs: float, sep: str = ",") -> str:
    secs = max(0.0, secs)
    ms = int(round((secs - int(secs)) * 1000))
    if ms == 1000:
        secs, ms = secs + 1, 0
    h, m, s = int(secs // 3600), int(secs % 3600 // 60), int(secs % 60)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


# ── on-screen subtitle cues ──────────────────────────────────────────────

_CLAUSE_END = "。！？；!?;，,、：:"


def _cjk_heavy(text: str) -> bool:
    return sum(_ends_cjk(c) for c in text) > len(text) * 0.3


def _split_long(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` characters: at clause
    punctuation first, then at spaces, then hard (CJK has no spaces)."""
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _CLAUSE_END:
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)
    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            target = len(piece) // -(-len(piece) // limit)       # balanced, not 84 + 6
            spaces = [i for i, c in enumerate(piece[: limit + 1]) if c == " " and i > target // 2]
            cut = min(spaces, key=lambda i: abs(i - target)) if spaces else target
            out.append(piece[:cut])
            piece = piece[cut:].lstrip()
        if piece:
            out.append(piece)
    lines: list[str] = []
    for piece in out:                              # greedily re-pack short clauses
        if lines and len(lines[-1]) + len(piece) <= limit:
            lines[-1] += piece
        else:
            lines.append(piece)
    return [s.strip() for s in lines if s.strip()]


def subtitle_cues(tr: Transcript, cjk_chars: int = 32, latin_chars: int = 84) -> list[Segment]:
    """Segments cut to subtitle size (about two lines on screen). Fast-mode
    segments run 10–30 s, far too long to read as one caption, so each is split
    at clause boundaries and its time shared out by character count. A speaker
    label is shown only when the speaker changes."""
    cues: list[Segment] = []
    last_speaker = ""
    for s in tr.segments:
        text = s.text.strip()
        if not text:
            continue
        limit = cjk_chars if _cjk_heavy(text) else latin_chars
        parts = _split_long(text, limit)
        total = sum(len(p) for p in parts) or 1
        span = max(s.end - s.start, 0.5 * len(parts))
        t = s.start
        for i, p in enumerate(parts):
            dur = span * len(p) / total
            label = ""
            if i == 0 and s.speaker and s.speaker != last_speaker:
                label = f"{tr.label(s.speaker)}: "
            cues.append(Segment(t, t + dur, label + p, s.speaker))
            t += dur
        if s.speaker:
            last_speaker = s.speaker
    return cues


def cues_to_srt(cues: list[Segment]) -> str:
    return "\n".join(f"{i}\n{srt_time(c.start)} --> {srt_time(max(c.end, c.start + 0.3))}\n{c.text}\n"
                     for i, c in enumerate(cues, 1))


# ── segmentation at natural pauses ───────────────────────────────────────

def segment_speech(x: np.ndarray, sr: int = SR, target: float = 15.0, max_len: float = 30.0,
                   min_gap: float = 0.30) -> list[tuple[float, float]]:
    """Cut audio into utterances at pauses. Returns (start_s, end_s) spans.

    The silence threshold adapts to the recording (noise floor × 4) so quiet
    phone memos and loud studio audio are both handled. Long stretches with no
    pause are cut at their quietest moment instead of at a hard boundary."""
    n_total = len(x) / sr
    if n_total <= max_len:
        return [(0.0, n_total)] if _has_speech(x) else []
    frame = int(0.02 * sr)
    n = len(x) // frame
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    floor = float(np.percentile(rms, 10))
    # noise floor × 4, but never above a fraction of the loud passages: in a
    # recording that is nearly all speech the 10th percentile *is* speech.
    thr = max(min(floor * 4.0, 0.2 * float(np.percentile(rms, 95))), 0.004)
    silent = rms < thr
    gaps: list[tuple[int, int]] = []          # (start_frame, end_frame) of pauses >= min_gap
    i = 0
    need = int(min_gap / 0.02)
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            if j - i >= need:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    cuts_s = [((a + b) / 2) * 0.02 for a, b in gaps]

    spans: list[tuple[float, float]] = []

    def quiet_cut(start: float) -> float:
        """Quietest moment in the last 8 s before the length limit."""
        lo = max(int((start + 4.0) / 0.02), int((start + max_len - 8.0) / 0.02))
        hi = max(lo + 1, min(n, int((start + max_len) / 0.02)))
        return (lo + int(np.argmin(rms[lo:hi]))) * 0.02

    start = 0.0
    last_ok: Optional[float] = None
    for c in cuts_s:
        while c - start > max_len:                    # next pause is too far away
            cut = last_ok if last_ok and last_ok - start > 4.0 else quiet_cut(start)
            spans.append((start, cut))
            start, last_ok = cut, None
        if c - start >= target:
            spans.append((start, c))
            start, last_ok = c, None
        elif c - start > 1.0:
            last_ok = c
    while n_total - start > max_len:
        cut = quiet_cut(start)
        spans.append((start, cut))
        start = cut
    spans.append((start, n_total))
    # drop spans that are only silence/noise (they make recognisers hallucinate)
    out = []
    for a, b in spans:
        seg = x[int(a * sr): int(b * sr)]
        if _has_speech(seg):
            out.append((a, b))
    return out


def _has_speech(x: np.ndarray, sr: int = SR) -> bool:
    if len(x) < int(0.15 * sr):
        return False
    frame = int(0.02 * sr)
    n = len(x) // frame
    if n == 0:
        return False
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    lo, hi = float(np.percentile(rms, 10)), float(np.percentile(rms, 95))
    if hi < 0.006:
        return False                       # too quiet to be speech
    if hi < 2.0 * lo:
        return False                       # steady level: hum / fan / hiss, not speech
    thr = max(0.006, min(lo * 4.0, 0.2 * hi))
    return bool(np.sum(rms > thr) >= 5)


# ── transcription ────────────────────────────────────────────────────────

def transcribe_file(
    path: str,
    engine,                                   # AsrEngine (active dictation model)
    speakers: bool = False,
    progress: Optional[ProgressCB] = None,
    cancel: Optional[threading.Event] = None,
) -> Transcript:
    """Transcribe an audio/video file.

    ``speakers=True`` uses MOSS-Transcribe-Diarize (speaker labels + timestamps);
    otherwise the active dictation model is used, segment by segment."""
    t0 = time.monotonic()

    def _check() -> None:
        if cancel is not None and cancel.is_set():
            raise TranscribeCancelled()

    def _p(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    _p(3, "decode")
    x = audio_io.decode_audio(path, SR)
    duration = len(x) / SR
    _check()

    if speakers:
        from thundertalk.core import diarize
        _p(10, "load_moss")
        diarize.load_model()
        _check()
        _p(25, "diarize")
        with GPU_LOCK:
            segs = diarize.transcribe(x)
        _check()
        if not segs:
            raise RuntimeError("no_speech")
        out = [Segment(s.start, s.end, s.text, s.speaker) for s in segs]
        _p(100, "done")
        return Transcript(out, duration, "MOSS-Transcribe-Diarize", time.monotonic() - t0,
                          has_speakers=any(s.speaker for s in out))

    if engine is None or not getattr(engine, "is_loaded", False):
        raise RuntimeError("no_model")
    spans = segment_speech(x)
    if not spans:
        raise RuntimeError("no_speech")
    segs: list[Segment] = []
    total = len(spans)
    for i, (a, b) in enumerate(spans):
        _check()
        _p(8 + int(90 * i / total), f"{i + 1}/{total}")
        pad = int(0.15 * SR)
        seg = x[max(0, int(a * SR) - pad): min(len(x), int(b * SR) + pad)]
        with GPU_LOCK:
            r = engine.recognize(seg, SR)
        text = (r.text or "").strip()
        if text:
            segs.append(Segment(a, b, text))
    if not segs:
        raise RuntimeError("no_speech")
    _p(100, "done")
    return Transcript(segs, duration, getattr(engine, "current_model", "") or "ASR", time.monotonic() - t0)


def transcribe_link(
    url: str,
    engine,
    speakers: bool = False,
    progress: Optional[ProgressCB] = None,
    cancel: Optional[threading.Event] = None,
    on_title: Optional[Callable[[str], None]] = None,
) -> Transcript:
    """Download the audio of a web link, transcribe it, delete the download.

    Progress messages: "fetch", then "download:<bytes done>:<bytes total>",
    then the same ones as ``transcribe_file``."""
    from thundertalk.core import links

    if progress:
        progress(-1, "fetch")

    def dl(pct: int, done: int, total: int) -> None:
        if progress:
            progress(pct, f"download:{done}:{total}")

    try:
        got = links.fetch_audio(url, dl, cancel, on_title)
    except links.LinkCancelled:
        raise TranscribeCancelled() from None
    try:
        tr = transcribe_file(got.path, engine, speakers=speakers, progress=progress, cancel=cancel)
    finally:
        shutil.rmtree(got.workdir, ignore_errors=True)
    tr.title, tr.source_url, tr.expected_duration = got.title, url, got.duration
    return tr


# ── saving ───────────────────────────────────────────────────────────────

EXPORT_EXTS = {"txt": ".txt", "md": ".md", "srt": ".srt", "vtt": ".vtt", "json": ".json"}


def unique_path(path: Path) -> Path:
    """``path``, or "name (2).ext", "name (3).ext"… if it already exists."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path


def save_outputs(tr: Transcript, folder: str, stem: str, formats: list[str]) -> list[str]:
    """Write ``tr`` as each format into ``folder``; never overwrites a file."""
    out: list[str] = []
    Path(folder).mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        p = unique_path(Path(folder) / f"{stem}{EXPORT_EXTS[fmt]}")
        p.write_text(tr.export(fmt), encoding="utf-8")
        out.append(str(p))
    return out
