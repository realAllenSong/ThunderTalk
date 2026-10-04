"""Quit must release the session during active capture and the delayed tail."""
from types import SimpleNamespace

import pytest

from tests.test_system_audio import FakeAudio
from thundertalk.core import system_audio as audio


@pytest.mark.parametrize('owner', ['_ducking_session', '_stopping_session'])
@pytest.mark.parametrize('stop_error', [False, True])
def test_quit_marks_microphone_close_and_restores_active_or_tail_owner(
        qapp, tmp_path, monkeypatch, owner, stop_error):
    from thundertalk import app
    backend = FakeAudio()
    original = backend.controls('speakers')
    controller = audio._DuckingController(backend, tmp_path / 'state.json')
    executor = audio._SystemAudioExecutor(controller)
    class Recorder:
        def stop(self):
            assert controller._in_transition()
            # Mirror the real teardown: original channels disappear, then
            # return at a different gain. Quit must retain graph ownership.
            backend.state['speakers'] = {'mute:0': False, 'vmvc:0': .7333333492279053}
            controller.poll()
            backend.state['speakers'] = {'mute:0': False, 'volm:1': .4375, 'volm:2': .4375}
            if stop_error:
                raise RuntimeError('close failed')
    monkeypatch.setattr(app, 'AudioRecorder', Recorder)
    monkeypatch.setattr(app, 'AsrEngine', lambda: None)
    pipe = app.Pipeline(SimpleNamespace(microphone='auto'))
    session = audio.AudioDuckingSession(executor, 1)
    try:
        session.ready.result(timeout=2)
        setattr(pipe, owner, session)
        pipe._recording = owner == '_ducking_session'
        pipe._stopping = owner == '_stopping_session'
        if stop_error:
            with pytest.raises(RuntimeError, match='close failed'):
                pipe.stop_capture()
        else:
            pipe.stop_capture()
        executor.submit(lambda: None).result(timeout=2)
        assert backend.controls('speakers') == original
        assert not controller.saved
        assert not controller.path.exists()
        assert not pipe._recording and not pipe._starting
        assert pipe._ducking_session is None and pipe._stopping_session is None
    finally:
        executor.submit(controller.close).result(timeout=2)
        executor.shutdown()
