"""Audio ducking lifecycle against a fake CoreAudio/AppleScript boundary."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future

import pytest

from thundertalk.core import system_audio as audio


class FakeAudio:
    def __init__(self):
        self.state = {"speakers": {"volm:1": 0.3125, "volm:2": 0.3125, "mute:0": False}}
        self.channel_counts = {"speakers": 2}
        self.selected = "speakers"
        self.members = []
        self.readonly = set()
        self.failed = set()
        self.calls = []
        self.apple = [31, False]
        self.apple_supported = True
        self.before_write = None

    def devices(self):
        return {uid: i for i, uid in enumerate(self.state)}

    def default(self):
        return self.selected

    def route(self):
        return [self.selected, *self.members]

    def channels(self, uid):
        return self.channel_counts.get(uid, 2)

    def controls(self, uid):
        return self.state[uid].copy() if uid in self.state else None

    def writable(self, uid, key):
        return (uid, key) not in self.readonly

    def write(self, uid, key, value):
        self.calls.append((uid, key, value))
        if self.before_write:
            self.before_write(uid, key, value)
        if (uid, key) in self.failed or not self.writable(uid, key):
            return False
        self.state[uid][key] = value
        return True

    def apple_read(self):
        return self.apple.copy() if self.apple_supported else None

    def apple_mute(self, muted):
        self.calls.append(("apple", "mute", muted))
        if self.apple_supported:
            self.apple[1] = muted
        return self.apple_supported

    def apple_volume(self, volume):
        self.calls.append(("apple", "volume", volume))
        self.apple[0] = volume
        # Reproduce why percentage restore must not override exact native scalars.
        for key in self.state.get(self.selected, {}):
            if key.startswith("volm:"):
                self.state[self.selected][key] = volume / 100
        return True


@pytest.fixture
def setup(tmp_path):
    backend = FakeAudio()
    controller = audio._DuckingController(backend, tmp_path / "audio-ducking.json")
    yield backend, controller
    controller.close()


@pytest.mark.parametrize("muted", [False, True])
def test_exact_scalar_and_mute_restore(setup, muted):
    backend, controller = setup
    backend.state["speakers"]["mute:0"] = muted
    original = backend.controls("speakers")
    controller.begin(1)
    assert backend.state["speakers"]["mute:0"] is True
    assert backend.state["speakers"]["volm:1"] == 0.3125
    controller.end(1)
    assert backend.controls("speakers") == original
    assert not any(c[:2] == ("apple", "volume") for c in backend.calls)
    assert not controller.path.exists()


def test_volume_fallback_preserves_channel_balance(setup):
    backend, controller = setup
    backend.state["speakers"] = {"volm:1": 0.3125, "volm:2": 0.28125}
    original = backend.controls("speakers")
    controller.begin(1)
    assert backend.controls("speakers") == {"volm:1": 0.0, "volm:2": 0.0}
    controller.end(1)
    assert backend.controls("speakers") == original


def test_partial_channel_mute_falls_back_to_volume(setup):
    backend, controller = setup
    backend.state["speakers"] = {"volm:1": 0.3125, "volm:2": 0.25, "mute:1": False}
    original = backend.controls("speakers")
    controller.begin(1)
    assert controller._silenced("speakers", backend.controls("speakers"))
    controller.end(1)
    assert backend.controls("speakers") == original


def test_failed_native_mute_falls_back_to_volume(setup):
    backend, controller = setup
    backend.failed.add(("speakers", "mute:0"))
    controller.begin(1)
    assert backend.state["speakers"]["volm:1"] == 0
    controller.end(1)
    assert backend.state["speakers"]["volm:1"] == 0.3125
    assert not controller.saved


def test_rapid_overlapping_and_duplicate_sessions_keep_first_snapshot(setup):
    backend, controller = setup
    original = backend.controls("speakers")
    controller.begin(1)
    controller.begin(1)
    controller.begin(2)
    controller.end(1)
    controller.end(1)
    assert backend.state["speakers"]["mute:0"] is True
    assert controller.saved["speakers"]["original"] == original
    controller.end(2)
    controller.begin(3)
    controller.end(2)  # delayed old callback cannot release generation 3
    assert backend.state["speakers"]["mute:0"] is True
    controller.end(3)
    assert backend.controls("speakers") == original


def test_device_change_and_aggregate_members_restore_each_original(setup):
    backend, controller = setup
    backend.state["aggregate"] = {}
    backend.state["headphones"] = {"volm:0": 0.15625, "mute:0": False}
    original = {uid: c.copy() for uid, c in backend.state.items()}
    controller.begin(1)
    backend.selected = "aggregate"
    backend.members = ["headphones"]
    controller.poll()
    assert backend.state["headphones"]["mute:0"] is True
    assert backend.state["speakers"]["mute:0"] is True
    controller.end(1)
    assert backend.state == original


def test_fixed_volume_without_controls_reports_and_skips(setup, capsys):
    backend, controller = setup
    backend.state["speakers"] = {}
    backend.apple_supported = False
    controller.begin(1)
    assert "cannot be silenced" in capsys.readouterr().out
    assert backend.calls == []
    controller.end(1)
    assert not controller.saved


def test_readonly_controls_do_not_count_as_success(setup, capsys):
    backend, controller = setup
    backend.readonly = {("speakers", k) for k in backend.state["speakers"]}
    backend.apple_supported = False
    controller.begin(1)
    assert "cannot be silenced" in capsys.readouterr().out
    assert not backend.calls
    controller.end(1)


def test_applescript_fallback_mutes_without_rounding_native_volume(setup):
    backend, controller = setup
    backend.failed = {("speakers", k) for k in backend.state["speakers"]}
    controller.begin(1)
    assert backend.apple == [31, True]
    assert backend.state["speakers"]["volm:1"] == 0.3125
    controller.end(1)
    assert backend.apple == [31, False]
    assert backend.state["speakers"]["volm:1"] == 0.3125
    assert not controller.saved


def test_fallback_for_previous_route_never_sets_new_route_volume(setup):
    backend, controller = setup
    backend.state["speakers"] = {}
    controller.begin(1)
    backend.state["hdmi"] = {}
    backend.selected = "hdmi"
    backend.apple = [70, False]
    controller.end(1)
    assert backend.apple == [70, False]
    assert "speakers" in controller.saved  # retained until that UID becomes current
    backend.selected = "speakers"
    backend.apple = [31, True]
    controller.recover()
    assert backend.apple == [31, False]
    assert not controller.saved


@pytest.mark.parametrize("poll_first", [False, True])
def test_user_volume_change_is_kept_but_owned_mute_released(setup, poll_first):
    backend, controller = setup
    controller.begin(1)
    backend.state["speakers"]["volm:1"] = 0.125
    backend.state["speakers"]["volm:2"] = 0.125
    if poll_first:
        controller.poll()
    controller.end(1)
    assert backend.state["speakers"] == {"volm:1": 0.125, "volm:2": 0.125, "mute:0": False}


def test_user_unmute_is_kept_and_not_reasserted(setup):
    backend, controller = setup
    controller.begin(1)
    backend.state["speakers"]["mute:0"] = False
    controller.poll()
    assert backend.state["speakers"]["mute:0"] is False
    controller.end(1)
    assert backend.state["speakers"]["mute:0"] is False


def test_user_volume_change_with_zero_volume_fallback_is_kept(setup):
    backend, controller = setup
    del backend.state["speakers"]["mute:0"]
    controller.begin(1)
    backend.state["speakers"]["volm:1"] = 0.1875
    backend.state["speakers"]["volm:2"] = 0.1875
    controller.end(1)
    assert backend.controls("speakers") == {"volm:1": 0.1875, "volm:2": 0.1875}


def test_snapshot_is_persisted_before_every_mutation(setup):
    backend, controller = setup
    def check_journal(uid, key, value):
        saved = json.loads(controller.path.read_text())["devices"][uid]
        assert saved["original"]["volm:1"] == 0.3125
        assert key in saved["expected"]
    backend.before_write = check_journal
    controller.begin(1)
    controller.end(1)


def test_journal_write_failure_never_mutes(setup, monkeypatch):
    backend, controller = setup
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(controller, "_persist", fail)
    with pytest.raises(OSError):
        controller.begin(1)
    assert backend.state["speakers"]["mute:0"] is False
    assert not backend.calls
    monkeypatch.undo()


def test_crash_recovery_uses_uid_not_transient_device_id(setup):
    backend, controller = setup
    controller.begin(1)
    # Simulate process death: release the advisory lock without running close().
    controller._journal_lock.close()
    replacement = audio._DuckingController(backend, controller.path)
    try:
        replacement.recover()
        assert backend.state["speakers"]["mute:0"] is False
        assert backend.state["speakers"]["volm:1"] == 0.3125
        assert not replacement.path.exists()
    finally:
        replacement.close()
        controller.saved.clear()
        controller.sessions.clear()


def test_disconnected_device_journal_survives_and_recovers_on_return(setup):
    backend, controller = setup
    controller.begin(1)
    disconnected = backend.state.pop("speakers")
    controller.end(1)
    assert controller.path.exists()
    backend.state["speakers"] = disconnected
    controller.recover()
    assert backend.state["speakers"]["mute:0"] is False
    assert not controller.path.exists()


def test_second_instance_cannot_recover_a_live_recording(setup):
    backend, controller = setup
    controller.begin(1)
    other = audio._DuckingController(backend, controller.path)
    with pytest.raises(BlockingIOError):
        other.recover()
    assert backend.state["speakers"]["mute:0"] is True
    controller.end(1)


@pytest.mark.parametrize("error", [False, True])
def test_restore_after_recorder_stop_error_or_cancel(setup, error):
    backend, controller = setup
    executor = audio._SystemAudioExecutor(controller)
    try:
        session = audio.AudioDuckingSession(executor, 1)
        session.ready.result(timeout=1)
        class Recorder:
            def stop(self):
                if error:
                    raise RuntimeError("stop failed")
                return None
        if error:
            with pytest.raises(RuntimeError, match="stop failed"):
                audio.stop_recording_and_restore(Recorder(), session)
        else:
            audio.stop_recording_and_restore(None, session)
        executor.submit(lambda: None).result(timeout=1)
        assert backend.state["speakers"]["mute:0"] is False
    finally:
        executor.shutdown()


def test_executor_orders_restore_before_next_begin_and_quit_waits(setup, monkeypatch):
    backend, controller = setup
    executor = audio._SystemAudioExecutor(controller)
    monkeypatch.setattr(audio, "_system_audio_executor", executor)
    monkeypatch.setattr(audio, "_get_system_audio_executor", lambda: executor)
    try:
        for _ in range(10):
            session = audio.mute_system_audio()
            session.restore()  # no waiting: exercise FIFO serialization
        executor.submit(lambda: None).result(timeout=3)
        assert backend.state["speakers"]["mute:0"] is False
        session = audio.mute_system_audio()
        session.ready.result(timeout=1)
        audio.shutdown_system_audio()
        audio.shutdown_system_audio()  # atexit after aboutToQuit is harmless
        assert not executor._thread.is_alive()
        assert backend.state["speakers"]["mute:0"] is False
        assert not controller.path.exists()
    finally:
        executor.shutdown()


def test_executor_monitors_route_without_blocking_qt(setup):
    backend, controller = setup
    executor = audio._SystemAudioExecutor(controller)
    try:
        session = audio.AudioDuckingSession(executor, 1)
        session.ready.result(timeout=1)
        backend.state["new"] = {"volm:0": 0.125, "mute:0": False}
        backend.selected = "new"
        deadline = time.monotonic() + 1
        while not backend.state["new"]["mute:0"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backend.state["new"]["mute:0"] is True
        session.restore().result(timeout=1)
        assert backend.state["new"]["mute:0"] is False
    finally:
        executor.shutdown()


def test_submit_does_not_wait_for_blocked_os_calls():
    executor = audio._SystemAudioExecutor()
    blocker, started = threading.Event(), threading.Event()
    try:
        def blocked_job():
            started.set()
            blocker.wait(timeout=1)
        first = executor.submit(blocked_job)
        assert started.wait(timeout=0.5)
        t0 = time.perf_counter()
        second = executor.submit(lambda: None)
        assert time.perf_counter() - t0 < 0.05
        assert not second.done()
        blocker.set()
        first.result(timeout=1)
        second.result(timeout=1)
    finally:
        blocker.set()
        executor.shutdown()


def test_public_restore_does_not_acquire_controller_lock(setup, monkeypatch):
    backend, controller = setup
    executor = audio._SystemAudioExecutor(controller)
    monkeypatch.setattr(audio, "_get_system_audio_executor", lambda: executor)
    session = audio.mute_system_audio()
    session.ready.result(timeout=1)
    try:
        with controller.lock:
            t0 = time.perf_counter()
            future = audio.unmute_system_audio(session)
            assert time.perf_counter() - t0 < 0.05
            assert isinstance(future, Future)
        future.result(timeout=1)
        assert backend.state["speakers"]["mute:0"] is False
    finally:
        executor.shutdown()


def test_restore_when_disk_fills_after_mute(setup, monkeypatch):
    backend, controller = setup
    controller.begin(1)
    def full_disk():
        raise OSError("disk full")
    monkeypatch.setattr(controller, "_persist", full_disk)
    controller.end(1)
    assert backend.state["speakers"]["mute:0"] is False
    assert backend.state["speakers"]["volm:1"] == 0.3125
    monkeypatch.undo()


def test_exception_mid_duck_restores_every_touched_channel(setup, monkeypatch):
    backend, controller = setup
    del backend.state["speakers"]["mute:0"]
    original = backend.controls("speakers")
    write = backend.write
    def fail_second_channel(uid, key, value):
        if key == "volm:2" and value == 0:
            raise RuntimeError("driver disconnected")
        return write(uid, key, value)
    monkeypatch.setattr(backend, "write", fail_second_channel)
    with pytest.raises(RuntimeError, match="driver disconnected"):
        controller.begin(1)
    assert backend.controls("speakers") == original
    assert not controller.sessions
    assert not controller.path.exists()


def test_crash_mid_duck_restores_partial_write(setup):
    backend, controller = setup
    del backend.state["speakers"]["mute:0"]
    controller.begin(1)
    saved = controller.saved["speakers"]
    saved["applying"] = True
    # Simulate termination after channel 1 write but before channel 2 write.
    backend.state["speakers"]["volm:2"] = saved["original"]["volm:2"]
    controller._persist()
    controller._journal_lock.close()
    replacement = audio._DuckingController(backend, controller.path)
    try:
        replacement.recover()
        assert backend.state["speakers"] == saved["original"]
        assert not replacement.path.exists()
    finally:
        replacement.close()
        controller.saved.clear()
        controller.sessions.clear()


def test_crash_mid_restore_retries_remaining_channels(setup):
    backend, controller = setup
    del backend.state["speakers"]["mute:0"]
    controller.begin(1)
    saved = controller.saved["speakers"]
    saved["restoring"] = True
    backend.state["speakers"]["volm:1"] = saved["original"]["volm:1"]
    controller._persist()
    controller._journal_lock.close()
    replacement = audio._DuckingController(backend, controller.path)
    try:
        replacement.recover()
        assert backend.state["speakers"] == saved["original"]
        assert not replacement.path.exists()
    finally:
        replacement.close()
        controller.saved.clear()
        controller.sessions.clear()


def test_failed_restore_of_one_device_still_restores_other_device(setup, monkeypatch):
    backend, controller = setup
    controller.begin(1)
    backend.state["new"] = {"volm:0": 0.25, "mute:0": False}
    backend.selected = "new"
    controller.poll()
    write = backend.write
    def fail_first_device(uid, key, value):
        if uid == "speakers" and value is False:
            raise RuntimeError("broken driver")
        return write(uid, key, value)
    monkeypatch.setattr(backend, "write", fail_first_device)
    controller.end(1)
    assert backend.state["new"]["mute:0"] is False
    assert "speakers" in controller.saved
    monkeypatch.undo()
    controller.recover()
    assert backend.state["speakers"]["mute:0"] is False
    assert not controller.saved


def test_restore_readback_failure_retains_journal_even_after_user_volume_change(setup, monkeypatch):
    backend, controller = setup
    controller.begin(1)
    backend.state["speakers"]["volm:1"] = 0.25
    backend.state["speakers"]["volm:2"] = 0.25
    write = backend.write
    def ignored_unmute(uid, key, value):
        if key == "mute:0" and value is False:
            return True  # driver accepts a write but does not apply it
        return write(uid, key, value)
    monkeypatch.setattr(backend, "write", ignored_unmute)
    controller.end(1)
    assert controller.path.exists()
    monkeypatch.undo()
    controller.recover()
    assert backend.state["speakers"]["mute:0"] is False
    assert backend.state["speakers"]["volm:1"] == 0.25
    assert not controller.path.exists()


def test_crash_recovery_preserves_user_volume_adjustment(setup):
    backend, controller = setup
    controller.begin(1)
    backend.state["speakers"]["volm:1"] = 0.25
    backend.state["speakers"]["volm:2"] = 0.25
    controller._journal_lock.close()
    replacement = audio._DuckingController(backend, controller.path)
    try:
        replacement.recover()
        assert backend.state["speakers"] == {"volm:1": 0.25, "volm:2": 0.25, "mute:0": False}
    finally:
        replacement.close()
        controller.saved.clear()
        controller.sessions.clear()


def test_unused_virtual_outputs_are_never_mutated(setup):
    backend, controller = setup
    backend.state["teams"] = {"volm:0": 0.0, "mute:0": True}
    backend.state["wemeet"] = {"volm:0": 0.0, "mute:0": True}
    controller.begin(1)
    controller.end(1)
    assert all(c[0] not in ("teams", "wemeet") for c in backend.calls)
    assert backend.state["teams"] == {"volm:0": 0.0, "mute:0": True}
    assert backend.state["wemeet"] == {"volm:0": 0.0, "mute:0": True}


def test_unused_virtual_output_is_restored_when_it_becomes_default(setup):
    backend, controller = setup
    backend.state["teams"] = {"volm:0": 0.375, "mute:0": False}
    controller.begin(1)
    backend.selected = "teams"
    controller.poll()
    assert backend.state["teams"]["mute:0"] is True
    controller.end(1)
    assert backend.state["teams"] == {"volm:0": 0.375, "mute:0": False}


def test_cancelled_future_never_discards_restore_or_kills_worker(setup):
    backend, controller = setup
    executor = audio._SystemAudioExecutor(controller)
    blocker, started = threading.Event(), threading.Event()
    try:
        session = audio.AudioDuckingSession(executor, 1)
        session.ready.result(timeout=1)
        def block():
            started.set()
            blocker.wait(timeout=1)
        executor.submit(block)
        assert started.wait(timeout=0.5)
        restore_result = session.restore()
        assert restore_result.cancel()
        blocker.set()
        executor.submit(lambda: None).result(timeout=1)
        assert backend.state["speakers"]["mute:0"] is False
        assert executor._thread.is_alive()
    finally:
        blocker.set()
        executor.shutdown()


def test_user_volume_change_during_mute_write_is_preserved(setup):
    backend, controller = setup
    def user_changes_volume(uid, key, value):
        if key == "mute:0" and value is True:
            backend.state[uid]["volm:1"] = 0.125
            backend.state[uid]["volm:2"] = 0.125
    backend.before_write = user_changes_volume
    controller.begin(1)
    controller.end(1)
    assert backend.state["speakers"] == {"volm:1": 0.125, "volm:2": 0.125, "mute:0": False}


def test_master_with_readonly_channel_mirrors_restores_exactly(setup, monkeypatch):
    backend, controller = setup
    backend.state["speakers"] = {"volm:0": 0.3125, "volm:1": 0.3125, "volm:2": 0.3125}
    backend.readonly = {("speakers", "volm:1"), ("speakers", "volm:2")}
    write = backend.write
    def master_write(uid, key, value):
        result = write(uid, key, value)
        if result and key == "volm:0":
            backend.state[uid]["volm:1"] = value
            backend.state[uid]["volm:2"] = value
        return result
    monkeypatch.setattr(backend, "write", master_write)
    controller.begin(1)
    assert backend.state["speakers"]["volm:1"] == 0.0
    controller.end(1)
    assert backend.state["speakers"] == {"volm:0": 0.3125, "volm:1": 0.3125, "volm:2": 0.3125}
    assert not controller.saved


def test_route_switch_inside_applescript_call_has_a_durable_native_snapshot(setup, monkeypatch):
    backend, controller = setup
    backend.state["speakers"] = {}  # must use global AppleScript fallback
    backend.state["new"] = {"volm:0": 0.5, "mute:0": False}
    def switch_and_mute(muted):
        # The switch happens after the caller's default check, inside the OS call.
        persisted = json.loads(controller.path.read_text())["devices"]
        assert persisted["new"]["original"]["mute:0"] is False
        backend.selected = "new"
        backend.apple = [50, muted]
        backend.state["new"]["mute:0"] = muted
        return True
    monkeypatch.setattr(backend, "apple_mute", switch_and_mute)
    controller.begin(1)
    assert backend.state["new"]["mute:0"] is True
    controller.end(1)
    assert backend.state["new"] == {"volm:0": 0.5, "mute:0": False}
    assert not any(c[:2] == ("apple", "volume") for c in backend.calls)
    # AppleScript-only original output is deferred safely until it is current.
    backend.selected = "speakers"
    backend.apple = [31, False]
    controller.recover()
    assert not controller.saved


def test_global_fallback_skips_when_alternate_route_cannot_be_snapshotted(setup, capsys):
    backend, controller = setup
    backend.state["speakers"] = {}
    backend.state["hdmi"] = {}  # no way to snapshot its hidden AppleScript mute
    controller.begin(1)
    assert "alternate output mute state is unreadable" in capsys.readouterr().out
    assert not backend.calls
    controller.end(1)
    assert not controller.saved


def test_crash_inside_global_mute_recovers_readonly_new_route(setup, monkeypatch):
    backend, controller = setup
    backend.state["speakers"] = {}
    backend.state["new"] = {"volm:0": 0.5, "mute:0": False}
    backend.readonly.add(("new", "mute:0"))
    def crash_inside_script(muted):
        backend.selected = "new"
        backend.apple = [50, muted]
        backend.state["new"]["mute:0"] = muted
        if muted:
            raise KeyboardInterrupt("simulated process termination")
        return True
    monkeypatch.setattr(backend, "apple_mute", crash_inside_script)
    # A killed process does not execute its exception/finally cleanup.
    monkeypatch.setattr(controller, "_restore", lambda: None)
    with pytest.raises(KeyboardInterrupt):
        controller.begin(1)
    controller._journal_lock.close()
    replacement = audio._DuckingController(backend, controller.path)
    try:
        replacement.recover()
        assert backend.state["new"] == {"volm:0": 0.5, "mute:0": False}
        assert "new" not in replacement.saved
        backend.selected = "speakers"
        backend.apple = [31, False]
        replacement.recover()
        assert not replacement.saved
    finally:
        replacement.close()
        controller.saved.clear()
        controller.sessions.clear()
