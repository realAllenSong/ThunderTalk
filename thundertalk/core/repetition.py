"""Repetition-loop detection for recognizer output (pure, no model imports).

An autoregressive recognizer (Qwen3-ASR, MOSS) sometimes falls into a loop on
long or noisy audio and repeats one phrase until its token budget runs out:
"come on，teacher，hands up，hands up，hands up，…" hundreds of times for a
chant that was said eight times. Real speech repeats too ("好的好的", "no no
no", a song chorus), so a repeated phrase alone is never proof of a loop:

  * ``looks_degenerate`` is the cheap, liberal check — a phrase repeated
    several times in a row, or more speech than the audio could hold. It only
    triggers a second look (re-decoding shorter pieces), never a cut.
  * ``is_implausible`` is the strict check — more syllables than anyone can say
    in the audio's duration. Only then does ``clean`` cut text, collapsing the
    offending runs to their first ``keep`` repetitions.

Text is measured in "units": a Latin word or number, or a single CJK
character (Han, kana, Hangul). Punctuation and spaces are ignored, so
"hands up，hands up" and "Hands up! Hands up!" are the same 2-unit phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RX_UNIT = re.compile(
    r"[A-Za-z0-9\u00C0-\u024F]+(?:['’][A-Za-z]+)*"     # Latin word / number
    r"|[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uAC00-\uD7AF]"  # one CJK char
)
_RX_VOWELS = re.compile(r"[aeiouyà-ÿ]+")

MAX_PERIOD = 40            # longest repeated phrase looked for, in units (a sentence)
# Sustained fast speech is ~7-8 syllables (or morae) per second in Mandarin,
# Japanese and English; recognizer output above this, measured against the
# whole clip including its pauses, was not spoken.
MAX_SYLLABLES_PER_S = 10.0
MIN_SYLLABLES = 16         # too little text to judge a rate on
SUSPECT_REPEATS = 4        # a phrase this many times in a row deserves a second look…
SUSPECT_UNITS = 8          # …if the run is at least this long ("好的好的好的好的")
SUSPECT_REPEATS_SHORT = 8  # single-unit runs ("哈哈哈哈") need more


@dataclass(frozen=True)
class Unit:
    text: str              # lower-cased
    start: int             # character offsets into the source text
    end: int


@dataclass(frozen=True)
class Run:
    """``count`` consecutive repetitions of a ``period``-unit phrase."""
    start: int             # char offset of the first repetition
    end: int               # char offset just past the last (possibly partial) one
    period: int
    count: int
    phrase: str
    rep_ends: tuple[int, ...]    # char offset just past each full repetition

    @property
    def units(self) -> int:
        return self.period * self.count


def units(text: str) -> list[Unit]:
    return [Unit(m.group().lower(), m.start(), m.end()) for m in _RX_UNIT.finditer(text)]


def _syllables(u: str) -> int:
    if len(u) == 1 and not u.isascii():
        return 1                                  # one CJK character
    if u.isdigit():
        return max(1, len(u))
    return max(1, len(_RX_VOWELS.findall(u)))


def syllables(text: str) -> int:
    """Rough spoken length: CJK characters count one each, Latin words by
    vowel groups ("teacher" = 2), numbers per digit."""
    return sum(_syllables(u.text) for u in units(text))


def find_runs(text: str, min_count: int = 3, max_period: int = MAX_PERIOD) -> list[Run]:
    """Non-overlapping runs of a phrase repeated at least ``min_count`` times
    back to back, longest first. Each run is reported at its shortest period
    ("好好好好" is "好" × 4, not "好好" × 2)."""
    us = units(text)
    toks = [u.text for u in us]
    n = len(toks)
    cands: list[tuple[int, int, int]] = []        # (start unit, covered units, period)
    for p in range(1, min(max_period, n // 2) + 1):
        i = 0
        while i + p < n:
            if toks[i] != toks[i + p]:
                i += 1
                continue
            j = i
            while j + p < n and toks[j] == toks[j + p]:
                j += 1
            covered = (j - i) + p                 # units i .. j+p-1 follow the period
            if covered // p >= min_count:
                cands.append((i, covered, p))
            i = j + 1
    cands.sort(key=lambda c: (-c[1], c[2], c[0]))
    taken = [False] * n
    runs: list[Run] = []
    for i, covered, p in cands:
        if any(taken[i:i + covered]):
            continue
        phrase = toks[i:i + p]
        for k in range(i, i + covered):
            taken[k] = True
        count = covered // p
        rep_ends = tuple(us[i + (r + 1) * p - 1].end for r in range(count))
        runs.append(Run(us[i].start, us[i + covered - 1].end, p, count, " ".join(phrase), rep_ends))
    runs.sort(key=lambda r: r.start)
    return runs


def rate(text: str, seconds: float) -> float:
    """Syllables per second of audio."""
    return syllables(text) / seconds if seconds > 0 else float("inf")


def is_implausible(text: str, seconds: float) -> bool:
    """More speech than ``seconds`` of audio can hold."""
    syl = syllables(text)
    return syl >= MIN_SYLLABLES and syl > MAX_SYLLABLES_PER_S * max(seconds, 0.0)


def has_loop(text: str) -> bool:
    """A phrase repeated often enough in a row to be worth a second look."""
    for r in find_runs(text, min_count=SUSPECT_REPEATS):
        if r.period == 1 and r.count < SUSPECT_REPEATS_SHORT:
            continue
        if r.units >= SUSPECT_UNITS:
            return True
    return False


def looks_degenerate(text: str, seconds: float) -> bool:
    """Liberal check: a repeated run or implausible length. Legit speech can
    pass this — callers re-decode, they don't cut on it."""
    return is_implausible(text, seconds) or has_loop(text)


def collapse(text: str, run: Run, keep: int = 2) -> str:
    """``text`` with ``run`` cut back to its first ``keep`` repetitions."""
    if run.count <= keep:
        return text
    cut = run.rep_ends[keep - 1] if keep > 0 else run.start
    return (text[:cut] + text[run.end:]).strip()


def clean(text: str, seconds: float, keep: int = 2) -> tuple[str, bool]:
    """Cut repetition loops out of an implausibly long result.

    Plausible text is returned untouched, however repetitive. Otherwise the
    longest repeated runs are collapsed (largest first) until the text fits
    the audio or no run of 3+ is left. Returns (text, changed)."""
    if not is_implausible(text, seconds):
        return text, False
    out = text
    while is_implausible(out, seconds):
        runs = find_runs(out, min_count=keep + 1)
        if not runs:
            break
        worst = max(runs, key=lambda r: (r.units, r.count))
        out = collapse(out, worst, keep)
    return out, out != text
