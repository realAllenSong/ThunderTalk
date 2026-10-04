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


def _elide(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…" if text else ""
    left = limit // 2
    return text[:left] + "…" + text[-(limit - left - 1):] if limit > 2 else text[:1] + "…"


def _merge(spans: list[DiffSpan]) -> tuple[DiffSpan, ...]:
    out: list[DiffSpan] = []
    for span in spans:
        if out and span.kind == "equal" and out[-1].kind == "equal":
            text = out[-1].original + span.original
            out[-1] = DiffSpan("equal", text, text)
        else:
            out.append(span)
    return tuple(out)


def proofread_pages(original: str, corrected: str,
                    fits: Callable[[str], bool]) -> list[DiffPage]:
    """Lay out the whole text with inline changes and cut it into pages.

    *fits* is the UI's measurement of one page (its width and line count), so
    pages use the full overlay width like the live transcript. Only pages that
    contain a change are kept; "…" marks text skipped before or after a page.
    An edit too large for one page is elided on both sides. Durations count the
    full edits plus a little for the surrounding text, at most 25 s in total.
    """
    atoms: list[tuple[DiffSpan, int]] = []
    for span in proofread_diff(original, corrected):
        if span.kind == "equal":
            atoms += [(DiffSpan("equal", tok, tok), 0) for tok in _tokens(span.original)]
            continue
        units = max(reading_units(span.original), reading_units(span.corrected))
        if not fits("…" + display_text((span,)) + "…"):
            low, high = 1, max(len(span.original), len(span.corrected))
            while low < high:
                mid = (low + high + 1) // 2
                candidate = DiffSpan(span.kind, _elide(span.original, mid), _elide(span.corrected, mid))
                if fits("…" + display_text((candidate,)) + "…"):
                    low = mid
                else:
                    high = mid - 1
            span = DiffSpan(span.kind, _elide(span.original, low), _elide(span.corrected, low))
        atoms.append((span, units))

    chunks: list[list[tuple[DiffSpan, int]]] = []
    current: list[tuple[DiffSpan, int]] = []
    for atom in atoms:
        if not current and atom[0].kind == "equal" and atom[0].original.isspace():
            continue
        if current and not fits("…" + display_text(tuple(a[0] for a in current + [atom])) + "…"):
            chunks.append(current)
            current = []
            if atom[0].kind == "equal" and atom[0].original.isspace():
                continue
        current.append(atom)
    if current:
        chunks.append(current)

    shown = [any(span.kind != "equal" for span, _ in chunk) for chunk in chunks]
    pages = []
    for i, chunk in enumerate(chunks):
        if not shown[i]:
            continue
        spans = [span for span, _ in chunk]
        while spans and spans[-1].kind == "equal" and spans[-1].original.isspace():
            spans.pop()
        if i > 0 and not shown[i - 1]:
            spans.insert(0, DiffSpan("equal", "…", "…"))
        if i + 1 < len(chunks) and not shown[i + 1]:
            spans.append(DiffSpan("equal", "…", "…"))
        changes = sum(1 for span, _ in chunk if span.kind != "equal")
        context = reading_units("".join(span.original for span, _ in chunk if span.kind == "equal"))
        units = sum(u for _, u in chunk) + context // 10
        pages.append(DiffPage(_merge(spans), changes, page_duration(units)))
    kept, total = [], 0.0
    for page in pages:
        if kept and total + page.duration > 25.0:
            break
        kept.append(page)
        total += page.duration
    if kept and len(kept) < len(pages):
        last = kept[-1]
        kept[-1] = DiffPage(last.spans, last.changes, last.duration,
                            sum(p.changes for p in pages[len(kept):]))
    return kept
