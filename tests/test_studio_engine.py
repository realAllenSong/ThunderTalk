"""Model ownership, memory admission and lock ordering, using fake weights."""

from types import SimpleNamespace
import threading

import numpy as np
import pytest

from thundertalk.core import asr, models, transcribe
from thundertalk.core.asr import AsrEngine, AsrResult
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.priority import DICTATION, STUDIO


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(models, "is_downloaded", lambda _: True)
    monkeypatch.setattr(models, "get_model_path", lambda mid: "/models/" + mid)


@pytest.mark.parametrize("loaded", [False, True])
def test_memory_guard_falls_back_without_constructing_engine(monkeypatch, installed, loaded):
    engine = SimpleNamespace(is_loaded=loaded, current_model="qwen3-asr-06b-int8")
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda _: False)
    monkeypatch.setattr(asr, "AsrEngine", lambda: pytest.fail("Allocated despite low memory"))
    messages = []
    if loaded:
        with transcribe.selected_engine(engine, "qwen3-asr-06b-mlx", lambda p, m: messages.append(m)) as chosen:
            assert chosen is engine
        assert messages == ["memory_fallback"]
    else:
        with pytest.raises(RuntimeError, match="memory"):
            with transcribe.selected_engine(engine, "qwen3-asr-06b-mlx"):
                pytest.fail("No usable fallback")
    assert transcribe._EXTRA_MODEL_LOCK.acquire(blocking=False)
    transcribe._EXTRA_MODEL_LOCK.release()


def test_only_one_extra_model_and_cleanup(monkeypatch, installed):
    class TemporaryEngine:
        def load_model(self, *a, **k):
            pass

        def unload(self):
            pass

    temporary = TemporaryEngine()
    constructions = []
    monkeypatch.setattr(asr, "AsrEngine", lambda: constructions.append(temporary) or temporary)
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda _: True)
    engine = SimpleNamespace(is_loaded=True, current_model="qwen3-asr-06b-int8")
    messages = []
    with transcribe.selected_engine(engine, "qwen3-asr-06b-mlx") as first:
        assert first is temporary
        with transcribe.selected_engine(engine, "sensevoice-small-int8", lambda p, m: messages.append(m)) as second:
            assert second is engine
        assert messages == ["model_busy_fallback"]
    assert constructions == [temporary]


@pytest.mark.parametrize("total,pressure,expected", [
    (32, 60, True), (8, 60, False), (32, 39, False), (32, None, False), (32, 101, False),
])
def test_headroom_requires_bytes_and_mac_pressure(monkeypatch, total, pressure, expected):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        available=3 * 1024**3, total=total * 1024**3))
    monkeypatch.setattr(transcribe.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(transcribe.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=f"System-wide memory free percentage: {pressure}%" if pressure is not None else "unavailable"))
    assert transcribe._has_model_headroom(SimpleNamespace(size_mb=1881)) is expected


@pytest.mark.parametrize("available,expected", [(3, False), (10, False), (16, True)])
def test_headroom_uses_psutil_off_mac(monkeypatch, available, expected):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(
        available=available * 1024**3, total=32 * 1024**3))
    monkeypatch.setattr(transcribe.platform, "system", lambda: "Linux")
    assert transcribe._has_model_headroom(SimpleNamespace(size_mb=1881)) is expected


def test_fallback_transcript_records_actual_model(monkeypatch, installed):
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda _: False)
    monkeypatch.setattr(transcribe, "_transcribe_file", lambda *a, **k: transcribe.Transcript([], 2, "dictation"))
    engine = SimpleNamespace(is_loaded=True, current_model="qwen3-asr-06b-int8")
    tr = transcribe.transcribe_file("fake.wav", engine, model_id="qwen3-asr-06b-mlx")
    assert tr.model_id == "qwen3-asr-06b-int8"
    assert not STUDIO.active


def test_dictation_keeps_its_model_during_alternate_gpu_studio_job(monkeypatch, installed):
    """Real engine locks, fake models: CPU dictation runs during a Metal span."""
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda _: True)
    monkeypatch.setattr(transcribe.audio_io, "decode_audio", lambda *a: np.ones(4 * transcribe.SR, np.float32))
    monkeypatch.setattr(transcribe, "segment_speech", lambda *a, **k: [(0, 2), (2, 4)])
    started, release, yielded = threading.Event(), threading.Event(), threading.Event()
    created, output, errors = [], {}, []

    def load(self, path, family, backend, memory_mode):
        created.append(self)
        self._model_id = path.rsplit("/", 1)[-1]
        self._active_backend = backend
        self._mlx_model = object()

    def decode(self, samples, sr, **kwargs):
        if self._mlx_model is not None:
            assert GPU_LOCK._is_owned()
            started.set()
            assert release.wait(2), "Studio span wasn't released"
        return AsrResult(self.current_model, len(samples) / sr, 1, self.current_model)

    monkeypatch.setattr(AsrEngine, "_load_model", load)
    monkeypatch.setattr(AsrEngine, "_recognize_any", decode)
    engine = AsrEngine()
    engine._recognizer = object()
    engine._model_id = "qwen3-asr-06b-int8"
    engine._active_backend = "onnx"

    def job():
        try:
            output["tr"] = transcribe.transcribe_file("fake.wav", engine, model_id="qwen3-asr-06b-mlx",
                progress=lambda p, m: yielded.set() if m == "yield" else None)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=job, daemon=True)
    worker.start()
    try:
        assert started.wait(2)
        DICTATION.begin()
        # Studio currently holds Metal. This recognizes on the original CPU model.
        result = engine.recognize(np.ones(transcribe.SR, np.float32))
        assert result.text == result.model == "qwen3-asr-06b-int8"
        release.set()
        assert yielded.wait(2), "Studio failed to yield between spans"
    finally:
        release.set()
        DICTATION.end()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert output["tr"].model_id == "qwen3-asr-06b-mlx"
    assert engine.is_loaded and engine.current_model == "qwen3-asr-06b-int8"
    assert len(created) == 1 and created[0] is not engine and not created[0].is_loaded


def test_cpu_recognize_waits_for_complete_load_on_its_own_engine(monkeypatch):
    engine = AsrEngine()
    partial, finish, entered, done = [threading.Event() for _ in range(4)]
    errors = []

    def load(*a):
        engine._recognizer = object()  # half initialized
        partial.set()
        assert finish.wait(2)
        engine._model_id = "complete"

    def decode(*a, **k):
        assert engine.current_model == "complete"
        return AsrResult("ok", 1, 1, "complete")

    def recognize():
        entered.set()
        try:
            engine.recognize(np.ones(transcribe.SR, np.float32))
            done.set()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(engine, "_load_model", load)
    monkeypatch.setattr(engine, "_recognize_any", decode)
    loader = threading.Thread(target=lambda: engine.load_model("fake", "Qwen3-ASR"), daemon=True)
    decoder = threading.Thread(target=recognize, daemon=True)
    loader.start()
    try:
        assert partial.wait(1)
        decoder.start()
        assert entered.wait(1) and not done.wait(0.05)
        # CPU initialization also doesn't occupy the global GPU lock.
        assert GPU_LOCK.acquire(blocking=False)
        GPU_LOCK.release()
    finally:
        finish.set()
        loader.join(2)
        if decoder.ident:
            decoder.join(2)
    assert not loader.is_alive() and not decoder.is_alive() and not errors and done.is_set()


def test_failed_load_leaves_engine_unloaded(monkeypatch):
    engine = AsrEngine()
    def load(*a):
        engine._recognizer = object()
        raise ValueError("bad weights")
    monkeypatch.setattr(engine, "_load_model", load)
    with pytest.raises(ValueError, match="bad weights"):
        engine.load_model("fake", "Qwen3-ASR")
    assert not engine.is_loaded
    with pytest.raises(RuntimeError, match="No model"):
        engine.recognize(np.ones(transcribe.SR, np.float32))


def test_metal_is_acquired_before_state_even_with_external_lock_owner(monkeypatch):
    # A local lock keeps a regression bounded without wedging the suite's GPU.
    lock = threading.RLock()
    monkeypatch.setattr(asr, "GPU_LOCK", lock)
    engine = AsrEngine()
    engine._mlx_model = object()
    held, waiting, decode_now, decoded = [threading.Event() for _ in range(4)]
    errors = []
    monkeypatch.setattr(engine, "_recognize_any", lambda *a, **k: AsrResult("ok", 1, 1, "mlx"))
    # Fake weights require no actual Metal cleanup.
    monkeypatch.setattr(engine, "_unload", lambda: setattr(engine, "_mlx_model", None))

    def owner():
        try:
            with lock:
                held.set()
                assert decode_now.wait(1)
                engine.recognize(np.ones(transcribe.SR, np.float32))
                decoded.set()
        except BaseException as exc:
            errors.append(exc)

    def unload():
        waiting.set()
        engine.unload()

    owner_thread = threading.Thread(target=owner, daemon=True)
    unload_thread = threading.Thread(target=unload, daemon=True)
    owner_thread.start()
    assert held.wait(1)
    unload_thread.start()
    assert waiting.wait(1)
    # Simulate AsrWorker, which already holds Metal when it calls recognize.
    decode_now.set()
    assert decoded.wait(1), "Lock order inversion blocked dictation"
    owner_thread.join(1)
    unload_thread.join(1)
    assert not errors and not owner_thread.is_alive() and not unload_thread.is_alive()


def test_preview_activity_clears_when_studio_fails(monkeypatch):
    def fail(*a, **k):
        assert STUDIO.active
        raise ValueError("decode failed")
    monkeypatch.setattr(transcribe, "_transcribe_file", fail)
    with pytest.raises(ValueError, match="decode failed"):
        transcribe.transcribe_file("bad.wav", None)
    assert not STUDIO.active
