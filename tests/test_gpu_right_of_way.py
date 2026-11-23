"""Dictation's right of way on the GPU: a preemptible job steps aside within one
of its steps and carries on afterwards
nothing else changes for anyone else."""
import threading
import time

from thundertalk.core import gpu_lock, mlx_runtime
from thundertalk.core.gpu_lock import DICTATION_GPU, GPU_LOCK, URGENT


def _job(steps, step_s, log, stop, *, nested=False):
    """A background unit holding the GPU for many short steps."""
    with GPU_LOCK, mlx_runtime.preemptible():
        if nested:
            GPU_LOCK.acquire()
        try:
            for i in range(steps):
                if stop.is_set():
                    break
                mlx_runtime.checkpoint()
                log.append(("step", i, time.monotonic()))
                time.sleep(step_s)
        finally:
            if nested:
                GPU_LOCK.release()


def test_dictation_waits_one_step_not_the_whole_unit_and_the_job_carries_on():
    log, stop = [], threading.Event()
    t = threading.Thread(target=_job, args=(200, 0.02, log, stop), kwargs={"nested": True})
    t.start()
    time.sleep(0.15)
    t0 = time.monotonic()
    with DICTATION_GPU:
        waited = time.monotonic() - t0
        steps_while_dictating = len(log)
        time.sleep(0.2)
        assert len(log) == steps_while_dictating           # the job is really paused
    time.sleep(0.15)
    assert len(log) > steps_while_dictating                 # and goes on by itself
    stop.set()
    t.join(5)
    assert waited < 0.1, waited                             # one 20 ms step, not 4 s of job
    assert not GPU_LOCK._is_owned()


def test_the_preview_skips_when_the_holder_cannot_step_aside():
    held, release = threading.Event(), threading.Event()

    def plain_holder():
        with GPU_LOCK:
            held.set()
            release.wait(5)

    t = threading.Thread(target=plain_holder)
    t.start()
    held.wait(5)
    t0 = time.monotonic()
    assert DICTATION_GPU.acquire(blocking=False) is False
    assert time.monotonic() - t0 < 0.05                     # no waiting behind a non-preemptible unit
    release.set()
    t.join(5)


def test_the_preview_gets_a_turn_from_a_preemptible_job():
    log, stop = [], threading.Event()
    t = threading.Thread(target=_job, args=(500, 0.02, log, stop))
    t.start()
    time.sleep(0.1)
    try:
        assert DICTATION_GPU.acquire(blocking=False) is True
        DICTATION_GPU.release()
    finally:
        stop.set()
        t.join(5)


def test_checkpoint_is_a_no_op_outside_a_region_or_when_nobody_waits():
    with GPU_LOCK:
        mlx_runtime.checkpoint()                            # not preemptible: dictation never yields to itself
        with mlx_runtime.preemptible():
            n = mlx_runtime.YIELDS["count"]
            mlx_runtime.checkpoint()
            assert mlx_runtime.YIELDS["count"] == n
    assert URGENT.waiting == 0 and URGENT.preemptible == 0


class _Model(dict):                                        # stands in for an mlx.nn.Module
    def __init__(self):
        super().__init__(step=lambda x: x + 1)

    def __getattr__(self, name):
        return self[name]


class _Plain:
    def __init__(self):
        self.step = lambda x: x * 2


def test_wrapping_is_per_instance_and_undone():
    m, p = _Model(), _Plain()
    calls = []
    orig = mlx_runtime.checkpoint
    mlx_runtime.checkpoint = lambda: calls.append(1)
    try:
        with GPU_LOCK, mlx_runtime.preemptible((m, "step"), (p, "step"), (None, "x")):
            assert m.step(1) == 2 and p.step(2) == 4 and len(calls) == 2
    finally:
        mlx_runtime.checkpoint = orig
    assert "step" not in vars(m) and m.step(1) == 2
    assert not isinstance(p.step, mlx_runtime._Checkpointed) and p.step(2) == 4
    assert _Model().step(1) == 2                            # other instances never wrapped


def test_step_wait_is_short():
    assert gpu_lock.STEP_WAIT_S <= 0.3


def _decoder(radius=3, hop=4):
    """A stand-in vocoder: each output frame depends on input frames within ``radius``."""
    import mlx.core as mx

    def fn(x):                                             # x: (1, C, T) → (1, 1, T*hop)
        k = 2 * radius + 1
        pad = mx.pad(x, [(0, 0), (0, 0), (radius, radius)], mode="edge")
        avg = sum(pad[:, :, i:i + x.shape[2]] for i in range(k)) / k
        y = mx.repeat(avg.sum(axis=1, keepdims=True), hop, axis=2)
        return y
    return fn


def test_chunked_decode_equals_one_pass_when_the_context_covers_the_reach():
    import mlx.core as mx
    import numpy as np
    x = mx.array(np.random.default_rng(0).standard_normal((1, 5, 300)).astype(np.float32))
    fn = _decoder(radius=3)
    with GPU_LOCK:
        full = np.asarray(fn(x))
        exact = np.asarray(mlx_runtime.chunked(fn, in_axis=2, out_axis=2, chunk=64, ctx=8)(x))
        short = np.asarray(mlx_runtime.chunked(fn, in_axis=2, out_axis=2, chunk=64, ctx=1)(x))
    assert exact.shape == full.shape and np.allclose(exact, full, atol=1e-5)
    assert not np.allclose(short, full, atol=1e-5)                 # too little context shows at the seams


def test_chunked_decode_keeps_a_decoders_trailing_samples_and_skips_short_input():
    import mlx.core as mx
    calls = []

    def tail8(x):                                          # like VoxCPM2's VAE: 8 samples more at the end
        calls.append(x.shape[1])
        return mx.zeros((1, x.shape[1] * 4 + 8))
    x = mx.zeros((1, 300, 2))
    with GPU_LOCK:
        y = mlx_runtime.chunked(tail8, in_axis=1, out_axis=1, chunk=64, ctx=8)(x)
        assert y.shape[1] == 1208 and max(calls) < 300                    # in pieces, same length
        calls.clear()
        mlx_runtime.chunked(tail8, in_axis=1, out_axis=1, chunk=400, ctx=8)(x)
        assert calls == [300]


def test_replaced_puts_the_attribute_back():
    p = _Plain()
    before = p.step
    with mlx_runtime.replaced(p, "step", lambda x: 0):
        assert p.step(5) == 0
    assert p.step is before
