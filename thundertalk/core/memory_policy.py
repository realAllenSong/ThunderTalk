"""Idle lifetime for on-demand models; dictation is deliberately not registered.

One daemon checks weak registrations every 15 seconds. Leases cover complete
jobs (including loads/cancellation), and restart the grace period on completion.
Reclamation takes Metal first, never waits for it, and never imports a runtime.
Owner callbacks must not wait for a job's private lock: return False to retry.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import gc
import logging
import sys
import threading
import time
import weakref

from thundertalk.core.gpu_lock import GPU_LOCK

IDLE_SECONDS = 180.0
MLX_CACHE_BYTES = 512 << 20
log = logging.getLogger(__name__)


def trim_caches() -> None:
    """Collect model cycles and clear already-imported runtimes on shared streams."""
    with GPU_LOCK:
        mx = sys.modules.get("mlx.core")
        if mx is not None:
            from thundertalk.core.mlx_runtime import mlx_context
            with mlx_context():
                gc.collect()
                mx.clear_cache()
        else:
            gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None and torch.backends.mps.is_available():
            torch.mps.empty_cache()


@dataclass
class _Entry:
    release: str
    busy: str | None
    used: float
    active: int = 0
    armed: bool = False


class MemoryPolicy:
    def __init__(self, timeout=IDLE_SECONDS, clock=time.monotonic):
        self.timeout, self.clock = timeout, clock
        self._entries = weakref.WeakKeyDictionary()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def register(self, owner, release="unload", busy=None) -> None:
        with self._lock:
            if owner not in self._entries:
                self._entries[owner] = _Entry(release, busy, self.clock())

    @contextmanager
    def using(self, owner):
        with self._lock:
            entry = self._entries[owner]
            entry.active += 1
            entry.armed = True
        try:
            yield
        finally:
            with self._lock:
                entry.active -= 1
                entry.used = self.clock()

    def sweep(self) -> list[str]:
        released = []
        if not GPU_LOCK.acquire(blocking=False):
            return released
        try:
            with self._lock:
                for owner, entry in list(self._entries.items()):
                    if (not entry.armed or entry.active or self.clock() - entry.used < self.timeout
                            or (entry.busy and getattr(owner, entry.busy)())):
                        continue
                    try:
                        if getattr(owner, entry.release)() is False:
                            continue
                        entry.armed = False
                        released.append(type(owner).__name__)
                    except Exception:
                        log.exception("Idle release failed for %s", type(owner).__name__)
                if released:
                    trim_caches()
        finally:
            GPU_LOCK.release()
        return released

    def start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="idle-memory", daemon=True)
                self._thread.start()

    def _run(self):
        while not self._stop.wait(15):
            self.sweep()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


POLICY = MemoryPolicy()


@contextmanager
def using(owner, release="unload", busy=None):
    POLICY.register(owner, release, busy)
    POLICY.start()
    with POLICY.using(owner):
        yield


def managed(release="unload", busy=None):
    """Lease a model method through loading, inference, failure and cancellation."""
    def decorate(fn):
        @wraps(fn)
        def call(self, *args, **kwargs):
            with using(self, release, busy):
                return fn(self, *args, **kwargs)
        return call
    return decorate
