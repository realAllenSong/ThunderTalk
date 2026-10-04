"""Thread/stream contracts without models, Metal jobs, or NumPy native aborts."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sys
import threading

import numpy as np
import pytest

from thundertalk.core import mlx_runtime as runtime, speech
from thundertalk.core.gpu_lock import GPU_LOCK


@pytest.fixture
def mx(monkeypatch):
    defaults = threading.local()
    streams, events = [], []

    def current(device):
        if not hasattr(defaults, "values"):
            defaults.values = {"cpu": ("cpu", threading.get_ident()), "gpu": ("gpu", threading.get_ident())}
        return defaults.values[device]

    def new(device):
        assert GPU_LOCK._is_owned()
        stream = (device, len(streams))
        streams.append(stream)
        return stream

    def set_default(stream):
        current(stream[0])
        defaults.values[stream[0]] = stream

    def synchronize(stream):
        assert GPU_LOCK._is_owned()
        events.append(("sync", stream))

    fake = SimpleNamespace(cpu="cpu", gpu="gpu", new_thread_unsafe_stream=new,
        default_stream=current, set_default_stream=set_default, synchronize=synchronize,
        events=events, streams=streams)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=fake))
    monkeypatch.setitem(sys.modules, "mlx.core", fake)
    monkeypatch.setattr(runtime, "_STREAMS", None)
    yield fake


def test_lazy_cpu_and_gpu_graphs_reuse_shareable_streams_across_workers(mx):
    created = threading.Event()
    release = threading.Event()
    cached = {}

    @runtime.serialized_mlx
    def produce():
        cached["streams"] = (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu))

    def producer():
        previous = (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu))
        produce()
        assert (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu)) == previous
        created.set()
        assert release.wait(3)

    @runtime.serialized_mlx
    def consume():
        assert GPU_LOCK._is_owned()
        assert (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu)) == cached["streams"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(producer)
        assert created.wait(3)
        try:
            pool.submit(consume).result(timeout=3)
        finally:
            release.set()
        first.result(timeout=3)
    assert len(mx.streams) == 2
    assert len(mx.events) == 4


def test_nested_calls_restore_defaults_and_drain_on_failure(mx):
    previous = (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu))
    @runtime.serialized_mlx
    def inner():
        raise ValueError("cancelled")
    @runtime.serialized_mlx
    def outer():
        inner()
    with pytest.raises(ValueError):
        outer()
    assert (mx.default_stream(mx.cpu), mx.default_stream(mx.gpu)) == previous
    assert len(mx.events) == 2 and not GPU_LOCK._is_owned()


def test_external_lock_count_allows_moss_token_handoff(mx):
    @runtime.serialized_mlx
    def suspended():
        GPU_LOCK.release()
        try:
            # Another thread must acquire Metal while this generator is paused.
            with ThreadPoolExecutor(max_workers=1) as pool:
                def dictate():
                    with GPU_LOCK:
                        return True
                assert pool.submit(dictate).result(timeout=3)
        finally:
            GPU_LOCK.acquire()
    with GPU_LOCK:
        suspended()
    assert not GPU_LOCK._is_owned()


def test_speech_converts_lazy_audio_while_producing_stream_and_lock_are_active(mx):
    class LazyAudio:
        def __array__(self, dtype=None):
            assert GPU_LOCK._is_owned()
            assert mx.default_stream(mx.cpu) == mx.streams[0]
            return np.ones(4, dtype=dtype)
    backend = SimpleNamespace(info=SimpleNamespace(needs_gpu=True),
                              generate=lambda *a, **kw: LazyAudio())
    assert speech.SpeechEngine()._gen(backend, "hi", "voice", "en", 0, 1, {}).tolist() == [1] * 4


def test_context_refuses_unlocked_mlx_work(mx):
    with pytest.raises(RuntimeError, match="GPU_LOCK"):
        with runtime.mlx_context():
            pass


def test_moss_cancel_closes_generator_and_drains_before_releasing_lock(monkeypatch, mx):
    from thundertalk.core import diarize
    closed, synced = [], []
    class Model:
        def stream_generate(self, audio, max_tokens):
            try:
                yield 1, None
                yield 2, None
            finally:
                closed.append(True)
    monkeypatch.setattr(diarize, "load_model", lambda: Model())
    monkeypatch.setattr(diarize, "synchronize", lambda: synced.append(GPU_LOCK._is_owned()))
    def cancel():
        raise ValueError("cancelled")
    with pytest.raises(ValueError, match="cancelled"):
        diarize.transcribe(np.zeros(16, np.float32), between_tokens=cancel)
    assert closed == [True] and synced == [True, True]
    assert not diarize._MODEL_LOCK.locked() and not GPU_LOCK._is_owned()


@pytest.mark.parametrize("fail", [False, True])
def test_moss_nonstream_generation_drains_lookahead_on_return_and_error(monkeypatch, mx, fail):
    from thundertalk.core import diarize
    synced = []
    class Model:
        def generate(self, audio, max_tokens):
            if fail:
                raise ValueError("decode failed")
            return SimpleNamespace(text="[0.0][S01]hello[1.0]")
    monkeypatch.setattr(diarize, "load_model", lambda: Model())
    monkeypatch.setattr(diarize, "synchronize", lambda: synced.append(GPU_LOCK._is_owned()))
    if fail:
        with pytest.raises(ValueError, match="decode failed"):
            diarize.transcribe(np.zeros(16, np.float32))
    else:
        assert diarize.transcribe(np.zeros(16, np.float32))[0].text == "hello"
    assert synced == [True] and not diarize._MODEL_LOCK.locked()
