"""Read-only snapshot, or --run: four brief checks of the public session API.

No microphone, ASR, playback, pasted text, audio files, route changes or virtual
output writes. Uses an isolated worktree journal and an independent finally
restore guard. This does not test microphone graph changes on hardware; those
are covered by the captured reproduction and fake backend regression tests.
"""
from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

from thundertalk.core import system_audio as audio
from tools.check_system_audio import printable, snapshot


class GuardedAudio(audio._DarwinAudio):
    def __init__(self, uid, original):
        super().__init__()
        self.allowed = uid
        self.original = original

    def write(self, uid, key, value):
        assert uid == self.allowed, 'non-default output writes prohibited'
        assert key.startswith('mute:') or value == self.original.get(key), 'gain changes prohibited'
        return super().write(uid, key, value)

    def apple_mute(self, muted):
        raise AssertionError('global AppleScript writes prohibited in hardware check')

    def apple_volume(self, volume):
        raise AssertionError('percentage writes prohibited')


def restore_guard(backend, before):
    """Bounded retries: a transient missing channel must not abort all cleanup."""
    uid = before['default']
    original = before['devices'][uid]['controls']
    deadline = time.monotonic() + 2
    last = None
    while True:
        current = backend.controls(uid)
        if current is not None:
            # Repair gains first, then mute; each control gets its own attempt.
            for key, value in sorted(original.items(), key=lambda pair: pair[0].startswith('mute:')):
                if key in current and current[key] != value:
                    try:
                        backend.write(uid, key, value)
                    except Exception as exc:
                        print('GUARD_WRITE_ERROR', key, type(exc).__name__, flush=True)
        after = snapshot(backend)
        if after == before and last == after:
            return after
        if time.monotonic() >= deadline:
            print('GUARD_AFTER', json.dumps(printable(after), sort_keys=True), flush=True)
            raise AssertionError('all-output guard readback differs; inspect retained journal')
        last = after
        time.sleep(.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    backend = audio._DarwinAudio()
    initial = snapshot(backend)
    print('INITIAL', json.dumps(printable(initial), sort_keys=True), flush=True)
    if not args.run:
        return
    uid = initial['default']
    original = initial['devices'][uid]['controls']
    assert backend.route() == [uid] and original.get('mute:0') is False
    assert backend.writable(uid, 'mute:0')
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    path = Path.cwd() / 'experiments' / 'session-hardware-recovery.json'
    assert not path.exists(), 'existing recovery journal requires inspection'
    controller = audio._DuckingController(GuardedAudio(uid, original), path)
    executor = audio._SystemAudioExecutor(controller)
    audio._system_audio_executor = executor
    started = time.perf_counter()
    try:
        audio.recover_system_audio().result(timeout=4)
        for case in ('cold_worker', 'quick_cancel', 'rapid_repeat_1', 'rapid_repeat_2'):
            before = snapshot(backend)
            print(case, 'BEFORE', json.dumps(printable(before), sort_keys=True), flush=True)
            assert before == initial, 'state changed before cycle; abort'
            session = None
            try:
                session = audio.mute_system_audio()
                if case == 'quick_cancel':
                    session.restore().result(timeout=4)  # release before ready delivery
                else:
                    session.ready.result(timeout=4)
                    session.synchronize().result(timeout=4)
                    current = backend.controls(uid)
                    print(case, 'READY', json.dumps(current, sort_keys=True), flush=True)
                    assert current['mute:0'] is True
            finally:
                try:
                    audio.stop_recording_and_restore(None, session)
                    executor.submit(lambda: None).result(timeout=4)
                finally:
                    after = restore_guard(backend, before)
                    print(case, 'AFTER', json.dumps(printable(after), sort_keys=True), flush=True)
                    assert after == before
    finally:
        try:
            audio.shutdown_system_audio()
        finally:
            final = restore_guard(backend, initial)
            print('FINAL', json.dumps(printable(final), sort_keys=True), flush=True)
            assert final == initial
    print(f'PASS: four public-session cases in {time.perf_counter()-started:.3f}s', flush=True)


if __name__ == '__main__':
    main()
