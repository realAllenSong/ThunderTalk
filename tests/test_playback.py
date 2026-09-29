"""Player logic with a fake PortAudio — no sound hardware needed."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from thundertalk.core import playback


class _FakeStream:
    def __init__(self, samplerate, channels, dtype, callback):
        self.samplerate, self.callback = samplerate, callback
        self.started = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class _FakeExec:
    def call(self, fn, timeout=None):
        return fn()


@pytest.fixture
def fake_audio(monkeypatch):
    made = []

    def output_stream(**kw):
        s = _FakeStream(**kw)
        made.append(s)
        return s

    fake_sd = types.SimpleNamespace(OutputStream=output_stream,
                                    query_devices=lambda kind=None: {"default_samplerate": 48000.0})
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    from thundertalk.core import audio_executor
    monkeypatch.setattr(audio_executor, "get_executor", lambda: _FakeExec())
    return made


def _pump(stream, frames=512):
    out = np.zeros((frames, 1), dtype=np.float32)
    stream.callback(out, frames, None, None)
    return out[:, 0]


def _tone(sec, sr=24000):
    return (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(sec * sr)) / sr)).astype(np.float32)


def test_plays_to_the_end_and_releases_the_device(fake_audio):
    p = playback.Player()
    p.load(_tone(0.5), 24000)
    assert p.duration == pytest.approx(0.5) and not p.is_playing
    assert p.play()
    s = fake_audio[0]
    assert s.samplerate == 48000 and s.started            # resampled to the device rate
    heard = 0
    for _ in range(200):
        if not p.is_playing:
            break
        heard += int(np.count_nonzero(_pump(s)))
    assert heard > 0
    assert not p.is_playing
    p.poll()
    assert s.closed                                       # device released after the end
    assert p.position == pytest.approx(0.5, abs=0.02)


def test_pause_keeps_position_and_resume_continues(fake_audio):
    p = playback.Player()
    p.load(_tone(1.0), 24000)
    p.play()
    for _ in range(20):
        _pump(fake_audio[0])
    p.pause()
    pos = p.position
    assert 0.1 < pos < 0.4 and not p.is_playing
    assert fake_audio[0].closed
    p.play()
    assert len(fake_audio) == 2                           # a fresh stream, same position
    assert p.position == pytest.approx(pos, abs=0.01)


def test_seek_and_play_from_fraction(fake_audio):
    p = playback.Player()
    p.load(_tone(2.0), 24000)
    p.play(start=0.5)
    assert p.position == pytest.approx(1.0, abs=0.01)
    p.seek(0.25)
    assert p.position == pytest.approx(0.5, abs=0.01)
    p.seek(5.0)                                           # clamped
    assert p.fraction == pytest.approx(1.0, abs=0.01)


def test_replay_after_end_starts_over(fake_audio):
    p = playback.Player()
    p.load(_tone(0.2), 24000)
    p.play()
    while p.is_playing:
        _pump(fake_audio[-1])
    p.poll()
    assert p.play()
    assert p.position < 0.05 and p.is_playing


def test_output_device_failure_is_reported_not_raised(monkeypatch):
    def boom(**kw):
        raise OSError("no output device")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        OutputStream=boom, query_devices=lambda kind=None: {"default_samplerate": 44100.0}))
    from thundertalk.core import audio_executor
    monkeypatch.setattr(audio_executor, "get_executor", lambda: _FakeExec())
    p = playback.Player()
    p.load(_tone(0.2), 24000)
    assert p.play() is False
    assert "no output device" in p.error and not p.is_playing


def test_nothing_loaded_is_a_noop(fake_audio):
    p = playback.Player()
    assert p.play() is False and p.duration == 0.0 and p.position == 0.0
    p.stop()
    p.clear()


def test_silence_is_written_while_paused(fake_audio):
    p = playback.Player()
    p.load(_tone(0.5), 24000)
    p.play()
    s = fake_audio[0]
    p._playing = False                                     # simulate the pause flag racing the callback
    assert not np.any(_pump(s))
