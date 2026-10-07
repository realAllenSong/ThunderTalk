"""Lyric check for one finished take: read it back with a recogniser and
score every sung line (advisory, see docs/plans/2026-10-06-music-studio.md).

The recogniser is any callable taking 16 kHz mono float32 audio and returning
text, so the scoring runs with fakes in tests and with the app's Qwen3-ASR
engine in the Studio worker. Call it only after the music job has exited.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from thundertalk.core import audio_io, music_lyrics
from thundertalk.core.music_lyrics import LineCheck, LyricLine

ASR_RATE = 16000

Recognise = Callable[[np.ndarray], str]


@dataclass(frozen=True)
class TakeCheck:
    lines: list[LyricLine]          # the shown lines, in order
    checks: list[LineCheck]
    windows: list[tuple[float, float]]   # per line when timed, else the scan windows
    timed: bool                      # True when the take's sentence timestamps were used
    line_ranges: list[Optional[tuple[float, float]]] = field(default_factory=list)

    @property
    def summary(self) -> tuple[int, int]:
        return music_lyrics.summary(self.checks)


def sidecar_path(wav: Path) -> Path:
    return Path(wav).with_suffix(".json")


def load_take(wav: Path) -> tuple[np.ndarray, float]:
    """16 kHz mono audio of a take and its length in seconds."""
    x, sr = audio_io.read_wav(str(wav))
    if x.ndim > 1:
        x = x.mean(axis=1)
    return audio_io.resample(x.astype(np.float32), sr, ASR_RATE), len(x) / sr


def _clip(audio: np.ndarray, window: tuple[float, float]) -> np.ndarray:
    a, b = window
    return audio[int(a * ASR_RATE):int(b * ASR_RATE)]


def check_take(wav: Path, recognise: Recognise, meta: Optional[dict] = None,
               between: Optional[Callable[[], None]] = None) -> Optional[TakeCheck]:
    """None for instrumental takes or takes without lyrics. `between` runs
    before every recogniser call (the worker waits for dictation there and
    raises to cancel)."""
    meta = meta if meta is not None else json.loads(sidecar_path(wav).read_text(encoding="utf-8"))
    if meta.get("instrumental"):
        return None
    lang = meta.get("vocal_language") or "en"
    shown = music_lyrics.lyric_lines(meta.get("shown_lyrics") or meta.get("lyrics") or "")
    sung = music_lyrics.lyric_lines(meta.get("lyrics") or "")
    if not shown:
        return None
    # The sung text is only a usable target when it still lines up with the shown one.
    sung_text = [s.text for s in sung] if len(sung) == len(shown) else [""] * len(shown)
    targets = [[t, line.text] for t, line in zip(sung_text, shown)]
    audio, duration = load_take(wav)

    def heard(window):
        if between:
            between()
        return (recognise(_clip(audio, window)) or "").strip()

    spans = music_lyrics.line_spans(meta.get("lyric_timestamps"), len(shown))
    if spans is not None:
        windows = music_lyrics.line_windows(spans, duration)
        checks = [music_lyrics.check_line(i, heard(w), targets[i], lang) for i, w in enumerate(windows)]
        return TakeCheck(shown, checks, windows, True, list(windows))

    windows = music_lyrics.scan_windows(duration)
    got, raw, provenance = [], [], []
    for w in windows:
        text = heard(w)
        cur = music_lyrics.units(text, lang)
        if lang in ("zh", "yue"):
            atoms = [a for run in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z]+", text)
                     for a in (list(run) if '\u3400' <= run[0] <= '\u9fff' else [run])]
        else:
            atoms = list(text.replace(" ", "")) if lang in ("ja", "ko") else re.findall(r"\w+", text)
        if len(atoms) != len(cur):
            atoms = cur
        overlap = next((n for n in range(min(12, len(got), len(cur)), 0, -1)
                        if got[-n:] == cur[:n]), 0)
        got.extend(cur[overlap:])
        raw.extend(atoms[overlap:])
        provenance.extend([w] * (len(cur) - overlap))
    target_units = [[music_lyrics.units(t, lang) for t in pair if t] for pair in targets]
    # Align against the sung text: that is what the model was asked to sing.
    per_line = music_lyrics.assign(got, [units[0] if units else [] for units in target_units])
    joiner = "" if lang in ("zh", "yue", "ja", "ko") else " "
    checks, ranges, offset = [], [], 0
    for i, u in enumerate(per_line):
        end = offset + len(u)
        checks.append(music_lyrics.judge(i, joiner.join(raw[offset:end]), u, target_units[i]))
        spans = provenance[offset:end]
        ranges.append((min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None)
        offset = end
    return TakeCheck(shown, checks, windows, False, ranges)
