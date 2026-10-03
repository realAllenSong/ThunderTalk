"""Recover Latin terms from a clean preview without rewriting final ASR text."""

from difflib import SequenceMatcher
import re

# Keep terms atomic so shared letters/digits cannot create partial replacements.
_LATIN = r"[A-Za-z][A-Za-z0-9]*(?:[.+'’_-][A-Za-z0-9]+)*"
_TOKEN = re.compile(rf"{_LATIN}(?:[ \t]+{_LATIN})*|[^\s]")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{1,16}\Z")
_TERM = re.compile(rf"{_LATIN}(?:[ \t]+{_LATIN})*\Z")
_PUNCT = str.maketrans({"，": ",", "、": ",", "。": ".", "：": ":",
                       "；": ";", "！": "!", "？": "?"})


def merge_preview_terms(final: str, preview: str | None) -> str:
    """Replace only bounded CJK-only spans aligned to complete Latin terms.

    Whitespace and equivalent punctuation are ignored for alignment, but all
    final bytes outside accepted spans are preserved. Require >=65% symmetric
    token similarity, four matching non-punctuation characters, and anchors on
    both sides of each substitution. Missing tails, insertions and ambiguous
    multi-term spans are left alone. The caller must supply only clean text.
    Alignment is evidence of position, not proof of what was actually spoken.
    """
    if not preview or not final or final == preview:
        return final
    f, p = list(_TOKEN.finditer(final)), list(_TOKEN.finditer(preview))

    def keys(tokens):
        return [m.group().casefold().translate(_PUNCT) for m in tokens]

    matcher = SequenceMatcher(None, keys(f), keys(p), autojunk=False)
    blocks = matcher.get_matching_blocks()
    matched = sum(b.size for b in blocks)
    anchors = sum(sum(c.isalnum() for c in f[i].group())
                  for b in blocks for i in range(b.a, b.a + b.size))
    if 2 * matched / max(len(f) + len(p), 1) < 0.65 or anchors < 4:
        return final
    edits = []
    opcodes = matcher.get_opcodes()
    for n, (tag, a, b, c, d) in enumerate(opcodes):
        if tag != "replace" or d - c != 1 or not (0 < n < len(opcodes) - 1):
            continue
        if opcodes[n - 1][0] != "equal" or opcodes[n + 1][0] != "equal":
            continue
        old = final[f[a].start():f[b - 1].end()]
        term = p[c].group()
        if _CJK.fullmatch(old) and _TERM.fullmatch(term):
            edits.append((f[a].start(), f[b - 1].end(), term))
    for start, end, term in reversed(edits):
        final = final[:start] + term + final[end:]
    return final
