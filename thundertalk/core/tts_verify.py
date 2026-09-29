"""Read a generated piece back with the local speech recogniser and score it
against the source text. Catches the failures duration checks cannot: a piece
of normal length in which a sentence was skipped, repeated or garbled."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

import numpy as np

from thundertalk.core import audio_io
from thundertalk.core.gpu_lock import GPU_LOCK

_CJK_LANGS = {"chinese", "japanese", "korean"}
_ZH_NUM = set("零〇一二三四五六七八九十百千万亿两点")
_EN_NUM = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
           "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
           "hundred", "thousand", "million", "billion", "and", "a"}


def tokens(text: str, lang: str) -> list[str]:
    """Comparable units: characters for CJK, words otherwise. Numbers are
    dropped from both sides (a recogniser may write "60" for "sixty")."""
    s = unicodedata.normalize("NFKC", text).lower()
    s = re.sub(r"[^\w\s]|_", " ", s)
    if lang in _CJK_LANGS:
        return [c for c in re.sub(r"\s+", "", s) if not c.isdigit() and c not in _ZH_NUM]
    return [w for w in s.split() if not w.isdigit() and w not in _EN_NUM]


def edit_distance(a: list[str], b: list[str]) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[-1]


def _tail_missing(ref: list[str], hyp: list[str]) -> bool:
    """True when the end of the text is absent from the audio. The model's most
    common content failure is stopping early, and a lost last sentence can be
    only ~10 % of a passage — under any sensible overall threshold, yet the
    first thing a listener notices."""
    import difflib
    n = max(4, len(ref) // 5)
    if len(ref) < 8:
        return False
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    covered = sum(max(0, min(b.a + b.size, len(ref)) - max(b.a, len(ref) - n))
                  for b in sm.get_matching_blocks())
    return covered < n * 0.5


def error_rate(reference: str, hypothesis: str, lang: str) -> Optional[float]:
    ref, hyp = tokens(reference, lang), tokens(hypothesis, lang)
    if len(ref) < 3:
        return None                        # too little to judge
    err = min(1.0, edit_distance(ref, hyp) / len(ref))
    if _tail_missing(ref, hyp):
        err = max(err, 0.5)
    return err


def make_verifier(engine):
    """A ``Verifier`` for ``TtsEngine.synthesize`` backed by the dictation
    engine, or None when no recogniser is loaded."""
    if engine is None or not getattr(engine, "is_loaded", False):
        return None

    def verify(audio: np.ndarray, text: str, lang: str) -> Optional[float]:
        x16 = audio_io.resample(audio.astype(np.float32), 24000, 16000)
        with GPU_LOCK:
            r = engine.recognize(x16, 16000)
        return error_rate(text, r.text or "", lang)

    return verify
