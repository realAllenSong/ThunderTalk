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

import os
import tempfile
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Optional


class DictationPriority:
    def __init__(self, stale_s: float = 600.0, shared_path: Optional[Path] = None) -> None:
        self._shared_path = shared_path
        self._lease = None
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
            if self._shared_path is not None:
                import fcntl
                if self._lease is None:
                    self._shared_path.parent.mkdir(parents=True, exist_ok=True)
                    self._lease = self._shared_path.open("a+")
                    fcntl.flock(self._lease, fcntl.LOCK_SH)
                os.utime(self._shared_path, None)

    def end(self) -> None:
        with self._cond:
            self._count = max(0, self._count - 1)
            if not self._count:
                if self._lease is not None:
                    self._lease.close()       # also released automatically if the app crashes
                    self._lease = None
                self._cond.notify_all()

    @property
    def active(self) -> bool:
        if self._count > 0 and time.monotonic() - self._since < self._stale_s:
            return True
        path = self._shared_path
        if path is None:
            return False
        import fcntl
        try:
            if time.time() - path.stat().st_mtime >= self._stale_s:
                return False
            with path.open("a+") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                return False
        except OSError:
            return False

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


def _shared_path(name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"thundertalk-{os.getuid()}" / name


DICTATION = DictationPriority(shared_path=_shared_path("dictation.lock"))


class StudioActivity:
    """Preview timing is not a model speed measurement during Studio work.

    Include yielding/loading as well as decoding: the first preview can
    still be waiting for an in-flight span on the same CPU engine.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._count > 0

    @contextmanager
    def running(self):
        with self._lock:
            self._count += 1
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1


STUDIO = StudioActivity()

# One alternate heavy model at a time across Studio jobs (an extra ASR model
# for a transcription, or a music job). Independent of GPU_LOCK: dictation can
# still use Metal between units of work.
class ModelSlot:
    """One alternate model across app and CLI processes; OS releases on crash.

    The child music/translation workers do not take this parent-owned slot.
    Metal's GPU_LOCK remains local because translation RPCs nest it in both
    parent and child. Dictation preempts Compose through the shared lease above.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._file = None

    def acquire(self, blocking: bool = True) -> bool:
        import fcntl
        if not self._lock.acquire(blocking=blocking):
            return False
        file = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            file = self.path.open("a+")
            fcntl.flock(file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self._file = file
            return True
        except BlockingIOError:
            if file:
                file.close()
            self._lock.release()
            return False
        except BaseException:
            if file:
                file.close()
            self._lock.release()
            raise

    def release(self) -> None:
        file, self._file = self._file, None
        if file is None:
            raise RuntimeError("release unlocked model slot")
        try:
            file.close()
        finally:
            self._lock.release()


EXTRA_MODEL = ModelSlot(_shared_path("extra-model.lock"))
