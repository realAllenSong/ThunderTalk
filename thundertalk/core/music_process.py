"""Studio ▸ Compose jobs in a disposable process: spawn → load → takes → exit.

Exiting is the unload: torch's MPS heap and MLX buffers go with the process.
The parent never holds a lock dictation takes while the job runs; it only
borrows ``GPU_LOCK`` for admission. Instead the guard loop clears the shared
``go`` event whenever dictation is active, the GPU is in use or memory is low,
and the child's hooks wait on it before every step, every 16 LM tokens and the
VAE decode (``core/music_worker.py``).
"""
from __future__ import annotations

import ctypes
import multiprocessing
import threading
import time
from typing import Callable, Optional

from thundertalk.core import memory_policy
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.memory_policy import managed
from thundertalk.core.priority import DICTATION, EXTRA_MODEL

ADMIT_FREE_PERCENT = 40
ADMIT_LEFT_FRACTION = 0.17  # abort line (15 %) plus a small margin, so a started take is not aborted
WARN_FREE_PERCENT = 25
ABORT_FREE_PERCENT = 15
STALL_S = 180.0
CANCEL_GRACE_S = 10.0
EXIT_GRACE_S = 10.0
KILL_WAIT_S = 10.0
POLL_S = 0.05
MEMORY_EVERY_S = 1.0

Event = tuple
OnEvent = Callable[[Event], None]


class MusicError(RuntimeError):
    """``code`` is ``model_busy``, ``music:memory:<pct>``, ``music:memory_low:<pct>``,
    ``music:stalled``, ``worker_stopped``, ``music:load_failed`` or ``music:failed``."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


def total_memory_mb() -> int:
    try:
        libc = ctypes.CDLL(None)
        value, size = ctypes.c_uint64(0), ctypes.c_size_t(8)
        if libc.sysctlbyname(b"hw.memsize", ctypes.byref(value), ctypes.byref(size), None, 0):
            return 0
        return value.value >> 20
    except (OSError, AttributeError):
        return 0


def gpu_free() -> bool:
    if not GPU_LOCK.acquire(blocking=False):
        return False
    GPU_LOCK.release()
    return True


def _release_gpu() -> bool:
    from thundertalk.core import speech
    return speech.release_gpu()


def admit(ram_need_mb: int, total_mb: int) -> None:
    """Raise ``MusicError`` unless the job may load now (call under ``GPU_LOCK``)."""
    if not _release_gpu():
        raise MusicError("model_busy")
    free = memory_policy.free_percent()
    if free is None:
        return                          # no gauge (tests off-mac): rely on the watchdog
    left_mb = total_mb * free / 100 - ram_need_mb
    if free < ADMIT_FREE_PERCENT or (total_mb > 0 and left_mb < ADMIT_LEFT_FRACTION * total_mb):
        raise MusicError(f"music:memory:{free}")


class MusicProcess:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._process = None
        self._lock = threading.Lock()

    @property
    def pid(self) -> Optional[int]:
        process = self._process
        try:
            return process.pid if process is not None and process.is_alive() else None
        except ValueError:
            return None

    @managed(release="release_idle")
    def run(self, request, on_event: OnEvent, cancel: threading.Event, *,
            ram_need_mb: int, total_mb: Optional[int] = None) -> str:
        """Run one job; returns ``"done"`` or ``"cancelled"``, raises ``MusicError``.
        Every child event is passed to ``on_event`` (finished takes included,
        so a later failure keeps them)."""
        if not EXTRA_MODEL.acquire(blocking=False):
            raise MusicError("model_busy")
        try:
            with self._lock:
                with GPU_LOCK:
                    admit(ram_need_mb, total_mb or total_memory_mb())
                    connection = self._spawn(request)
                self._clean = False
                try:
                    return self._watch(connection, on_event, cancel)
                finally:
                    connection.close()
                    self._stop(EXIT_GRACE_S if self._clean else 0.0)
        finally:
            EXTRA_MODEL.release()

    def _spawn(self, request):
        from thundertalk.core.music_worker import _serve
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        self._go, self._cancel = ctx.Event(), ctx.Event()
        self._process = ctx.Process(target=_serve, args=(child, request, self._go, self._cancel),
                                    name="music", daemon=True)
        try:
            self._process.start()
        except Exception:
            parent.close()
            child.close()
            self._process = None
            raise
        child.close()
        return parent

    def _watch(self, connection, on_event: OnEvent, cancel: threading.Event) -> str:
        now = self._clock()
        last_event = next_memory = now
        cancel_at = None
        low = 0
        warned = False
        while True:
            now = self._clock()
            if now >= next_memory:
                next_memory = now + MEMORY_EVERY_S
                free = memory_policy.free_percent()
                low = low + 1 if free is not None and free < ABORT_FREE_PERCENT else 0
                if low >= 2 or memory_policy.pressure_level() == memory_policy.PRESSURE_CRITICAL:
                    raise MusicError(f"music:memory_low:{free if free is not None else '?'}")
                if free is not None and free < WARN_FREE_PERCENT:
                    if not warned:
                        on_event(("memory_low_warn", free))
                    warned = True
                else:
                    warned = False
            allowed = not DICTATION.active and gpu_free() and low == 0
            if allowed:
                self._go.set()
            else:
                self._go.clear()
                last_event = now            # waiting by design is not a stall
            if cancel.is_set() and cancel_at is None:
                self._cancel.set()
                cancel_at = now
            if cancel_at is not None and now - cancel_at > CANCEL_GRACE_S:
                return "cancelled"
            if now - last_event > STALL_S:
                raise MusicError("music:stalled")
            try:
                event = connection.recv() if connection.poll(POLL_S) else None
            except (EOFError, OSError):
                event = ("eof",)
            if event is None and self._alive():
                continue
            if event is None or event[0] == "eof":
                if cancel_at is not None:
                    return "cancelled"
                raise MusicError("worker_stopped")
            last_event = self._clock()
            kind = event[0]
            if kind in ("done", "cancelled"):
                self._clean = True
                return kind
            if kind == "error":
                self._clean = True
                raise MusicError(event[1], event[2] if len(event) > 2 else "")
            on_event(event)

    def _alive(self) -> bool:
        process = self._process
        return process is not None and process.is_alive()

    def _stop(self, grace: float = 0.0) -> None:
        """Wait ``grace`` for a child that reported its end, then terminate → kill."""
        process, self._process = self._process, None
        if process is None:
            return
        process.join(timeout=grace)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=KILL_WAIT_S)
        if process.is_alive():
            # A killed child can outlive the wait while the kernel frees a large
            # model heap; keep it so the idle sweep reaps it instead of raising here.
            self._process = process
            return
        process.close()

    def release_idle(self) -> bool:
        """Idle sweep: kill a process a crashed job left behind."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._stop()
        finally:
            self._lock.release()
        return True
