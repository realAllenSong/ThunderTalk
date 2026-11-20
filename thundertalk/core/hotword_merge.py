"""Let hotwords change only the words they are about.

A local LLM decoder takes the user's hotwords as a prompt, and a prompt shifts
the whole transcript, not just the hotword: with the single hotword
"PlayWright CLI", 21 of 40 real dictations came out differently, unrelated
sentences included ("我不懂。" → "我不行。"). So the decoder runs twice, without
and with the hotwords, and ``merge`` starts from the plain transcript and takes
from the hinted one only the stretches where a hotword appears.

The second decode only runs when it can matter: ``candidates`` finds the
hotwords that some stretch of the plain transcript sounds like, and only those
go into the prompt. Most dictations name no hotword, so they cost one decode,
and a long hotword list no longer has to fit the prompt. A hotword the decoder
already wrote is put in the user's spelling without a decode (``respell``).

The prompt also makes the decoder write a hotword over words that sound
nothing like it ("USD" → "PlayWright CLI", "opportunity" → "Opus Sonnet"), so
a stretch is only taken when the plain words sound like what replaces them
(``sounds_like``: letters and tone-less pinyin, so "罗塞塔" can become "Rosetta").
"""
from __future__ import annotations

import difflib
import functools
import re

_UNIT = re.compile(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*|[㐀-鿿぀-ヿ가-힯]")


SOUND_ALIKE = 0.52     # "play race"/"Playwright CLI" 0.57, "罗塞塔"/"Rosetta" 0.53; "道它"/"Rosetta" 0.50, "girls night"/"歌 Sonnet" 0.44


@functools.lru_cache(maxsize=8192)
def _pinyin(ch: str) -> str:
    try:
        from pypinyin import lazy_pinyin
        return lazy_pinyin(ch)[0]
    except Exception:
        return ch


def _sound(text: str) -> str:
    out = []
    for ch in text.lower():
        if "\u3400" <= ch <= "\u9fff":
            out.append(_pinyin(ch))
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
            start, end = index[at], index[at + len(key) - 1] + 1
            if not (_inside_word(text, start - 1, start) or _inside_word(text, end - 1, end)):
                spans.append((start, end))
            at = squashed.find(key, at + 1)
    return spans


def _inside_word(text: str, left: int, right: int) -> bool:
    """True when the characters at ``left`` and ``right`` belong to one Latin word ("Sol" in "solution")."""
    return (0 <= left and right < len(text)
            and text[left].isascii() and text[left].isalnum() and text[right].isascii() and text[right].isalnum())


def respell(text: str, hotwords: list[str]) -> str:
    """Hotwords the decoder already wrote, in the user's spelling ("playwright cli" → "PlayWright CLI")."""
    spans = sorted((s, e, w) for w in hotwords for s, e in _hotword_spans(text, [w]))
    out, at = [], 0
    for s, e, w in spans:
        if s < at:
            continue
        out += [text[at:s], w]
        at = e
    return "".join(out) + text[at:]


def _alike_enough(key: str) -> float:
    """How close a stretch must sound before the second decode is worth running.

    Letters and pinyin are a blunt ear: at 0.52 "Gemini" matches some stretch in
    78 % of real dictations and "Opus" in 72 %, a 13-letter hotword in 4 %.
    Short hotwords therefore need a closer match (measured on 596 dictations:
    Sol/Opus/Terra/Sonnet ≤ 2 %, Gemini 15 %, Haiku 29 % — "还可" is "hai ke")."""
    n = len(key)
    return 0.75 if n <= 4 else 0.67 if n <= 6 else 0.6 if n <= 9 else 0.55


_DIGRAPHS = (("zh", "j"), ("ch", "k"), ("sh", "s"), ("th", "t"), ("ph", "f"), ("ck", "k"), ("ng", "n"), ("x", "ks"))
_CLASSES = str.maketrans("lbdgcqzxv", "rptkkkjsf")


def _skeleton(key: str) -> str:
    """Consonants of a sound key in coarse classes (l=r, b=p, d=t, g=k=c…), repeats dropped.
    A name written in Chinese keeps these and little else: 罗塞塔 "luosaita" and "rosetta" are both r-s-t."""
    for a, b in _DIGRAPHS:
        key = key.replace(a, b)
    out = []
    for ch in key.translate(_CLASSES):
        if ch not in "aeiouyw0123456789" and (not out or out[-1] != ch):
            out.append(ch)
    return "".join(out)


def candidates(plain: str, hotwords: list[str]) -> list[str]:
    """The hotwords some stretch of ``plain`` sounds like but does not spell, closest first."""
    units = [(m.group(), _sound(m.group())) for m in _UNIT.finditer(plain)]
    units = [(u, k) for u, k in units if k]
    found = []
    for word in hotwords:
        key = _sound(word)
        if not key or _hotword_spans(plain, [word]):
            continue
        need = _alike_enough(key)
        bones = _skeleton(key) if word.isascii() else ""
        sm = difflib.SequenceMatcher(None, autojunk=False)
        sm.set_seq2(key)
        lo, hi, best = max(2, -(-len(key) * 3 // 5)), len(key) * 1.7, 0.0
        for i in range(len(units)):
            window, chinese = "", True
            for unit, sound in units[i:]:
                window += sound
                chinese = chinese and not unit.isascii()
                if len(window) > hi:
                    break
                if len(window) >= lo:
                    sm.set_seq1(window)
                    if sm.real_quick_ratio() >= need and sm.quick_ratio() >= need:
                        best = max(best, sm.ratio())
                    elif chinese and len(bones) >= 3 and _skeleton(window) == bones:
                        best = max(best, need)        # a transliteration: same consonants, other vowels
        if best >= need:
            found.append((best, word))
    return [w for _score, w in sorted(found, key=lambda f: -f[0])]


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
