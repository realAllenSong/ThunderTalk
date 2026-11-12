"""Word-level equality that ignores punctuation, case and acronym spacing."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[一-鿿぀-ヿ]|[A-Za-z0-9]+(?:'[A-Za-z0-9]+)*")


def words(text: str) -> list[str]:
    text = re.sub(r"(?<![A-Za-z])[A-Za-z](?:\s+[A-Za-z](?![A-Za-z]))+",
                  lambda m: re.sub(r"\s+", "", m.group(0)), text or "")
    return [t.lower() for t in _TOKEN.findall(text)]


def same_words(a: str, b: str) -> bool:
    """True when ``b`` only adds punctuation/capitals/acronym joins to ``a``."""
    wa, wb = words(a), words(b)
    if wa == wb:
        return True
    # "i s m" may be joined in b but not a (or vice versa): compare without spaces.
    return "".join(wa) == "".join(wb)
