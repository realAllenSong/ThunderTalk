"""Offline, advisory artist-name warnings for Compose. Never blocks generation."""
from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from thundertalk import asset_path

# These words are also ordinary descriptions. They count only near a cue.
AMBIGUOUS = frozenset('queen yes muse genesis rush kiss air heart cream journey eagles oasis '
                      'santana chicago boston kansas asia europe america tool seal pink blur pulp '
                      'future drake sting prince live traffic focus garbage suede bread'.split())
_CJK = re.compile(r'[\u3400-\u9fff]')
_CUES = re.compile(
    r'(?:in\s+the\s+style\s+of|sounds?\s+like|cover\s+of|imitat(?:e|ing))\s+'
    r'(?P<en>[^,.;\n!?]{1,60})|(?P<style>[\w .\-]{2,40})[- ]style\b|'
    r'像(?P<like>[^，。\n]{1,30}?)一样|(?P<zh>[\u3400-\u9fffA-Za-z0-9·]{2,20})风格|'
    r'模仿(?P<imitate>[^，。\n]{1,30})|(?P<cover>翻唱)', re.IGNORECASE)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str


@functools.lru_cache(maxsize=1)
def artist_names():
    path = Path(asset_path('music/artists.txt'))
    try:
        return tuple(line.strip() for line in path.read_text('utf-8').splitlines()
                     if line.strip() and not line.startswith('#'))
    except OSError:
        return ()


@functools.lru_cache(maxsize=1)
def _patterns():
    return tuple(_pattern(name) for name in artist_names() if len(name) >= 2)


def _pattern(name):
    name = unicodedata.normalize('NFC', name).strip()
    pattern = re.escape(name) if _CJK.search(name) else r'(?<!\w)' + re.escape(name) + r'(?!\w)'
    return name, re.compile(pattern, re.IGNORECASE)


def find_artist_mentions(text: str, names=None) -> list[Span]:
    """Offsets always refer to the original string, including non-ASCII casefolds."""
    if not text.strip():
        return []
    # Normalise names, not the source: replacing by returned spans stays safe.
    cues = list(_CUES.finditer(text))
    found = []
    patterns = _patterns() if names is None else (_pattern(name) for name in names if len(name) >= 2)
    for name, pattern in patterns:
        for match in pattern.finditer(text):
            if name.casefold() in AMBIGUOUS and not any(
                    cue.start() <= match.start() and match.end() <= cue.end() for cue in cues):
                continue
            found.append(Span(match.start(), match.end(), match.group()))
    for cue in cues:
        # Known artists get just the name removed. Unknown cue requests lose
        # the whole phrase, rather than leaving a dangling imitation prompt.
        if not any(cue.start() <= span.start and span.end <= cue.end() for span in found):
            found.append(Span(cue.start(), cue.end(), cue.group()))
    ordered = sorted(found, key=lambda s: (s.start, -(s.end - s.start)))
    result = []
    for span in ordered:
        if not result or span.start >= result[-1].end:
            result.append(span)
    return result


def remove_mentions(text: str, spans: list[Span]) -> str:
    for span in sorted(spans, key=lambda s: s.start, reverse=True):
        text = text[:span.start] + text[span.end:]
    return re.sub(r' {2,}', ' ', text).strip()
