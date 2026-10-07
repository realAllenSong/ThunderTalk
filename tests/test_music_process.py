"""MusicProcess lifetime with a fake spawn context: no torch, no models."""
import threading
from types import SimpleNamespace

import pytest

from thundertalk.core import memory_policy as memory
from thundertalk.core import music_process as mp
from thundertalk.core import music_worker
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.priority import DICTATION, EXTRA_MODEL


class Child:
    """Scripted child: ``script(child)`` runs on every parent poll and may emit events."""

    def __init__(self, script):
        self.script = script
        self.queue = []
        self.polls = 0
        self.go = self.cancel = None
        self.alive = False
        self.terminated = self.killed = self.closed = False
        self.joins = []

    def emit(self, *event):
        self.queue.append(event)
        self.ended = self.ended or event[0] in ("done", "cancelled", "error")

    ended = False


@pytest.fixture
def env(monkeypatch):
    now = [0.0]
    policy = memory.MemoryPolicy(clock=lambda: now[0])
    monkeypatch.setattr(policy, "start", lambda: None)
    monkeypatch.setattr(memory, "POLICY", policy)
    monkeypatch.setattr(memory, "trim_caches", lambda: None)
    state = SimpleNamespace(free=[60], pressure=1, released=True, child=None, now=now, step=0.1)

    def free():
        return state.free.pop(0) if len(state.free) > 1 else state.free[0]

    monkeypatch.setattr(memory, "free_percent", free)
    monkeypatch.setattr(memory, "pressure_level", lambda: state.pressure)
    monkeypatch.setattr(mp, "_release_gpu", lambda: state.released)

    class Connection:
        def __init__(self, child):
            self.child = child
            self.closed = False

        def poll(self, timeout):
            c = self.child
            c.polls += 1
            now[0] += state.step
            if c.alive and not c.queue:
                c.script(c)
            return bool(c.queue)

        def recv(self):
            if not self.child.queue:
                raise EOFError
            return self.child.queue.pop(0)

        def close(self):
            self.closed = True

    class Process:
        def __init__(self, target, args, name, daemon):
            assert target is music_worker._serve and daemon and name == "music"
            state.child.request = args[1]
            state.child.go, state.child.cancel = args[2], args[3]
            self.c = state.child

        pid = 4321

        def start(self):
            self.c.alive = True

        def is_alive(self):
            return self.c.alive

        def join(self, timeout=None):
            self.c.joins.append(timeout)
            if self.c.ended:
                self.c.alive = False

        def terminate(self):
            self.c.terminated = True
            self.c.alive = False

        def kill(self):
            self.c.killed = True
            self.c.alive = False

        def close(self):
            self.c.closed = True

    class Ctx:
        def Pipe(self, duplex):
            assert duplex is False
            return Connection(state.child), SimpleNamespace(close=lambda: None)

        def Event(self):
            return threading.Event()

    Ctx.Process = Process
    contexts = []

    def get_context(kind):
        contexts.append(kind)
        return Ctx()

    monkeypatch.setattr(mp, "multiprocessing", SimpleNamespace(get_context=get_context))
    state.contexts = contexts
    return state


def _run(env, script, cancel=None, **kw):
    env.child = Child(script)
    events = []
    proc = mp.MusicProcess(clock=lambda: env.now[0])
    result = proc.run("request", events.append, cancel or threading.Event(),
                      ram_need_mb=kw.pop("ram_need_mb", 12000), total_mb=kw.pop("total_mb", 65536))
    return result, events, proc


def finishing(child):
    if child.polls == 1:
        child.emit("phase", "load", None)
        child.emit("step", 0, 8)
        child.emit("take", 0, "/tmp/take-1.wav", {"seed": 1})
        child.emit("done")


def test_job_forwards_events_in_order_and_exits(env):
    result, events, proc = _run(env, finishing)
    assert result == "done"
    assert env.contexts == ["spawn"]
    assert env.child.request == "request"
    assert events == [("phase", "load", None), ("step", 0, 8), ("take", 0, "/tmp/take-1.wav", {"seed": 1})]
    assert env.child.joins[0] == mp.EXIT_GRACE_S and env.child.closed
    assert proc.pid is None
    assert EXTRA_MODEL.acquire(blocking=False)
    EXTRA_MODEL.release()


def test_error_event_raises_with_code_after_keeping_takes(env):
    def script(child):
        child.emit("take", 0, "/tmp/a.wav", {})
        child.emit("error", "music:failed", "boom")

    with pytest.raises(mp.MusicError) as err:
        _run(env, script)
    assert err.value.code == "music:failed" and err.value.detail == "boom"


def test_crash_without_end_event_is_worker_stopped(env):
    def script(child):
        child.alive = False

    with pytest.raises(mp.MusicError) as err:
        _run(env, script)
    assert err.value.code == "worker_stopped"


def test_cancel_reaches_child_which_reports_cancelled(env):
    cancel = threading.Event()

    def script(child):
        if child.polls == 2:
            cancel.set()
        if child.cancel.is_set():
            child.emit("cancelled")

    result, _, _ = _run(env, script, cancel=cancel)
    assert result == "cancelled" and not env.child.terminated


def test_unresponsive_child_is_terminated_after_grace(env):
    cancel = threading.Event()
    cancel.set()
    env.step = 1.0
    result, _, _ = _run(env, lambda child: None, cancel=cancel)
    assert result == "cancelled"
    assert env.child.cancel.is_set() and env.child.terminated
    assert env.child.joins[0] == 0.0


def test_dictation_clears_go_and_end_sets_it(env):
    seen = []

    def script(child):
        if child.polls == 1:
            DICTATION.begin()
        elif child.polls == 3:
            DICTATION.end()
        seen.append(child.go.is_set())
        if child.polls == 5:
            child.emit("done")

    try:
        _run(env, script)
    finally:
        while DICTATION.active:
            DICTATION.end()
    # go is evaluated before each poll: set, cleared during dictation, set again.
    assert seen[:5] == [True, False, False, True, True]


def test_another_gpu_user_pauses_the_job(env):
    held, release = threading.Event(), threading.Event()

    def holder():
        with GPU_LOCK:
            held.set()
            release.wait(5)

    seen = []

    def script(child):
        if child.polls == 1:
            threading.Thread(target=holder, daemon=True).start()
            held.wait(5)
        elif child.polls == 3:
            release.set()
            while not GPU_LOCK.acquire(timeout=0.01):
                pass
            GPU_LOCK.release()
        seen.append(child.go.is_set())
        if child.polls == 5:
            child.emit("done")

    _run(env, script)
    assert seen[:5] == [True, False, False, True, True]


def test_two_low_memory_samples_abort(env):
    env.free = [60, 60, 14, 14]
    env.step = 0.5

    with pytest.raises(mp.MusicError) as err:
        _run(env, lambda child: None)
    assert err.value.code == "music:memory_low:14"
    assert env.child.terminated and env.child.joins[0] == 0.0


def test_one_low_sample_only_pauses_and_warns(env):
    env.free = [60, 60, 14, 20, 60]     # admission takes the first sample
    env.step = 1.0
    seen = []

    def script(child):
        seen.append(child.go.is_set())
        if child.polls == 4:
            child.emit("done")

    result, events, _ = _run(env, script)
    assert result == "done"
    assert seen[:4] == [True, False, True, True]
    assert events == [("memory_low_warn", 14)]


def test_critical_pressure_aborts(env):
    env.pressure = memory.PRESSURE_CRITICAL
    env.free = [50]
    with pytest.raises(mp.MusicError) as err:
        _run(env, lambda child: None)
    assert err.value.code == "music:memory_low:50"


def test_stall_watchdog_kills_a_silent_child(env):
    env.step = 10.0
    with pytest.raises(mp.MusicError) as err:
        _run(env, lambda child: None)
    assert err.value.code == "music:stalled"
    assert env.child.terminated


def test_pause_is_not_a_stall(env):
    env.step = 10.0

    def script(child):
        if child.polls == 1:
            DICTATION.begin()
        if child.polls == 30:
            DICTATION.end()
            child.emit("done")

    try:
        result, _, _ = _run(env, script)
    finally:
        while DICTATION.active:
            DICTATION.end()
    assert result == "done"


@pytest.mark.parametrize("free,total,need,code", [
    (39, 65536, 12000, "music:memory:39"),
    (45, 32768, 12000, "music:memory:45"),     # 14.7 GB free − 12 GB < 20 % of 32 GB
])
def test_admission_refuses_low_memory_before_spawn(env, free, total, need, code):
    env.free = [free]
    with pytest.raises(mp.MusicError) as err:
        _run(env, finishing, total_mb=total, ram_need_mb=need)
    assert err.value.code == code
    assert env.contexts == []


def test_busy_gpu_voice_model_is_model_busy(env):
    env.released = False
    with pytest.raises(mp.MusicError) as err:
        _run(env, finishing)
    assert err.value.code == "model_busy" and env.contexts == []


def test_extra_model_slot_taken_is_model_busy(env):
    assert EXTRA_MODEL.acquire(blocking=False)
    try:
        with pytest.raises(mp.MusicError) as err:
            _run(env, finishing)
    finally:
        EXTRA_MODEL.release()
    assert err.value.code == "model_busy" and env.contexts == []


def test_lease_armed_and_idle_release_kills_a_leftover(env):
    _, _, proc = _run(env, finishing)
    entry = memory.POLICY._entries[proc]
    assert entry.armed and not entry.active
    leftover = Child(lambda c: None)
    leftover.alive = True
    proc._process = SimpleNamespace(
        is_alive=lambda: leftover.alive, join=lambda timeout=None: None,
        terminate=lambda: setattr(leftover, "alive", False), kill=lambda: None,
        close=lambda: setattr(leftover, "closed", True))
    env.now[0] += memory.IDLE_SECONDS + 1
    assert memory.POLICY.sweep() == ["MusicProcess"]
    assert not leftover.alive and leftover.closed


def test_total_memory_reads_hw_memsize():
    assert mp.total_memory_mb() > 1024
