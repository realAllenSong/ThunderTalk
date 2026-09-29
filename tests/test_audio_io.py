"""ffmpeg/scipy-free audio I/O."""

from __future__ import annotations

import shutil
import struct
import sys
import wave

import numpy as np
import pytest

from thundertalk.core import audio_io

needs_afconvert = pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert only")


def _tone(seconds, hz=440.0, sr=16000, amp=0.4):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _peak_hz(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spec))]


def test_wav_round_trip(tmp_path):
    x = _tone(1.0)
    p = tmp_path / "a.wav"
    audio_io.write_wav(str(p), x, 16000)
    y, sr = audio_io.read_wav(str(p))
    assert sr == 16000 and len(y) == len(x)
    assert np.max(np.abs(x - y)) < 1e-3


def test_wav_bytes_is_a_valid_wav():
    import io
    data = audio_io.wav_bytes(_tone(0.5), 16000)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getnframes() == 8000


@pytest.mark.parametrize("width", [1, 3])
def test_reads_uncommon_pcm_widths(tmp_path, width):
    x = _tone(0.2)
    p = tmp_path / f"w{width}.wav"
    if width == 1:
        raw = ((x * 127 + 128).astype(np.uint8)).tobytes()
    else:
        ints = (x * (2 ** 23 - 1)).astype(np.int32)
        raw = b"".join(struct.pack("<i", int(v))[:3] for v in ints)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(16000)
        w.writeframes(raw)
    y, sr = audio_io.read_wav(str(p))
    assert sr == 16000 and len(y) == len(x)
    assert np.max(np.abs(x - y)) < (0.02 if width == 1 else 1e-3)


def test_stereo_is_downmixed_by_decode(tmp_path):
    left, right = _tone(0.5, 300.0), _tone(0.5, 300.0)
    p = tmp_path / "st.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        inter = np.empty(len(left) * 2, dtype=np.int16)
        inter[0::2] = (left * 32767).astype(np.int16)
        inter[1::2] = (right * 32767).astype(np.int16)
        w.writeframes(inter.tobytes())
    y = audio_io.decode_audio(str(p), 16000)
    assert y.ndim == 1 and abs(len(y) - len(left)) < 200


@pytest.mark.parametrize("a,b", [(16000, 24000), (24000, 16000), (44100, 16000), (16000, 16000)])
def test_resample_keeps_pitch_and_level(a, b):
    x = _tone(1.0, 440.0, a)
    y = audio_io.resample(x, a, b)
    assert abs(len(y) - len(x) * b / a) <= 2
    assert abs(_peak_hz(y, b) - 440.0) < 4.0
    assert abs(np.max(np.abs(y)) - 0.4) < 0.03


def test_decode_missing_or_garbage_file_is_a_typed_error(tmp_path):
    with pytest.raises(audio_io.AudioDecodeError):
        audio_io.decode_audio(str(tmp_path / "nope.wav"))
    junk = tmp_path / "junk.m4a"
    junk.write_bytes(b"this is not audio" * 100)
    with pytest.raises(audio_io.AudioDecodeError):
        audio_io.decode_audio(str(junk))


@needs_afconvert
def test_decode_resamples_wav_to_target(tmp_path):
    p = tmp_path / "a.wav"
    audio_io.write_wav(str(p), _tone(1.0, 500.0, 44100), 44100)
    y = audio_io.decode_audio(str(p), 16000)
    assert abs(len(y) - 16000) < 100
    assert abs(_peak_hz(y, 16000) - 500.0) < 5.0


@needs_afconvert
def test_m4a_export_and_decode_round_trip(tmp_path):
    x = _tone(2.0, 350.0, 24000)
    p = tmp_path / "out.m4a"
    audio_io.export_audio(str(p), x, 24000)
    assert p.stat().st_size > 1000
    assert abs(audio_io.audio_duration(str(p)) - 2.0) < 0.15
    y = audio_io.decode_audio(str(p), 24000)
    assert abs(_peak_hz(y[2000:-2000], 24000) - 350.0) < 5.0


@needs_afconvert
def test_export_wav_by_extension(tmp_path):
    p = tmp_path / "x.wav"
    audio_io.export_audio(str(p), _tone(0.5, 440.0, 24000), 24000)
    y, sr = audio_io.read_wav(str(p))
    assert sr == 24000 and len(y) == 12000


def test_audio_exts_cover_common_containers():
    for ext in (".wav", ".mp3", ".m4a", ".mp4", ".mov", ".flac", ".ogg", ".aac"):
        assert ext in audio_io.AUDIO_EXTS


@pytest.mark.skipif(sys.platform != "darwin", reason="afinfo is macOS-only")
def test_duration_of_wav(tmp_path):
    p = tmp_path / "d.wav"
    audio_io.write_wav(str(p), _tone(1.5), 16000)
    assert abs(audio_io.audio_duration(str(p)) - 1.5) < 0.05
