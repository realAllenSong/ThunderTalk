"""Let hotwords change only the words they are about.

A local LLM decoder takes the user's hotwords as a prompt, and a prompt shifts
the whole transcript, not just the hotword: with the single hotword
"PlayWright CLI", 21 of 40 real dictations came out differently, unrelated
sentences included ("我不懂。" → "我不行。"). So the decoder runs twice, without
and with the hotwords, and ``merge`` starts from the plain transcript and takes
from the hinted one only the stretches where a hotword appears.

The prompt also makes the decoder write a hotword over words that sound
nothing like it ("USD" → "PlayWright CLI", "opportunity" → "Opus Sonnet"), so
a stretch is only taken when the plain words sound like what replaces them
(``sounds_like``: letters and tone-less pinyin, so "罗塞塔" can become "Rosetta").
"""
from __future__ import annotations

import difflib
import re

_UNIT = re.compile(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*|[㐀-鿿぀-ヿ가-힯]")


SOUND_ALIKE = 0.5      # "play race"/"Playwright CLI" 0.57, "罗塞塔"/"Rosetta" 0.53; "girls night"/"歌 Sonnet" 0.44


def _sound(text: str) -> str:
    out = []
    for ch in text.lower():
        if "\u3400" <= ch <= "\u9fff":
            try:
                from pypinyin import lazy_pinyin
                out.append(lazy_pinyin(ch)[0])
            except Exception:
                out.append(ch)
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
    return "".join(out)


def sounds_like(heard: str, written: str) -> bool:
    a, b = _sound(heard), _sound(written)
    return bool(a and b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= SOUND_ALIKE


def _pieces(text: str) -> tuple[str, list[str], list[tuple[int, int]]]:
    """Text before the first word, then one piece per word (the word and
    whatever follows it up to the next word), with each piece's span."""
    starts = [m.start() for m in _UNIT.finditer(text)]
    if not starts:
        return text, [], []
    spans = list(zip(starts, starts[1:] + [len(text)]))
    return text[:starts[0]], [text[a:b] for a, b in spans], spans


def _hotword_spans(text: str, hotwords: list[str]) -> list[tuple[int, int]]:
    """Where the hotwords occur in ``text``, ignoring case and spaces."""
    index = [i for i, c in enumerate(text) if not c.isspace()]
    squashed = "".join(text[i] for i in index).lower()
    spans = []
    for word in hotwords:
        key = "".join(word.lower().split())
        at = squashed.find(key) if key else -1
        while at >= 0:
            spans.append((index[at], index[at + len(key) - 1] + 1))
            at = squashed.find(key, at + 1)
    return spans


def merge(plain: str, hinted: str, hotwords: list[str]) -> str:
    """``plain`` with the hotword stretches of ``hinted`` put in.

    A stretch is taken when the two transcripts differ there, the hinted side
    overlaps a hotword and the two sides sound alike; everything else stays as
    the plain decode wrote it. Nothing is taken when the plain transcript is empty, or where it has no
    words at all for the stretch (a prompt can make the decoder recite the
    hotwords over silence).
    """
    if not plain.strip() or not hinted.strip() or plain == hinted or not hotwords:
        return plain
    targets = _hotword_spans(hinted, hotwords)
    if not targets:
        return plain
    head_a, a, _ = _pieces(plain)
    _head_b, b, spans_b = _pieces(hinted)
    if not a or not b:
        return plain
    word = lambda piece: _UNIT.match(piece).group()      # noqa: E731
    sm = difflib.SequenceMatcher(None, [word(p) for p in a], [word(p) for p in b], autojunk=False)
    out = [head_a]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        take = False
        if tag == "replace":          # a hotword added where the plain decode heard nothing is not trusted
            lo, hi = spans_b[j1][0], spans_b[j2 - 1][1]
            take = (any(s < hi and e > lo for s, e in targets)
                    and sounds_like("".join(a[i1:i2]), "".join(b[j1:j2])))
        out += b[j1:j2] if take else a[i1:i2]
    return "".join(out)
