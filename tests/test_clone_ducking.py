"""Studio recording uses the same session lifecycle, without audio hardware."""
from concurrent.futures import Future

import numpy as np
import pytest

from thundertalk.ui.studio import clone_dialog as clone
from thundertalk.core import voices


class FakeSession:
    def __init__(self):
        self.ready = Future()
        self.restores = 0
        self.refreshes = 0

    def restore(self):
        self.restores += 1

    def refresh(self):
        self.refreshes += 1


class FakeRecorder:
    def __init__(self, **kwargs):
        self.is_recording = False
        self.current_rms = 0

    def start(self, mic):
        self.is_recording = True

    def stop(self):
        self.is_recording = False
        return np.zeros(10)


@pytest.fixture
def dialog(qapp, isolated_home, monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(clone, "mute_system_audio", lambda: session)
    monkeypatch.setattr(clone, "AudioRecorder", FakeRecorder)
    dlg = clone.CloneDialog(None, None, voices.VoiceLibrary())
    yield dlg, session
    dlg.reject()


def test_waits_for_mute_before_capture_then_restores_on_cancel(dialog, qapp):
    dlg, session = dialog
    dlg._toggle_record()
    assert dlg._recorder is None
    session.ready.set_result(None)
    qapp.processEvents()
    assert dlg._recorder.is_recording
    assert session.refreshes == 1
    dlg.reject()
    assert session.restores == 1


def test_cancel_before_mute_completion_never_starts_microphone(dialog, qapp):
    dlg, session = dialog
    dlg._toggle_record()
    dlg.reject()
    session.ready.set_result(None)
    qapp.processEvents()
    assert dlg._recorder is None
    assert session.restores == 1


def test_start_error_releases_session(dialog, qapp, monkeypatch):
    dlg, session = dialog
    def fail(self, mic):
        raise RuntimeError("no microphone")
    monkeypatch.setattr(FakeRecorder, "start", fail)
    dlg._toggle_record()
    session.ready.set_result(None)
    qapp.processEvents()
    assert dlg._recorder is None
    assert session.restores == 1


def test_stop_error_releases_session(dialog, qapp, monkeypatch):
    dlg, session = dialog
    dlg._toggle_record()
    session.ready.set_result(None)
    qapp.processEvents()
    def fail(self):
        raise RuntimeError("stop error")
    monkeypatch.setattr(FakeRecorder, "stop", fail)
    with pytest.raises(RuntimeError, match="stop error"):
        dlg._stop_recording(discard=True)
    assert session.restores == 1


def test_mute_setting_off_uses_no_session(qapp, isolated_home, monkeypatch):
    monkeypatch.setattr(clone, "AudioRecorder", FakeRecorder)
    monkeypatch.setattr(clone, "mute_system_audio", lambda: pytest.fail("mute disabled"))
    dlg = clone.CloneDialog(None, None, voices.VoiceLibrary(), mute_speakers=False)
    try:
        dlg._toggle_record()
        assert dlg._recorder.is_recording
        assert dlg._ducking_session is None
    finally:
        dlg.reject()


def test_mute_request_error_does_not_leave_starting_state(dialog, monkeypatch):
    dlg, session = dialog
    def fail():
        raise RuntimeError("CoreAudio unavailable")
    monkeypatch.setattr(clone, "mute_system_audio", fail)
    dlg._toggle_record()
    assert not dlg._starting
    assert dlg._recorder is None
    assert session.restores == 0


def test_mute_future_error_releases_session_and_never_captures(dialog, qapp):
    dlg, session = dialog
    dlg._toggle_record()
    session.ready.set_exception(OSError("journal write failed"))
    qapp.processEvents()
    assert not dlg._starting
    assert dlg._recorder is None
    assert session.restores == 1


def test_close_still_finishes_when_recorder_stop_raises(dialog, qapp, monkeypatch):
    dlg, session = dialog
    dlg._toggle_record()
    session.ready.set_result(None)
    qapp.processEvents()
    def fail(self):
        raise RuntimeError("stop error")
    monkeypatch.setattr(FakeRecorder, "stop", fail)
    dlg.reject()
    assert session.restores == 1
    assert dlg._recorder is None
