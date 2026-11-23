"""One lock for every MLX/Metal job in the process, and dictation's right of way.

Dictation (Qwen3-ASR), file transcription (MOSS) and speech synthesis all run
on the same GPU from different threads. MLX is not designed for arbitrary
concurrent evaluation of separate models from several threads, so heavy calls
take this lock. Hold it per unit of work (one utterance, one sentence) — not
for a whole long job.

A long unit (a sentence of VoxCPM2 can run 15 s) is made *preemptible*: the
engine marks the region (``mlx_runtime.preemptible``) and calls
``mlx_runtime.checkpoint`` between its own steps (one token, one diffusion
step). Dictation takes the lock through ``DICTATION_GPU``, which first says
it is waiting; the job's next checkpoint hands the GPU over and takes it back
when dictation is done with it. So a background job keeps going while you
talk, and you wait at most one of its steps, never a whole sentence.
"""

from __future__ import annotations

import threading

GPU_LOCK = threading.RLock()


class _Urgent:
    """How many dictation calls are waiting for the GPU right now, and how
    many threads hold it inside a preemptible region."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self.waiting = 0
        self.preemptible = 0

    def wanted(self) -> bool:
        return self.waiting > 0

    def enter(self) -> None:
        with self._cond:
            self.waiting += 1

    def leave(self) -> None:
        with self._cond:
            self.waiting -= 1
            if not self.waiting:
                self._cond.notify_all()

    def wait_none(self, poll: float = 0.05) -> None:
        with self._cond:
            while self.waiting:
                self._cond.wait(poll)

    def holder_steps_aside(self) -> bool:
        return self.preemptible > 0


URGENT = _Urgent()

# Waiting this long for a preemptible job's next step is still a "free" GPU
# for the live preview (one VoxCPM2 / IndexTTS token or LongCat step is well
# under it); anything else holding the lock means the preview skips its tick.
STEP_WAIT_S = 0.3


class _DictationGpu:
    """``GPU_LOCK`` for dictation: the same lock, taken with right of way.

    ``acquire(blocking=False)`` keeps its meaning for the preview and the
    streaming decode ("only if the GPU is free"), except that a preemptible
    job counts as free: it is asked to step aside and given ``STEP_WAIT_S``.
    """

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if GPU_LOCK.acquire(blocking=False):
            return True
        if not blocking and not URGENT.holder_steps_aside():
            return False
        URGENT.enter()
        try:
            if not blocking:
                return GPU_LOCK.acquire(timeout=STEP_WAIT_S)
            return GPU_LOCK.acquire(timeout=timeout) if timeout >= 0 else GPU_LOCK.acquire()
        finally:
            URGENT.leave()

    def release(self) -> None:
        GPU_LOCK.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def _is_owned(self) -> bool:
        return GPU_LOCK._is_owned()


DICTATION_GPU = _DictationGpu()
