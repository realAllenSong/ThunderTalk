"""Conservative, model-independent rejection of runaway preview output."""

import re
import unicodedata


def preview_is_looping(text: str, duration: float) -> bool:
    """Reject three adjacent copies of a phrase, ignoring case/separators.

    The generous 24 characters/second + 48 allowance covers fast mixed
    speech. A suspect decode is discarded in its entirety, including its
    prefix: none of that window is trusted for committing or term recovery.
    This guards output, not inference time (sherpa has no per-call cap).
    """
    compact = "".join(c.casefold() for c in text if c.isalnum())
    if len("".join(text.split())) > 48 + max(0.0, duration) * 24:
        return True
    if re.search(r"\b([a-z])(?:[\s,;.!?]+\1){2,}\b", text, re.IGNORECASE):
        return True
    # Bound work before looking for tandem n-grams, even on runaway output.
    for start in range(len(compact)):
        for size in range(1, (len(compact) - start) // 3 + 1):
            phrase = compact[start:start + size]
            # Single Latin letters in normal spelling ("www") aren't phrases.
            if size == 1 and "CJK" not in unicodedata.name(phrase, ""):
                continue
            if compact.startswith(phrase * 3, start):
                return True
    return False
