"""Names from where the user is typing, used as hotwords nobody had to add.

When a dictation starts, the text around the cursor in the focused field (and
the window title) is read through Accessibility, on this Mac only, and the
distinctive names in it — "Rosetta", "Playwright CLI", "GRPO", "MCP" — become
hotwords for that take. They go the same way as the user's own hotwords
(``hotword_merge``): never into the prompt of the decode the text comes from,
only into the check of stretches that sound like them. So a name that is on
screen is spelled the way it is on screen, and nothing else changes.

Only words that look like names are taken (an inner capital, a digit, an
acronym, or a capitalised word in the middle of a sentence); ordinary words
need no help and would only cost second decodes.
"""
from __future__ import annotations

import platform
import re
import threading

MAX_TERMS = 40
BEFORE, AFTER = 3000, 500          # characters read on either side of the cursor
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.+'’][A-Za-z0-9]+)*")
_HASH = re.compile(r"^(?=.*\d)[0-9a-fA-F]{7,}$")
# Capitalised or upper-case, and still not a name.
_PLAIN = {w.lower() for w in """
I A An The This That These Those It He She We You They My Your Our His Her Its Their If In On At To Of For From By With
And Or But So As Is Are Was Were Be Been Am Do Does Did Not No Yes OK Okay Hi Hello Thanks Thank Please Sorry Let Can
Could Would Should Will May Might Must What When Where Why How Who Which There Here Then Now Also Just Only Very More
Most Some Any All Each Every One Two Three First Second Next Last New Old Good Great Note Notes Todo TODO FIXME Done
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April June July August September
October November December AM PM Re Fwd Dear Best Regards True False None Null
""".split()}


def _is_name(token: str, mid_sentence: bool) -> bool:
    if len(token) < 2 or len(token) > 30 or token.lower() in _PLAIN or _HASH.match(token):
        return False
    if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
        return len(token) >= 3
    if any(c.isupper() for c in token[1:]):
        return True                                   # GitHub, MCP, iPhone, PyTorch
    return token[0].isupper() and mid_sentence and len(token) >= 3


def extract_terms(text: str, limit: int = MAX_TERMS) -> list[str]:
    """Distinctive names in ``text``, the ones nearest its end (the cursor) first."""
    found: list[tuple[int, str]] = []
    prev_end, prev_name = -2, False
    for m in _TOKEN.finditer(text):
        token = m.group()
        before = text[max(0, m.start() - 2):m.start()]
        gap = before.rstrip(" ")[-1:] if before.strip() else ""
        mid = bool(gap) and gap not in ".!?。！？:：\n\r\t•-*#>|"
        name = _is_name(token, mid)
        joins = prev_name and text[prev_end:m.start()] == " " and (name or (token[0].isupper() and token.lower() not in _PLAIN))
        if joins and found and len(found[-1][1].split()) < 3:
            found[-1] = (m.end(), found[-1][1] + " " + token)      # "Claude Code", "GitHub Actions"
            prev_end, prev_name = m.end(), True
            continue
        if name:
            found.append((m.end(), token))
        prev_end, prev_name = m.end(), name
    out, seen = [], set()
    for _pos, term in sorted(found, key=lambda f: -f[0]):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
            if len(out) >= limit:
                break
    return out


def _ax_text(pid: int) -> str:
    """Text around the cursor of the focused element of app ``pid``, plus its window title."""
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCopyParameterizedAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementSetMessagingTimeout,
        AXValueCreate,
        AXValueGetValue,
        kAXValueCFRangeType,
    )
    from CoreFoundation import CFRangeMake

    app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, 0.25)          # a hung app must not hold up a dictation
    parts = []
    err, window = AXUIElementCopyAttributeValue(app, "AXFocusedWindow", None)
    if not err and window is not None:
        err, title = AXUIElementCopyAttributeValue(window, "AXTitle", None)
        if not err and isinstance(title, str):
            parts.append(title)
    err, focused = AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
    if err or focused is None:
        return "\n".join(parts)
    AXUIElementSetMessagingTimeout(focused, 0.25)
    cursor = None
    err, selected = AXUIElementCopyAttributeValue(focused, "AXSelectedTextRange", None)
    if not err and selected is not None:
        ok, rng = AXValueGetValue(selected, kAXValueCFRangeType, None)
        if ok:
            cursor = int(rng[0] if isinstance(rng, tuple) else rng.location)
    if cursor is not None:
        start = max(0, cursor - BEFORE)
        wanted = AXValueCreate(kAXValueCFRangeType, CFRangeMake(start, cursor - start + AFTER))
        err, chunk = AXUIElementCopyParameterizedAttributeValue(focused, "AXStringForRange", wanted, None)
        if err or not isinstance(chunk, str):          # range past the end: ask for the part before the cursor only
            wanted = AXValueCreate(kAXValueCFRangeType, CFRangeMake(start, cursor - start))
            err, chunk = AXUIElementCopyParameterizedAttributeValue(focused, "AXStringForRange", wanted, None)
        if not err and isinstance(chunk, str) and chunk.strip():
            parts.append(chunk)
            return "\n".join(parts)
    err, value = AXUIElementCopyAttributeValue(focused, "AXValue", None)
    if not err and isinstance(value, str) and value:
        parts.append(value[max(0, cursor - BEFORE):cursor + AFTER] if cursor is not None else value[-BEFORE:])
        return "\n".join(parts)
    # Web views (browsers, Electron apps) keep their text behind text markers, not in AXValue.
    err, first = AXUIElementCopyAttributeValue(focused, "AXStartTextMarker", None)
    if err or first is None:
        return "\n".join(parts)
    last = None
    err, selection = AXUIElementCopyAttributeValue(focused, "AXSelectedTextMarkerRange", None)
    if not err and selection is not None:
        err, last = AXUIElementCopyParameterizedAttributeValue(focused, "AXStartTextMarkerForTextMarkerRange", selection, None)
    if err or last is None:
        err, last = AXUIElementCopyAttributeValue(focused, "AXEndTextMarker", None)
    if err or last is None:
        return "\n".join(parts)
    err, span = AXUIElementCopyParameterizedAttributeValue(focused, "AXTextMarkerRangeForUnorderedTextMarkers", [first, last], None)
    if not err and span is not None:
        err, text = AXUIElementCopyParameterizedAttributeValue(focused, "AXStringForTextMarkerRange", span, None)
        if not err and isinstance(text, str):
            parts.append(text[-BEFORE:])
    return "\n".join(parts)


def read_context(pid: int | None) -> str:
    if not pid or platform.system() != "Darwin":
        return ""
    try:
        return _ax_text(int(pid))
    except Exception as exc:                            # no Accessibility grant, app gone, odd element
        print(f"[Context] Not read: {type(exc).__name__}")
        return ""


class ContextCapture:
    """Reads the context off the UI thread when a dictation starts; ``terms`` is ready by the time it ends."""

    def __init__(self, reader=read_context, on_terms=None):
        self._reader = reader
        self._on_terms = on_terms          # called with the names of the current take (empty at its start)
        self._lock = threading.Lock()
        self._terms: list[str] = []
        self._take = 0

    def start(self, pid: int | None) -> None:
        with self._lock:
            self._take += 1
            self._terms = []
            take = self._take
        if self._on_terms:
            self._on_terms([])

        def run() -> None:
            try:
                terms = extract_terms(self._reader(pid)) if pid else []
            except Exception as exc:                    # context is a bonus; a dictation never depends on it
                print(f"[Context] Not read: {type(exc).__name__}")
                terms = []
            with self._lock:
                current = take == self._take
                if current:
                    self._terms = terms
            if current and terms and self._on_terms:
                self._on_terms(terms)
            if terms:
                print(f"[Context] {len(terms)} names from the focused field")

        threading.Thread(target=run, name="context-terms", daemon=True).start()

    @property
    def terms(self) -> list[str]:
        with self._lock:
            return list(self._terms)
