"""Idle lifetimes and lock ordering with no native weights or runtimes."""
import gc
import threading
import weakref

import pytest

from thundertalk.core import diarize, memory_policy as memory
from thundertalk.core.asr import AsrEngine
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.translate import TranslationEngine


class Owner:
    def __init__(self):
        self.unloads = 0
        self.pinned = False
        self.available = True

    def unload(self):
        assert GPU_LOCK._is_owned()
        if not self.available:
            return False
        self.unloads += 1

    def is_pinned(self):
        return self.pinned


@pytest.fixture
def policy(monkeypatch):
    now = [0.0]
    p = memory.MemoryPolicy(timeout=180, clock=lambda: now[0])
    monkeypatch.setattr(memory, "trim_caches", lambda: None)
    monkeypatch.setattr(memory, "POLICY", p)
    monkeypatch.setattr(p, "start", lambda: None)
    p.now = now
    return p


def test_lease_protects_long_and_nested_jobs_and_restarts_idle_on_completion(policy):
    o = Owner()
    policy.register(o)
    with policy.using(o):
        policy.now[0] = 200
        with policy.using(o):
            assert policy.sweep() == []
        assert policy.sweep() == []
    policy.now[0] = 379
    assert policy.sweep() == []
    policy.now[0] = 380
    assert policy.sweep() == ["Owner"]
    assert policy.sweep() == [] and o.unloads == 1


def test_failure_cancellation_and_reuse_have_the_same_lifetime(policy):
    o = Owner()
    policy.register(o)
    with pytest.raises(ValueError):
        with policy.using(o):
            raise ValueError("cancel")
    policy.now[0] = 180
    policy.sweep()
    with policy.using(o):
        pass
    policy.now[0] = 360
    policy.sweep()
    assert o.unloads == 2


def test_pinned_and_busy_owners_are_retried_without_losing_registration(policy):
    o = Owner()
    policy.register(o, busy="is_pinned")
    with policy.using(o):
        pass
    policy.now[0] = 180
    o.pinned = True
    assert not policy.sweep()
    o.pinned, o.available = False, False
    assert not policy.sweep()
    o.available = True
    assert policy.sweep() == ["Owner"]


def test_registrations_do_not_keep_model_owners_alive(policy):
    o = Owner()
    policy.register(o)
    ref = weakref.ref(o)
    del o
    gc.collect()
    assert ref() is None and not policy._entries


def test_sweep_never_waits_for_a_dictation_gpu_job(policy):
    held, release = threading.Event(), threading.Event()
    def job():
        with GPU_LOCK:
            held.set()
            assert release.wait(3)
    t = threading.Thread(target=job)
    t.start()
    try:
        assert held.wait(3)
        assert policy.sweep() == []
    finally:
        release.set()
        t.join(3)
    assert not t.is_alive()


def test_translation_idle_keeps_reload_source_and_explicit_off_forgets_it(policy, monkeypatch):
    e = TranslationEngine()
    e.prepare("/downloaded/model")
    assert e.can_translate and not e.is_loaded
    loads = []
    def load(source):
        loads.append(source)
        e._model = object()
        e._processor = object()
    monkeypatch.setattr(e, "load_model", load)
    e._ensure_loaded()
    assert loads == ["/downloaded/model"]
    with memory.using(e, release="release_idle"):
        pass
    policy.now[0] = 180
    policy.sweep()
    assert e.can_translate and not e.is_loaded
    e._ensure_loaded()
    assert loads == ["/downloaded/model"] * 2
    e.unload()
    assert not e.can_translate


def test_moss_dictation_pins_shared_model_and_studio_never_loads_a_copy(policy, monkeypatch):
    model, engine = object(), AsrEngine()
    monkeypatch.setattr(diarize, "_MODEL", model)
    monkeypatch.setattr(diarize, "_PINNED", weakref.WeakSet())
    # Bypass stream setup: this branch only returns existing fake weights.
    assert diarize.load_model.__wrapped__(owner=engine) is model
    assert diarize.load_model.__wrapped__() is model
    policy.now[0] = 180
    assert not policy.sweep()
    assert diarize.unload_model.__wrapped__() is False
    diarize.unpin_model(engine)
    # No native MLX; use the unwrapped callback for the final release.
    monkeypatch.setattr(diarize, "unload_model", diarize.unload_model.__wrapped__)
    policy.sweep()
    assert diarize._MODEL is None
