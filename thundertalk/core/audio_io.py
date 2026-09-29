"""Audio file I/O with no ffmpeg and no scipy.

The packaged app ships without ffmpeg and PyInstaller excludes scipy, so both
of the old code paths (`ffmpeg -i ... ` for every transcription, `scipy.io.
wavfile` for reading/playing) failed for ordinary users. This module uses:

  * macOS `afconvert` (part of the OS) to decode m4a / mp3 / aac / wav / flac /
    aiff / caf and the audio track of mp4 / mov;
  * plain numpy + the stdlib `wave` module for WAV;
  * an FFT resampler for sample-rate conversion.

`ffmpeg` is only a fallback (for containers CoreAudio can't open, e.g. mkv,
webm, ogg) and on non-macOS platforms.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

_IS_MAC = sys.platform == "darwin"

# Extensions we offer in file pickers / accept on drop.
AUDIO_EXTS = frozenset({
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".aiff", ".aif", ".caf",
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".ogg", ".opus",
})


class AudioDecodeError(RuntimeError):
    """The file could not be decoded (message is safe to show to the user)."""


def find_ffmpeg() -> Optional[str]:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(c):
            return c
    return None


# ── WAV (numpy only) ─────────────────────────────────────────────────────

def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a WAV file as float32 in [-1, 1]. Multi-channel is returned as
    (n, channels). Handles 8/16/24/32-bit PCM and 32-bit float."""
    with open(path, "rb") as f:
        head = f.read(64)
    fmt_tag = None
    idx = head.find(b"fmt ")
    if idx >= 0 and len(head) >= idx + 10:
        fmt_tag = int.from_bytes(head[idx + 8: idx + 10], "little")
    if fmt_tag == 3:                                   # IEEE float — stdlib wave rejects it
        return _read_wav_float(path)
    with wave.open(path, "rb") as wf:
        sr, ch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioDecodeError(f"Unsupported WAV sample width: {sw} bytes")
    if ch > 1:
        x = x.reshape(-1, ch)
    return x, sr


def _read_wav_float(path: str) -> tuple[np.ndarray, int]:
    data = Path(path).read_bytes()
    pos, sr, ch, bits = 12, 0, 1, 32
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt ":
            ch = int.from_bytes(data[pos + 10:pos + 12], "little")
            sr = int.from_bytes(data[pos + 12:pos + 16], "little")
            bits = int.from_bytes(data[pos + 22:pos + 24], "little")
        elif cid == b"data":
            dt = "<f4" if bits == 32 else "<f8"
            x = np.frombuffer(data[pos + 8: pos + 8 + size], dtype=dt).astype(np.float32)
            return (x.reshape(-1, ch) if ch > 1 else x), sr
        pos += 8 + size + (size & 1)
    raise AudioDecodeError("WAV file has no data chunk")


def write_wav(path: str, x: np.ndarray, sr: int) -> None:
    """Write mono/stereo float audio as 16-bit PCM WAV (universally playable)."""
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    ch = 1 if pcm.ndim == 1 else pcm.shape[1]
    with wave.open(path, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(pcm.tobytes())


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    import io
    x = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1 if pcm.ndim == 1 else pcm.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


# ── decoding ─────────────────────────────────────────────────────────────

def _afconvert(src: str, dst: str, sr: int) -> None:
    r = subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{sr}", "-c", "1", src, dst],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        raise AudioDecodeError(r.stderr.strip() or f"afconvert failed ({r.returncode})")


def _ffmpeg(src: str, dst: str, sr: int) -> None:
    ff = find_ffmpeg()
    if not ff:
        raise AudioDecodeError("ffmpeg_missing")
    r = subprocess.run([ff, "-y", "-v", "error", "-i", src, "-vn", "-ar", str(sr), "-ac", "1", dst],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise AudioDecodeError(r.stderr.strip()[-300:] or f"ffmpeg failed ({r.returncode})")


def decode_audio(path: str, sr: int = 16000) -> np.ndarray:
    """Decode any supported audio/video file to a mono float32 array at ``sr``.

    Raises AudioDecodeError with a user-presentable message on failure."""
    src = str(path)
    if not os.path.isfile(src):
        raise AudioDecodeError(f"File not found: {src}")
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        errors: list[str] = []
        ok = False
        if _IS_MAC and shutil.which("afconvert"):
            try:
                _afconvert(src, tmp, sr)
                ok = True
            except (AudioDecodeError, subprocess.SubprocessError) as e:
                errors.append(str(e))
        if not ok:
            try:
                _ffmpeg(src, tmp, sr)
                ok = True
            except AudioDecodeError as e:
                errors.append(str(e))
        if not ok:
            if any(e == "ffmpeg_missing" for e in errors):
                raise AudioDecodeError(
                    "This file format isn't supported by macOS. Convert it to m4a, mp3 or wav, "
                    "or install ffmpeg (brew install ffmpeg).")
            raise AudioDecodeError("Couldn't read this file: " + (errors[0] if errors else "unknown error"))
        x, got = read_wav(tmp)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if got != sr:
            x = resample(x, got, sr)
        if len(x) == 0:
            raise AudioDecodeError("The file contains no audio.")
        return x.astype(np.float32)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def audio_duration(path: str) -> float:
    """Best-effort duration in seconds (0.0 if unknown)."""
    if _IS_MAC and shutil.which("afinfo"):
        try:
            r = subprocess.run(["afinfo", path], capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                if "estimated duration" in line:
                    return float(line.split(":")[1].split()[0])
        except Exception:
            pass
    return 0.0


# ── resampling ───────────────────────────────────────────────────────────

def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """High-quality FFT resample (band-limited; no scipy). Mono or (n, ch)."""
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if x.ndim > 1:
        return np.stack([resample(x[:, c], sr_in, sr_out) for c in range(x.shape[1])], axis=1)
    n_out = int(round(len(x) * sr_out / sr_in))
    # Pad to a fast FFT length and taper the edges so the periodic assumption
    # doesn't ring at the ends.
    n = len(x)
    pad = min(2048, n // 4)
    if pad > 8:
        w = np.ones(n, dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, pad, dtype=np.float32)
        w[:pad] = ramp
        w[-pad:] = ramp[::-1]
        x = x * w
    spec = np.fft.rfft(x.astype(np.float64))
    n_new = n_out
    out_spec = np.zeros(n_new // 2 + 1, dtype=np.complex128)
    m = min(len(spec), len(out_spec))
    out_spec[:m] = spec[:m]
    y = np.fft.irfft(out_spec, n=n_new) * (n_new / n)
    return y.astype(np.float32)


# ── export ───────────────────────────────────────────────────────────────

def export_audio(path: str, x: np.ndarray, sr: int) -> None:
    """Save as .wav, or .m4a (AAC) via macOS afconvert."""
    ext = Path(path).suffix.lower()
    if ext == ".wav" or ext == "":
        write_wav(path if ext else path + ".wav", x, sr)
        return
    if ext in (".m4a", ".aac", ".mp4") and _IS_MAC and shutil.which("afconvert"):
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            write_wav(tmp, x, sr)
            # AAC-LC mono refuses high bitrates at low sample rates (24 kHz
            # rejects 96k+), so ask for a sensible one and fall back to the
            # encoder's own default.
            wanted = "96000" if sr >= 32000 else "64000"
            err = ""
            for extra in (["-b", wanted], []):
                r = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", *extra, tmp, path],
                                   capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    break
                err = r.stderr.strip()
            else:
                raise AudioDecodeError(err or "m4a export failed")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return
    raise AudioDecodeError(f"Can't export as {ext}")
