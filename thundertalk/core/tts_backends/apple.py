"""The voices built into macOS (AVSpeechSynthesizer), nothing to download.

Measured behaviour that shapes this module (macOS 15.6):

  * ``writeUtterance:toBufferCallback:`` delivers float32 PCM at the voice's
    own rate (22.05 kHz for compact and Siri voices) on the *main* dispatch
    queue, whichever thread asked. In the app Qt's event loop drains that
    queue; on the main thread (CLI, selftest) this module runs the run loop
    itself. Off the main thread with no run loop, nothing ever arrives, so
    after ``FIRST_BUFFER_S`` it falls back to NSSpeechSynthesizer, which
    renders to a file from any thread but does not offer the Siri voices;
  * the system lists ~190 voices; most are novelty ("Bubbles", "Organ") or
    Eloquence voices (16 kHz, robotic). Only ``com.apple.voice.*`` and the
    Siri voices are offered, for Chinese and English;
  * ``AVSpeechUtterance.rate`` is not linear in speaking time, so speed is
    left to the pipeline's pitch-preserving stretch like the other engines.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from typing import Optional

import numpy as np

from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, TtsBackend

SAMPLE_RATE = 24000                      # the pipeline's rate; voices render at their own
PREFIX = "apple:"
FIRST_BUFFER_S = 8.0
_LANGS = {"zh": "chinese", "en": "english"}
_QUALITY = {2: "enhanced", 3: "premium"}
_REGION = {
    "zh-CN": ("Mandarin (China)", "普通话（中国大陆）"), "zh-TW": ("Mandarin (Taiwan)", "国语（台湾）"),
    "zh-HK": ("Cantonese (Hong Kong)", "粤语（香港）"), "en-US": ("US English", "美式英语"),
    "en-GB": ("British English", "英式英语"), "en-AU": ("Australian English", "澳大利亚英语"),
    "en-IE": ("Irish English", "爱尔兰英语"), "en-IN": ("Indian English", "印度英语"),
    "en-ZA": ("South African English", "南非英语"), "en-CA": ("Canadian English", "加拿大英语"),
}


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import AVFoundation  # noqa: F401
        return True
    except ImportError:
        return False


def _offered(identifier: str) -> bool:
    return identifier.startswith("com.apple.voice.") or ".siri_" in identifier


def quality_of(v) -> str:
    """"premium" | "enhanced" | "siri" | "compact"."""
    q = _QUALITY.get(int(v.quality()))
    if q:
        return q
    return "siri" if ".siri_" in str(v.identifier()) else "compact"


_RANK = {"premium": 0, "enhanced": 1, "siri": 2, "compact": 3}
_VOICES: Optional[list[BackendVoice]] = None
_LOCALES: dict[str, str] = {}
_LOCK = threading.Lock()


def _scan() -> list[BackendVoice]:
    import AVFoundation as AV
    rows = []
    for v in AV.AVSpeechSynthesisVoice.speechVoices():
        ident, loc = str(v.identifier()), str(v.language())
        lang = _LANGS.get(loc.split("-")[0])
        if lang is None or not _offered(ident):
            continue
        q = quality_of(v)
        region_en, region_zh = _REGION.get(loc, (loc, loc))
        q_en = {"premium": "Premium", "enhanced": "Enhanced", "siri": "Siri"}.get(q, "")
        q_zh = {"premium": "高级", "enhanced": "增强", "siri": "Siri"}.get(q, "")
        name = str(v.name()) + (f" ({q_en})" if q_en else "")
        gender = {1: "m", 2: "f"}.get(int(v.gender()), "")
        rows.append((lang != "chinese", _RANK[q], loc != ("zh-CN" if lang == "chinese" else "en-US"), name,
                     BackendVoice(id=PREFIX + ident, name=name, language=lang, gender=gender,
                                  blurb_en=f"{region_en}" + (f" · {q_en}" if q_en else ""),
                                  blurb_zh=f"{region_zh}" + (f" · {q_zh}" if q_zh else ""),
                                  quality=q)))
        _LOCALES[PREFIX + ident] = loc
    rows.sort(key=lambda r: r[:4])
    return [r[4] for r in rows]


def system_voices() -> list[BackendVoice]:
    """Chinese and English system voices, best first (scanned once)."""
    global _VOICES
    with _LOCK:
        if _VOICES is None:
            _VOICES = _scan() if available() else []
        return list(_VOICES)


def locale_of(voice_id: str) -> str:
    system_voices()
    return _LOCALES.get(voice_id, "")


def best_voice(language: str) -> Optional[str]:
    """The highest-quality installed voice for "chinese" / "english"."""
    return next((v.id for v in system_voices() if v.language == language), None)


def _identifier(voice) -> str:
    if not isinstance(voice, str) or not voice.startswith(PREFIX):
        raise ValueError("System voices cannot clone; pick one of the installed voices")
    return voice[len(PREFIX):]


def _pump(until: threading.Event, deadline: float) -> None:
    from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop
    loop = NSRunLoop.currentRunLoop()
    while not until.is_set() and time.monotonic() < deadline:
        loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))


def _render_av(text: str, identifier: str, timeout: float) -> Optional[tuple[np.ndarray, int]]:
    """Speak into buffers. None when no buffer arrived (no run loop serves the
    main queue)."""
    import AVFoundation as AV
    voice = AV.AVSpeechSynthesisVoice.voiceWithIdentifier_(identifier)
    if voice is None:
        raise ValueError(f"System voice not installed: {identifier}")
    synth = AV.AVSpeechSynthesizer.alloc().init()
    utt = AV.AVSpeechUtterance.speechUtteranceWithString_(text)
    utt.setVoice_(voice)
    chunks: list[np.ndarray] = []
    rate = [0]
    first, done = threading.Event(), threading.Event()

    def on_buffer(buf) -> None:
        try:
            n = int(buf.frameLength())
            first.set()
            if n == 0:
                done.set()
                return
            fmt = buf.format()
            rate[0] = int(fmt.sampleRate())
            if int(fmt.commonFormat()) == 1:                     # AVAudioPCMFormatFloat32
                chunks.append(np.frombuffer(buf.floatChannelData()[0].as_buffer(n), dtype=np.float32).copy())
            else:
                pcm = np.frombuffer(buf.int16ChannelData()[0].as_buffer(n), dtype=np.int16)
                chunks.append(pcm.astype(np.float32) / 32768.0)
        except Exception:                 # never let an exception escape into the ObjC queue
            done.set()

    synth.writeUtterance_toBufferCallback_(utt, on_buffer)
    end = time.monotonic() + timeout
    if threading.current_thread() is threading.main_thread():
        _pump(done, end)
    elif not first.wait(FIRST_BUFFER_S):
        synth.stopSpeakingAtBoundary_(0)
        return None
    else:
        done.wait(max(0.0, end - time.monotonic()))
    if not done.is_set():
        synth.stopSpeakingAtBoundary_(0)
        raise RuntimeError("The system voice did not finish speaking")
    audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    return audio, rate[0] or 22050


def _render_ns(text: str, identifier: str, timeout: float) -> tuple[np.ndarray, int]:
    """NSSpeechSynthesizer to an AIFF file: works on any thread."""
    import AppKit
    from Foundation import NSURL

    from thundertalk.core import audio_io
    synth = AppKit.NSSpeechSynthesizer.alloc().initWithVoice_(identifier)
    if synth is None:
        raise RuntimeError("This system voice needs the app's main thread (Siri voices)")
    fd, tmp = tempfile.mkstemp(suffix=".aiff", prefix="tt-say-")
    os.close(fd)
    try:
        if not synth.startSpeakingString_toURL_(text, NSURL.fileURLWithPath_(tmp)):
            raise RuntimeError("The system voice could not start")
        end = time.monotonic() + timeout
        while synth.isSpeaking() and time.monotonic() < end:
            time.sleep(0.03)
        return audio_io.decode_audio(tmp, SAMPLE_RATE), SAMPLE_RATE
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class AppleBackend(TtsBackend):
    stochastic = False               # same text + voice → same audio; retrying cannot help
    info = BackendInfo(
        id="apple", name="Apple",
        blurb_en="The voices built into macOS. Nothing to download; Chinese and English; no cloning.",
        blurb_zh="macOS 自带的系统语音。无需下载，支持中英文，不支持克隆。",
        languages=("chinese", "english"), language_codes=("zh", "en"),
        supports_presets=True, supports_clone=False, needs_gpu=False, speed="apple_tts",
    )
    sample_rate = SAMPLE_RATE

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def is_ready(self) -> bool:
        return available() and bool(system_voices())

    def voices(self) -> list[BackendVoice]:
        return system_voices()

    def load(self) -> None:
        if not self.is_ready():
            from thundertalk.core.tts import TtsModelMissing
            raise TtsModelMissing("apple")

    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        ident = _identifier(voice)
        if not text.strip():
            return np.zeros(0, dtype=np.float32)
        timeout = 30.0 + 0.5 * len(text)
        with self._lock:
            got = _render_av(text, ident, timeout)
            if got is None:
                got = _render_ns(text, ident, timeout)
        audio, sr = got
        from thundertalk.core import audio_io
        return audio_io.resample(audio, sr, SAMPLE_RATE) if sr != SAMPLE_RATE else audio
