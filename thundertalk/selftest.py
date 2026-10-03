"""Headless checks that run inside the *packaged* app:

    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest audio
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest tts
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest asr --file talk.m4a
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest clone --file me.wav --text "what I said"
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest link --file https://…   (downloads the audio only)
    ThunderTalk.app/Contents/MacOS/ThunderTalk --selftest burn                   (needs ffmpeg installed)

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


def check_tts(engine: str = "") -> bool:
    """Speak one English sentence with every downloaded engine (or just ``engine``)."""
    from thundertalk.core import speech
    ok_all, ran = True, 0
    for b in speech.backends():
        if engine and b.info.id != engine:
            continue
        if not b.is_ready():
            _say(True, f"tts:{b.info.id}", skipped="not downloaded")
            continue
        voice = next(v.id for v in b.voices() if v.language in ("english", "multi")) if any(
            v.language in ("english", "multi") for v in b.voices()) else b.voices()[0].id
        t0 = time.monotonic()
        r = speech.get_engine().synthesize("Hello from the packaged app. This sentence checks that speech comes out.",
                                           voice, language="english", seed=7)
        rms = float(np.sqrt(np.mean(r.audio ** 2)))
        ok = 2.0 < r.duration < 14.0 and rms > 0.02
        ok_all &= _say(ok, f"tts:{b.info.id}", voice=voice, seconds_audio=round(r.duration, 1),
                       seconds_taken=round(time.monotonic() - t0, 1), rms=round(rms, 3), sample_rate=r.sample_rate)
        ran += 1
        speech.get_engine().unload()
    if ran == 0:
        return _say(False, "tts", error="no speech engine downloaded")
    return ok_all


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


def check_moss(path: str) -> bool:
    """MOSS dictation: noise with nobody speaking must come back empty and
    fast (it used to loop for ~20 s and paste bare timestamps); *path*, if
    given, must come back as words."""
    import time

    import numpy as np

    from thundertalk.core import audio_io, diarize
    from thundertalk.core.asr import AsrEngine
    eng = AsrEngine()
    eng._load_mlx_moss(diarize.resolve_model_path())
    noise = (0.006 * np.random.default_rng(0).standard_normal(22400)).astype(np.float32)
    t0 = time.perf_counter()
    quiet = eng.recognize(noise, 16000).text
    quiet_s = time.perf_counter() - t0
    ok = quiet == "" and quiet_s < 5.0
    said = ""
    if path:
        said = eng.recognize(audio_io.decode_audio(path, 16000), 16000).text
        ok &= bool(said.strip()) and "[" not in said
    return _say(ok, "moss", noise_text=quiet, noise_seconds=round(quiet_s, 2), text=said[:200])


def check_clone(path: str, text: str, engine: str = "") -> bool:
    from thundertalk.core import audio_io, speech, tts, tts_verify, voices
    bid = engine or speech.DEFAULT_CLONE_BACKEND
    if not speech.backend(bid).is_ready():
        return _say(False, "clone", error=f"{bid} not downloaded")
    x = audio_io.decode_audio(path, tts.SR)
    chk = voices.prepare_reference(x, tts.SR)
    prompt = tts.ClonePrompt(chk.audio, text, "selftest")
    src = "Now the cloned voice reads a brand new sentence that was never in the recording."
    r = speech.get_engine().synthesize(src, prompt, language="english", seed=11, clone_backend=bid)
    speech.get_engine().unload()
    asr = _load_asr()
    a24 = audio_io.resample(r.audio, r.sample_rate, 24000)
    err = tts_verify.make_verifier(asr)(a24, src, "english")
    return _say(2.0 < r.duration < 14.0 and (err is None or err < 0.3), "clone", engine=bid, reference_ok=chk.ok,
                seconds_audio=round(r.duration, 1), read_back_error=None if err is None else round(err, 3))


def check_link(url: str) -> bool:
    """Download the audio of a web link (no model) and decode it."""
    from thundertalk.core import audio_io, links
    t0 = time.monotonic()
    got = links.fetch_audio(url)
    try:
        size = Path(got.path).stat().st_size
        x = audio_io.decode_audio(got.path, 16000)
    finally:
        import shutil
        shutil.rmtree(got.workdir, ignore_errors=True)
    secs = len(x) / 16000
    return _say(secs > 1.0, "link", title=got.title, format=Path(got.path).suffix, bytes=size,
                seconds_audio=round(secs, 1), seconds_advertised=got.duration,
                seconds_taken=round(time.monotonic() - t0, 1))


def check_burn() -> bool:
    """Burn a two-line Chinese/English transcript into a generated clip (needs ffmpeg)."""
    from PySide6.QtGui import QGuiApplication

    from thundertalk.core import audio_io, burn
    from thundertalk.core.transcribe import Segment, Transcript
    ff = audio_io.find_ffmpeg()
    if not ff:
        return _say(False, "burn", error="ffmpeg not found")
    app = QGuiApplication.instance() or QGuiApplication(["selftest"])  # noqa: F841 - fonts need it
    with tempfile.TemporaryDirectory() as d:
        src, out = str(Path(d) / "clip.mp4"), str(Path(d) / "out.mp4")
        import subprocess
        subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi",
                        "-i", "sine=frequency=300", "-t", "4", "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
        tr = Transcript([Segment(0.2, 1.8, "你好，这是字幕测试。"), Segment(2.0, 3.8, "Subtitles in the packaged app.")],
                        4.0, "selftest")
        r = burn.burn(src, tr, out, soft=True, ffmpeg=ff)
        info = burn.probe(ff, out)
    return _say(info is not None and info.width == 640 and r.cues == 2, "burn", bytes=r.size,
                seconds_taken=round(r.seconds, 2), encoder=burn.pick_encoder(ff))


def check_translate() -> bool:
    from thundertalk.core import audio_io
    from thundertalk.core.models import get_model_path
    from thundertalk.core.translate import TranslationEngine
    from thundertalk.core.tts_backends.presets import load_presets
    eng = TranslationEngine()
    try:
        eng.load_model(get_model_path("seamless-m4t-v2-large") or "hf://facebook/seamless-m4t-v2-large")
        result = eng.translate_text("Hello world. How are you today?", "eng", "cmn")
        clip = next(p for p in load_presets() if p.language == "english")
        speech = eng.translate(audio_io.decode_audio(str(clip.wav), 16000), "cmn")
        return _say(bool(result.text.strip()) and bool(speech.text.strip()), "translate",
                    text=result.text, speech_text=speech.text,
                    inference_ms=result.inference_ms, speech_inference_ms=speech.inference_ms)
    finally:
        eng.unload()


def check_runtime() -> bool:
    from thundertalk.core import runtime
    runtime.install(lambda pct, msg: _say(True, "runtime-progress", percent=pct, message=msg))
    runtime.require()
    import torch
    import torchaudio
    value = torchaudio.functional.melscale_fbanks(201, 0, 8000, 80, 16000)
    return _say(value.shape == (201, 80), "runtime", torch=torch.__version__,
                path=torch.__file__)


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ThunderTalk --selftest")
    ap.add_argument("what", choices=["audio", "tts", "asr", "moss", "clone", "link", "burn", "translate", "runtime", "all"])
    ap.add_argument("--file", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--engine", default="", help="kokoro | voxcpm2 | indextts (default: all downloaded / the clone default)")
    a = ap.parse_args(argv)
    ok = True
    try:
        if a.what in ("audio", "all"):
            ok &= check_audio()
        if a.what in ("tts", "all"):
            ok &= check_tts(a.engine)
        if a.what == "asr":
            ok &= check_asr(a.file)
        if a.what == "moss":
            ok &= check_moss(a.file)
        if a.what == "runtime":
            ok &= check_runtime()
        if a.what == "translate":
            ok &= check_translate()
        if a.what == "clone":
            ok &= check_clone(a.file, a.text, a.engine)
        if a.what == "link":
            ok &= check_link(a.file)
        if a.what == "burn":
            ok &= check_burn()
    except Exception as exc:                                    # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok = _say(False, a.what, error=f"{type(exc).__name__}: {exc}")
    print("SELFTEST", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
