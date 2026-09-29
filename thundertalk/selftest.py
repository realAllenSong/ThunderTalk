"""Headless checks that run inside the *packaged* app:

    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest audio
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest tts
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest asr --file talk.m4a
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest clone --file me.wav --text "what I said"

The old Lab worked from source but not from the shipped app (missing server,
ffmpeg, scipy). These exist so a release can prove each Studio feature works
in the bundle, with real models, before it is published."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


def _say(ok: bool, name: str, **info) -> bool:
    print(json.dumps({"ok": ok, "check": name, **info}, ensure_ascii=False), flush=True)
    return ok


def check_audio() -> bool:
    from thundertalk.core import audio_io, playback  # noqa: F401
    sr = 24000
    t = np.arange(sr * 2) / sr
    x = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    with tempfile.TemporaryDirectory() as d:
        m4a = str(Path(d) / "x.m4a")
        audio_io.export_audio(m4a, x, sr)
        y = audio_io.decode_audio(m4a, 16000)
        dur = audio_io.audio_duration(m4a)
        spec = np.abs(np.fft.rfft(y[1600:-1600] * np.hanning(len(y) - 3200)))
        hz = float(np.fft.rfftfreq(len(y) - 3200, 1 / 16000)[int(np.argmax(spec))])
    return _say(abs(hz - 300) < 6 and abs(dur - 2.0) < 0.2, "audio", m4a_roundtrip_hz=round(hz, 1), duration=round(dur, 2))


def check_tts() -> bool:
    from thundertalk.core import tts
    eng = tts.get_engine()
    if not tts.repo_ready(tts.CUSTOM_REPO):
        return _say(False, "tts", error="voice engine not downloaded", repo=tts.CUSTOM_REPO)
    t0 = time.monotonic()
    r = eng.synthesize("Hello from the packaged app. This sentence checks that speech comes out.", "ryan",
                       language="english", seed=7)
    rms = float(np.sqrt(np.mean(r.audio ** 2)))
    return _say(3.0 < r.duration < 14.0 and rms > 0.02 and all(s.ok for s in r.segments), "tts",
                seconds_audio=round(r.duration, 1), seconds_taken=round(time.monotonic() - t0, 1), rms=round(rms, 3),
                pieces=len(r.segments))


def _load_asr():
    from thundertalk.core.asr import AsrEngine
    eng = AsrEngine()
    eng.load_model("hf://Qwen/Qwen3-ASR-0.6B", "Qwen3-ASR", "mlx")
    return eng


def check_asr(path: str) -> bool:
    from thundertalk.core import transcribe
    eng = _load_asr()
    t = transcribe.transcribe_file(path, eng)
    return _say(bool(t.segments), "asr", segments=len(t.segments), realtime=round(t.realtime_factor, 1),
                text=t.to_text()[:200])


def check_clone(path: str, text: str) -> bool:
    from thundertalk.core import audio_io, tts, tts_verify, voices
    if not tts.repo_ready(tts.BASE_REPO):
        return _say(False, "clone", error="clone model not downloaded", repo=tts.BASE_REPO)
    x = audio_io.decode_audio(path, tts.SR)
    chk = voices.prepare_reference(x, tts.SR)
    prompt = tts.ClonePrompt(chk.audio, text, "selftest")
    eng = tts.get_engine()
    src = "Now the cloned voice reads a brand new sentence that was never in the recording."
    r = eng.synthesize(src, prompt, language="english", seed=11)
    asr = _load_asr()
    err = tts_verify.make_verifier(asr)(r.audio, src, "english")
    return _say(2.0 < r.duration < 14.0 and (err is None or err < 0.3), "clone", reference_ok=chk.ok,
                seconds_audio=round(r.duration, 1), read_back_error=None if err is None else round(err, 3))


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ThunderTalk --selftest")
    ap.add_argument("what", choices=["audio", "tts", "asr", "clone", "all"])
    ap.add_argument("--file", default="")
    ap.add_argument("--text", default="")
    a = ap.parse_args(argv)
    ok = True
    try:
        if a.what in ("audio", "all"):
            ok &= check_audio()
        if a.what in ("tts", "all"):
            ok &= check_tts()
        if a.what == "asr":
            ok &= check_asr(a.file)
        if a.what == "clone":
            ok &= check_clone(a.file, a.text)
    except Exception as exc:                                    # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok = _say(False, a.what, error=f"{type(exc).__name__}: {exc}")
    print("SELFTEST", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
