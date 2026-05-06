"""Dedicated worker thread for ALL PortAudio interactions.

Background
----------
macOS's CoreAudio HAL holds internal recursive mutexes during device
state changes (Bluetooth A2DP↔HFP profile switches, hot-unplug, sample-
rate negotiation). PortAudio's ``Pa_StopStream``, ``Pa_Terminate``,
``Pa_OpenStream``, and friends all route through that HAL and can block
on those mutexes for hours when the device gets into a bad state.

When such a call originates from the Qt main thread, the entire GUI
freezes. v1.1.12 patched ``stream.stop()`` specifically with an inline
watchdog; v1.1.13 generalises that pattern: ALL PortAudio calls run on
a single dedicated daemon thread, and callers wait with a bounded
timeout instead of a blocking system call.

If a job hangs, the watchdog releases the caller and the worker stays
stuck inside the wedged PA call. Subsequent jobs queue up behind it and
will also time out — that is fine: at this point the OS-level audio
state is unrecoverable from inside the process, and the only correct
behaviour is to keep the GUI responsive so the user can quit cleanly.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional


class AudioCallTimeout(Exception):
    """Raised when an enqueued PortAudio call did not finish in time."""


class _Job:
    __slots__ = ("fn", "result", "exc", "done")

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn: Callable[[], Any] = fn
        self.result: Any = None
        self.exc: Optional[BaseException] = None
        self.done = threading.Event()


class AudioExecutor:
    """Single-threaded executor that owns every PortAudio interaction.

    Submit work via ``call(fn, timeout=2.0)``. The function runs on the
    worker thread; the caller blocks at most ``timeout`` seconds. On
    timeout, ``AudioCallTimeout`` is raised and the job is abandoned.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="audio-executor", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                job.result = job.fn()
            except BaseException as exc:
                job.exc = exc
            finally:
                job.done.set()

    def call(self, fn: Callable[[], Any], timeout: float = 2.0) -> Any:
        job = _Job(fn)
        self._q.put(job)
        if not job.done.wait(timeout=timeout):
            raise AudioCallTimeout(
                f"audio call did not complete within {timeout:.1f}s "
                "(CoreAudio HAL likely wedged); job abandoned"
            )
        if job.exc is not None:
            raise job.exc
        return job.result

    def shutdown(self) -> None:
        """Best-effort: ask the worker to exit. Ignored if it's stuck."""
        self._q.put(None)


_default: Optional[AudioExecutor] = None


def get_executor() -> AudioExecutor:
    """Module-level singleton. First call lazily spins up the worker."""
    global _default
    if _default is None:
        _default = AudioExecutor()
    return _default
