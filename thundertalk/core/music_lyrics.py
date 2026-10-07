"""Lyric check for composed takes: which lyric lines were sung clearly.

A take is recognised line by line (from the sentence timestamps the worker
saved) or, without timestamps, in overlapping windows whose text is spread over
the lines by global alignment. Chinese is compared on tone-less pinyin so a
homophone (宋 / 颂, or "song" heard for 宋) counts as sung correctly. The result
is advice: singing over a full mix is hard for a speech recogniser.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Sequence

from thundertalk.core import tts_verify

SECTION_RE = re.compile(r"^\s*\[([^\]\n]+)\]\s*$")
PASS_ERROR = 0.35
PAD_S = 0.4
WINDOW_S = 15.0
OVERLAP_S = 2.0

_PINYIN_LANGS = {"zh", "yue"}
_CHAR_LANGS = {"ja": "japanese", "ko": "korean"}
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

CLEAR, UNCLEAR, NOT_HEARD = "clear", "unclear", "not_heard"


@dataclass(frozen=True)
class LyricLine:
    index: int        # position among the sung lines (section tags and blank lines skipped)
    text: str
    section: str      # the [tag] above it, lower-cased; "" before the first tag


@dataclass(frozen=True)
class LineCheck:
    index: int
    heard: str
    status: str       # CLEAR | UNCLEAR | NOT_HEARD
    error: Optional[float]


def lyric_lines(lyrics: str) -> list[LyricLine]:
    out, section = [], ""
    for raw in lyrics.splitlines():
        if m := SECTION_RE.match(raw):
            section = m.group(1).strip().lower()
        elif raw.strip():
            out.append(LyricLine(len(out), raw.strip(), section))
    return out


def _pinyin(chars: str) -> list[str]:
    try:
        from pypinyin import lazy_pinyin
    except ImportError:             # scoring still works, homophones just count as errors
        return list(chars)
    return lazy_pinyin(chars)


def units(text: str, lang: str) -> list[str]:
    """Comparable units: tone-less pinyin syllables (and Latin words) for
    Chinese, characters for Japanese / Korean, words otherwise."""
    if lang in _PINYIN_LANGS:
        s = re.sub(r"[^\w\s]|_", " ", unicodedata.normalize("NFKC", text).lower())
        out: list[str] = []
        for run in re.findall(r"[\u3400-\u9fff\uf900-\ufaff]+|[a-z]+", s):
            out.extend(_pinyin(run) if _CJK.match(run) else [run])
        return out
    return tts_verify.tokens(text, _CHAR_LANGS.get(lang, "english"))


def error_rate(heard: Sequence[str], target: Sequence[str]) -> float:
    if not target:
        return 0.0
    return tts_verify.edit_distance(list(heard), list(target)) / len(target)


def check_line(index: int, heard: str, targets: Sequence[str], lang: str) -> LineCheck:
    """A line passes if it is close enough to any of `targets` (the sung text
    and the shown text)."""
    return judge(index, heard, units(heard, lang), [units(t, lang) for t in targets if t])


def judge(index: int, heard: str, got: Sequence[str], targets: Sequence[Sequence[str]]) -> LineCheck:
    if not got:
        return LineCheck(index, heard, NOT_HEARD, None)
    err = min((error_rate(got, t) for t in targets), default=0.0)
    return LineCheck(index, heard, CLEAR if err <= PASS_ERROR else UNCLEAR, round(err, 3))


def line_windows(spans: Sequence[tuple[float, float]], duration: float,
                 pad: float = PAD_S) -> list[tuple[float, float]]:
    """Sentence timestamps padded on both sides and clamped to the take."""
    return [(max(0.0, a - pad), min(duration, b + pad)) for a, b in spans]


def scan_windows(duration: float, size: float = WINDOW_S,
                 overlap: float = OVERLAP_S) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    out, start, step = [], 0.0, size - overlap
    while True:
        end = min(duration, start + size)
        out.append((start, end))
        if end >= duration:
            return out
        start += step


def assign(heard: Sequence[str], lines: Sequence[Sequence[str]]) -> list[list[str]]:
    """Spread recognised units over the lyric lines by global alignment
    against the concatenated lines (fallback when there are no timestamps)."""
    owner = [i for i, line in enumerate(lines) for _ in line]
    out: list[list[str]] = [[] for _ in lines]
    if not owner:
        return out
    target = [u for line in lines for u in line]
    sm = difflib.SequenceMatcher(None, target, list(heard), autojunk=False)
    for _tag, i1, i2, j1, j2 in sm.get_opcodes():
        if j1 == j2:
            continue
        for k, j in enumerate(range(j1, j2)):
            # inserted units go to the line before the gap; replaced ones spread evenly
            pos = i1 + (k * (i2 - i1)) // (j2 - j1) if i2 > i1 else max(0, i1 - 1)
            out[owner[min(pos, len(owner) - 1)]].append(heard[j])
    return out


def overlap_merge(texts: Sequence[str], lang: str) -> list[str]:
    """Units of consecutive window transcripts with the repeated overlap
    removed (longest matching tail/head of up to 12 units)."""
    merged: list[str] = []
    for text in texts:
        cur = units(text, lang)
        best = 0
        for n in range(min(12, len(merged), len(cur)), 0, -1):
            if merged[-n:] == cur[:n]:
                best = n
                break
        merged.extend(cur[best:])
    return merged


def summary(checks: Sequence[LineCheck]) -> tuple[int, int]:
    """(clear lines, total lines) for the take badge."""
    return sum(c.status == CLEAR for c in checks), len(checks)
