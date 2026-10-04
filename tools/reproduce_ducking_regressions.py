"""Compare narrowly reproduced regressions against tested 88a124a using fakes."""
import json
import subprocess
import tempfile
import types
from pathlib import Path
from thundertalk.core import system_audio as current
from thundertalk.core import audio_diagnostics as diag
from tests.test_system_audio import FakeAudio, DelayedAudio
import logging
logger = logging.Logger('comparison')
logger.addHandler(logging.NullHandler())
diag._logger = logger
baseline = types.ModuleType('baseline_audio')
exec(compile(subprocess.check_output(['git', 'show', '88a124a:thundertalk/core/system_audio.py'],
                                    text=True), '88a124a:system_audio.py', 'exec'), baseline.__dict__)


class LatchedAudio(FakeAudio):
    def __init__(self):
        super().__init__()
        self.latched = False
    def write(self, uid, key, value):
        if key == 'mute:0' and value is True:
            self.latched = True
        elif key.startswith('volm:') and self.state[uid]['mute:0'] is False:
            self.latched = False
        return super().write(uid, key, value)


for version, mod in [('88a124a', baseline), ('fixed', current)]:
    for case in ('delayed_readback', 'graph_reset', 'publication_latch'):
        backend = DelayedAudio() if case == 'delayed_readback' else (
            LatchedAudio() if case == 'publication_latch' else FakeAudio())
        backend.apple_supported = False
        with tempfile.TemporaryDirectory(prefix='audio-fakes-', dir=Path.cwd()) as directory:
            controller = mod._DuckingController(backend, Path(directory) / 'state.json')
            try:
                controller.begin(1)
                if case == 'graph_reset':
                    if mod is current:
                        controller.transition(1, 'open')
                    backend.state['speakers']['mute:0'] = False
                    if mod is current:
                        controller.synchronize(1)
                    else:
                        controller.poll()
                ready = backend.controls('speakers')
                controller.end(1)
                # Let accepted setters arrive, just as later driver reads do.
                for _ in range(10):
                    backend.controls('speakers')
                print(json.dumps(dict(version=version, case=case, ready=ready,
                                      after=backend.controls('speakers'),
                                      journal_pending=bool(controller.saved),
                                      playback_latch=getattr(backend, 'latched', None)), sort_keys=True))
            finally:
                controller.close()
