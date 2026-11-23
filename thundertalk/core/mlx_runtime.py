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
    from thundertalk.core.memory_policy import MLX_CACHE_BYTES
    mx.set_cache_limit(MLX_CACHE_BYTES)
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
            try:
                mx.clear_cache()
                mx.set_cache_limit(MLX_CACHE_BYTES)
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


@contextmanager
def preemptible(*targets):
    """Mark this thread's MLX work as a background unit that steps aside for
    dictation. ``targets`` are ``(object, attribute)`` pairs: each named
    callable (a sub-model called once per token or diffusion step) gets a
    ``checkpoint()`` before every call, on this instance only, for the
    duration. Nested regions are fine; the outermost one undoes the wrapping."""
    from thundertalk.core.gpu_lock import URGENT
    wrapped = []
    for obj, name in targets:
        inner = getattr(obj, name, None) if obj is not None else None
        if inner is None or isinstance(inner, _Checkpointed):
            continue
        own = vars(obj).get(name, _MISSING)          # an instance attribute to put back, or nothing
        object.__setattr__(obj, name, _Checkpointed(inner))
        wrapped.append((obj, name, own))
    depth = getattr(_LOCAL, "preemptible", 0)
    _LOCAL.preemptible = depth + 1
    if not depth:
        with URGENT._cond:
            URGENT.preemptible += 1
    try:
        yield
    finally:
        _LOCAL.preemptible = depth
        if not depth:
            with URGENT._cond:
                URGENT.preemptible -= 1
        for obj, name, own in reversed(wrapped):
            if own is _MISSING:
                object.__delattr__(obj, name)
            else:
                object.__setattr__(obj, name, own)


_MISSING = object()


class _Checkpointed:
    def __init__(self, inner) -> None:
        self._inner = inner

    def __call__(self, *args, **kwargs):
        checkpoint()
        return self._inner(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@contextmanager
def replaced(obj, name: str, new):
    """``obj.name`` is ``new`` on this instance for the duration (nothing when ``obj`` is None)."""
    if obj is None:
        yield
        return
    own = vars(obj).get(name, _MISSING)
    object.__setattr__(obj, name, new)
    try:
        yield
    finally:
        if own is _MISSING:
            object.__delattr__(obj, name)
        else:
            object.__setattr__(obj, name, own)


def _span(x, axis: int, a: int, b: int):
    return x[(slice(None),) * axis + (slice(a, b),)]


def chunked(fn, *, in_axis: int, out_axis: int, chunk: int, ctx: int, small_chunk: int = 0):
    """A convolutional decoder (vocoder, audio VAE) run over overlapping pieces
    of its time axis, with a ``checkpoint()`` between pieces, so one long
    decode no longer holds the GPU for seconds. Each piece gets ``ctx`` frames
    of context on both sides that are cut off again; with ``ctx`` at least the
    decoder's reach the result equals the one-pass decode. While the user is
    dictating the pieces shrink to ``small_chunk``. A decoder that adds a few
    samples at the end (VoxCPM2's VAE: 8) keeps them on the last piece; one
    with no whole number of samples per frame is decoded in one go."""
    def run(x, *args, **kwargs):
        import mlx.core as mx
        from thundertalk.core.priority import DICTATION
        total = x.shape[in_axis]
        if total <= chunk + 2 * ctx:
            return fn(x, *args, **kwargs)
        outs, start = [], 0
        while start < total:
            size = small_chunk if small_chunk and DICTATION.active else chunk
            a, b = max(0, start - ctx), min(total, start + size + ctx)
            checkpoint()
            y = fn(_span(x, in_axis, a, b), *args, **kwargs)
            mx.eval(y)
            hop = y.shape[out_axis] // (b - a)
            if not hop:
                return fn(x, *args, **kwargs)      # fewer samples than frames: decode in one go
            n = min(size, total - start)
            last = start + size >= total
            outs.append(_span(y, out_axis, (start - a) * hop, None if last else (start - a + n) * hop))
            start += size
        return mx.concatenate(outs, axis=out_axis)
    return run


YIELDS = {"count": 0}


def checkpoint() -> None:
    """Between two steps of a preemptible unit: if dictation is waiting for
    the GPU, finish the queued work, hand the lock over (every level this
    thread holds), and take it back once dictation has it no longer waiting.
    A no-op outside a preemptible region, so dictation itself never yields."""
    from thundertalk.core.gpu_lock import URGENT
    if not getattr(_LOCAL, "preemptible", 0) or not URGENT.wanted() or not GPU_LOCK._is_owned():
        return
    synchronize()
    state = GPU_LOCK._release_save()
    try:
        URGENT.wait_none()
    finally:
        GPU_LOCK._acquire_restore(state)
    YIELDS["count"] += 1


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
