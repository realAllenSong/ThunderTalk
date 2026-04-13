"""Auto-learn hotwords by detecting user corrections after ASR paste.

After ThunderTalk pastes transcribed text into the active app, the user
may manually correct a word.  If they then copy the corrected text
(e.g. selecting the word and copying), we detect the new clipboard
content differs from what we pasted and learn the new words as hotwords.

Workflow:
1. After paste, record the pasted text and start a short monitoring window.
2. Poll the clipboard for changes during that window.
3. If new clipboard content is a plausible correction (short edit of our
   text), extract the changed words and add them as hotwords.
"""

from __future__ import annotations

import difflib
import threading
import time
from typing import Callable, Optional

import pyperclip


_last_pasted: str = ""
_monitor_thread: Optional[threading.Thread] = None
_on_new_hotword: Optional[Callable[[str], None]] = None

MONITOR_DURATION_SECS = 30
POLL_INTERVAL_SECS = 1.5


def set_callback(cb: Callable[[str], None]) -> None:
    """Set callback invoked with each auto-learned word."""
    global _on_new_hotword
    _on_new_hotword = cb


def on_text_pasted(text: str) -> None:
    """Call after ASR text is pasted. Starts clipboard monitoring."""
    global _last_pasted, _monitor_thread
    _last_pasted = text.strip()
    if not _last_pasted:
        return
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _monitor_thread = threading.Thread(target=_monitor_clipboard, daemon=True)
    _monitor_thread.start()


def _monitor_clipboard() -> None:
    baseline = _last_pasted
    deadline = time.monotonic() + MONITOR_DURATION_SECS
    seen = {baseline}

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECS)
        try:
            current = pyperclip.paste().strip()
        except Exception:
            continue
        if not current or current in seen:
            continue
        seen.add(current)
        new_words = _extract_corrections(baseline, current)
        for w in new_words:
            if _on_new_hotword:
                _on_new_hotword(w)


def _extract_corrections(original: str, corrected: str) -> list[str]:
    """Find words the user changed (added or replaced)."""
    if len(corrected) > len(original) * 3 or len(corrected) < 2:
        return []

    ratio = difflib.SequenceMatcher(None, original.lower(), corrected.lower()).ratio()
    if ratio < 0.3:
        return []

    orig_words = set(original.lower().split())
    corr_words = corrected.split()

    new_words = []
    for w in corr_words:
        if w.lower() not in orig_words and len(w) >= 2:
            new_words.append(w)

    return new_words[:5]
