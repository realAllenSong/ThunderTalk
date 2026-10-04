"""Lossless proofreading diff: CJK characters, Latin words and punctuation."""
from dataclasses import dataclass
from collections.abc import Callable
from difflib import SequenceMatcher
import math
import re

_TOKEN = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]|[^\W_]+(?:['’][^\W_]+)*|\s+|.", re.UNICODE)


@dataclass(frozen=True)
class DiffSpan:
    kind: str
    original: str
    corrected: str


def _tokens(text: str) -> list[str]:
    # Split CJK runs that the Unicode word branch can absorb after Latin text.
    return [part for token in _TOKEN.findall(text) for part in re.findall(
        r"[\u3400-\u9fff\U00020000-\U000323af]|[^\u3400-\u9fff\U00020000-\U000323af]+", token)]


def proofread_diff(original: str, corrected: str) -> list[DiffSpan]:
    before, after = _tokens(original), _tokens(corrected)
    return [DiffSpan(kind, "".join(before[a:b]), "".join(after[c:d]))
            for kind, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()]


@dataclass(frozen=True)
class DiffPage:
    spans: tuple[DiffSpan, ...]
    changes: int
    duration: float
    omitted: int = 0


def display_text(spans: tuple[DiffSpan, ...]) -> str:
    return "".join(s.original if s.kind == "equal" else
                   s.original + (" → " if s.original and s.corrected else "") + s.corrected
                   for s in spans)


def reading_units(text: str) -> int:
    """CJK characters and Latin words; unusually long words need extra time."""
    return sum(max(1, math.ceil(len(token) / 12)) for token in _tokens(text)
               if not token.isspace())


def page_duration(units: int) -> float:
    return min(6.0, 3.0 + 0.17 * units)


def _context(text: str, *, tail: bool) -> str:
    tokens = _tokens(text)
    # Four non-space tokens: four Chinese characters or a few English words.
    selected, count = [], 0
    for token in reversed(tokens) if tail else tokens:
        if not token.isspace():
            count += 1
        if count > 4:
            break
        selected.append(token)
    value = "".join(reversed(selected) if tail else selected)
    return ("…" + value if tail else value + "…") if len(value) < len(text) else value


def _elide(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…" if text else ""
    left = limit // 2
    return text[:left] + "…" + text[-(limit - left - 1):] if limit > 2 else text[:1] + "…"


def proofread_pages(original: str, corrected: str,
                    fits: Callable[[str], bool]) -> list[DiffPage]:
    """Group contextual changes into measured two-line pages, at most 25 s.

    The UI supplies its actual font/width measurement. Oversized single edits
    are elided on both sides, preserving the arrow and correction styling.
    Durations use the full edits, so truncation never shortens reading time.
    """
    diff = proofread_diff(original, corrected)
    snippets = []
    for i, span in enumerate(diff):
        if span.kind == "equal":
            continue
        before = _context(diff[i - 1].original, tail=True) if i else ""
        after = _context(diff[i + 1].original, tail=False) if i + 1 < len(diff) else ""
        def snippet(old=span.original, new=span.corrected):
            return (DiffSpan("equal", before, before), DiffSpan(span.kind, old, new),
                    DiffSpan("equal", after, after))
        spans = snippet()
        if not fits(display_text(spans)):
            before = "…" if before else ""
            after = "…" if after else ""
            spans = snippet()
        if not fits(display_text(spans)):
            low, high = 1, max(len(span.original), len(span.corrected))
            while low < high:
                mid = (low + high + 1) // 2
                candidate = snippet(_elide(span.original, mid), _elide(span.corrected, mid))
                if fits(display_text(candidate)):
                    low = mid
                else:
                    high = mid - 1
            spans = snippet(_elide(span.original, low), _elide(span.corrected, low))
        snippets.append((spans, max(reading_units(span.original), reading_units(span.corrected))))

    pages, current, count, units = [], (), 0, 0
    separator = (DiffSpan("equal", "\u2028", "\u2028"),)
    for spans, size in snippets:
        candidate = current + (separator if current else ()) + spans
        if current and (count == 3 or not fits(display_text(candidate))):
            pages.append(DiffPage(current, count, page_duration(units)))
            current, count, units = (), 0, 0
        current += (separator if current else ()) + spans
        count += 1
        units += size
    if current:
        pages.append(DiffPage(current, count, page_duration(units)))
    kept, total = [], 0.0
    for page in pages:
        if total + page.duration > 25.0:
            break
        kept.append(page)
        total += page.duration
    if kept and len(kept) < len(pages):
        last = kept[-1]
        kept[-1] = DiffPage(last.spans, last.changes, last.duration,
                            sum(p.changes for p in pages[len(kept):]))
    return kept
