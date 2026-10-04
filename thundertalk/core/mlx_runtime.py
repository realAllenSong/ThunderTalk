"""Streams for serialized MLX work that moves between app worker threads.

MLX 0.32 ordinary streams have thread-owned command encoders. Cached lazy
DSP arrays and compiled graphs retain their producing stream even after a
new thread installs its own defaults. Use MLX's explicitly shareable streams
under GPU_LOCK for both CPU and Metal, including imports/load and conversion.
No lazy output may escape to UI/audio threads: return Python/NumPy values.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import threading

from thundertalk.core.gpu_lock import GPU_LOCK

_STREAMS = None
_LOCAL = threading.local()


@contextmanager
def mlx_context():
    """Caller owns GPU_LOCK, including while evaluating CPU MLX graphs."""
    global _STREAMS
    if not GPU_LOCK._is_owned():
        raise RuntimeError("MLX context requires GPU_LOCK")
    if getattr(_LOCAL, "depth", 0):
        _LOCAL.depth += 1
        try:
            yield
        finally:
            _LOCAL.depth -= 1
        return
    import mlx.core as mx
    if _STREAMS is None:
        # Unlike new_stream, these encoders can be used from different threads.
        # The application, rather than MLX, must serialize access to them.
        _STREAMS = (mx.new_thread_unsafe_stream(mx.cpu), mx.new_thread_unsafe_stream(mx.gpu))
    previous = (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu))
    for stream in _STREAMS:
        mx.set_default_stream(stream)
    _LOCAL.depth = 1
    try:
        yield
    finally:
        try:
            synchronize()
        finally:
            _LOCAL.depth = 0
            for stream in previous:
                mx.set_default_stream(stream)


def synchronize() -> None:
    """Drain shared queues before a suspended MOSS job hands Metal to dictation."""
    if not GPU_LOCK._is_owned():
        raise RuntimeError("MLX synchronization requires GPU_LOCK")
    if _STREAMS is not None:
        import mlx.core as mx
        for stream in _STREAMS:
            mx.synchronize(stream)


def serialized_mlx(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        # Do not increase an external owner's recursion count: Studio MOSS
        # releases exactly that hold between tokens to let dictation run.
        acquired = not GPU_LOCK._is_owned()
        if acquired:
            GPU_LOCK.acquire()
        try:
            with mlx_context():
                return fn(*args, **kwargs)
        finally:
            if acquired:
                GPU_LOCK.release()
    return run


def evaluate_model(model) -> None:
    """Materialize weights on the load thread before publishing the model."""
    import mlx.core as mx
    mx.eval(model.parameters())
