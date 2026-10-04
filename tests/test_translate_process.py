"""Disposable translation lifetime with fake pipes, never torch or models."""
from types import SimpleNamespace
import threading

import pytest

from thundertalk.core import memory_policy as memory, translate_process as remote
from thundertalk.core import i18n
from thundertalk.core.asr import AsrEngine


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(i18n, "LANG", "en")
    now = [0.0]
    policy = memory.MemoryPolicy(clock=lambda: now[0])
    monkeypatch.setattr(policy, "start", lambda: None)
    monkeypatch.setattr(memory, "POLICY", policy)
    monkeypatch.setattr(memory, "trim_caches", lambda: None)
    created = []

    class Connection:
        closed = False
        gate = None
        started = None
        error = None
        def send(self, request):
            self.request = request
        def recv(self):
            if self.request[0] != "load_model" and self.gate is not None:
                self.started.set()
                assert self.gate.wait(3)
            if self.error:
                raise self.error
            return True, self.request[0]
        def close(self):
            self.closed = True

    class Process:
        pid = 123
        alive = False
        closed = False
        def __init__(self, **kwargs):
            assert kwargs["daemon"] and kwargs["target"] is remote._serve
            created.append(self)
        def start(self):
            self.alive = True
        def is_alive(self):
            return self.alive
        def terminate(self):
            self.alive = False
        def join(self, timeout):
            pass
        def close(self):
            self.closed = True

    connections = []
    def pipe():
        parent, child = Connection(), Connection()
        connections.append(parent)
        return parent, child
    def context(mode):
        assert mode == "spawn"
        return SimpleNamespace(Pipe=pipe, Process=Process)
    monkeypatch.setattr(remote.multiprocessing, "get_context", context)
    engine = remote.TranslationProcess()
    yield engine, policy, now, created, connections
    engine.unload()


def test_prepare_is_light_and_idle_exit_reloads_only_on_request(worker):
    e, policy, now, created, connections = worker
    e.prepare("/models/seamless")
    assert e.can_translate and not e.is_loaded and not created
    assert e.translate_text("你好", "cmn", "eng") == "translate_text"
    assert e.current_model == "seamless" and len(created) == 1
    assert e.translate_text("hello", "eng", "spa") == "translate_text"
    assert len(created) == 1
    now[0] = 180
    assert policy.sweep() == ["TranslationProcess"]
    assert created[0].closed and connections[0].closed
    assert e.can_translate and not e.is_loaded
    e.translate_text("again", "eng", "spa")
    assert len(created) == 2
    e.unload()
    assert not e.can_translate


def test_crashed_worker_is_reaped_and_next_request_retries(worker):
    e, _, _, created, connections = worker
    e.load_model("/models/seamless")
    connections[0].error = EOFError()
    with pytest.raises(RuntimeError, match="worker stopped"):
        e.translate_text("hello", "eng", "spa")
    assert created[0].closed and not e.is_loaded and e.can_translate
    e.translate_text("retry", "eng", "spa")
    assert len(created) == 2


def test_active_translation_survives_idle_sweep_and_cpu_dictation_runs(worker, monkeypatch):
    e, policy, now, created, connections = worker
    e.load_model("/models/seamless")
    c = connections[0]
    c.started, c.gate = threading.Event(), threading.Event()
    errors = []
    def translate():
        try:
            e.translate_text("hello", "eng", "spa")
        except Exception as exc:
            errors.append(exc)
    t = threading.Thread(target=translate)
    t.start()
    try:
        assert c.started.wait(3)
        now[0] = 500
        assert policy.sweep() == [] and created[0].alive
        asr = AsrEngine()
        asr._recognizer = object()
        monkeypatch.setattr(asr, "_recognize_any", lambda *a, **k: SimpleNamespace(text=""))
        assert asr.recognize([]).text == ""
    finally:
        c.gate.set()
        t.join(3)
    assert not t.is_alive() and not errors
    now[0] = 680
    assert policy.sweep() == ["TranslationProcess"]
