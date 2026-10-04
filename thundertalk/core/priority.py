"""Dictation goes first.

Hold-to-talk dictation and Studio file jobs share the dictation model (and,
for MLX models, the GPU). A Studio job must never make a dictation late or
incomplete, so the dictation pipeline raises ``DICTATION`` when recording
starts and lowers it once the text is pasted; long Studio jobs call
``wait_clear()`` between units of work (one span, one MOSS token) and stay
paused while it is raised.

It counts: a new recording can start while the previous one is still being
decoded, and the first one finishing must not resume Studio under the second.
A count left raised by a bug must not freeze Studio forever, so it expires
``stale_s`` seconds after the last ``begin()``.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class DictationPriority:
    def __init__(self, stale_s: float = 600.0) -> None:
        self._cond = threading.Condition()
        self._count = 0
        self._since = 0.0
        self._stale_s = stale_s

    def begin(self) -> None:
        with self._cond:
            now = time.monotonic()
            if now - self._since >= self._stale_s:
                self._count = 0               # forget a leaked begin()
            self._count += 1
            self._since = now

    def end(self) -> None:
        with self._cond:
            self._count = max(0, self._count - 1)
            if not self._count:
                self._cond.notify_all()

    @property
    def active(self) -> bool:
        return self._count > 0 and time.monotonic() - self._since < self._stale_s

    def wait_clear(self, cancel: Optional[threading.Event] = None,
                   on_wait: Optional[Callable[[], None]] = None, poll: float = 0.1) -> float:
        """Block while a dictation is in progress (or until ``cancel`` is set).
        ``on_wait`` runs once, before waiting. Returns the seconds waited."""
        if not self.active:
            return 0.0
        if on_wait is not None:
            on_wait()
        t0 = time.monotonic()
        with self._cond:
            while self.active and not (cancel is not None and cancel.is_set()):
                self._cond.wait(poll)
        return time.monotonic() - t0


DICTATION = DictationPriority()
