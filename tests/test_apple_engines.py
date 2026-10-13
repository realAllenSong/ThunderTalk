"""Built-in Apple engines: on-device Speech recognition and system voices.

Real framework calls; skipped when this Mac has no on-device locale, no
system voice or speech recognition was denied."""

from __future__ import annotations

import sys
import threading

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

apple_speech = pytest.importorskip("thundertalk.core.apple_speech")
apple_tts = pytest.importorskip("thundertalk.core.tts_backends.apple")

from thundertalk.core import audio_io, tts_verify  # noqa: E402


def _need_asr(lang: str) -> str:
    if not apple_speech.available():
        pytest.skip("Speech framework unavailable")
    if apple_speech.authorization_status() in ("denied", "restricted"):
        pytest.skip("speech recognition permission denied")
    try:
        return apple_speech.resolve_locale(lang)
    except apple_speech.AppleSpeechError:
        pytest.skip(f"no on-device locale for {lang}")


def _need_voice(language: str) -> str:
    if not apple_tts.available():
        pytest.skip("AVFoundation unavailable")
    voice = apple_tts.best_voice(language)
    if voice is None:
        pytest.skip(f"no system voice for {language}")
    return voice


@pytest.fixture(autouse=True)
def _no_prompt(monkeypatch):
    monkeypatch.setattr(apple_speech, "PROMPT", False)


def _say(text: str, language: str) -> tuple[np.ndarray, int]:
    backend = apple_tts.AppleBackend()
    return backend.generate(text, _need_voice(language), language), backend.sample_rate


SENTENCES = {
    "chinese": "今天下午三点开会，请大家准时到会议室。",
    "english": "The meeting starts at three this afternoon, please be on time.",
}


@pytest.mark.parametrize("language", ["chinese", "english"])
def test_system_voice_renders_at_pipeline_rate(language):
    audio, sr = _say(SENTENCES[language], language)
    assert sr == apple_tts.SAMPLE_RATE == 24000
    assert audio.dtype == np.float32
    assert 1.0 < len(audio) / sr < 15.0
    assert float(np.sqrt(np.mean(audio ** 2))) > 0.01


def test_voices_are_marked_with_quality_and_language():
    if not apple_tts.available():
        pytest.skip("AVFoundation unavailable")
    voices = apple_tts.system_voices()
    if not voices:
        pytest.skip("no system voices")
    assert all(v.id.startswith(apple_tts.PREFIX) for v in voices)
    assert all(v.quality in ("premium", "enhanced", "siri", "compact") for v in voices)
    assert {v.language for v in voices} <= {"chinese", "english"}


@pytest.mark.parametrize("language,lang", [("chinese", "zh"), ("english", "en")])
def test_read_back_through_apple_recognition(language, lang):
    locale = _need_asr(lang)
    audio, sr = _say(SENTENCES[language], language)
    got = apple_speech.AppleRecognizer().recognize(audio_io.resample(audio, sr, 16000), 16000, [locale])
    assert tts_verify.error_rate(SENTENCES[language], got.text, language) < 0.2
    assert got.words
    times = [w.start for w in got.words]
    assert times == sorted(times)
    assert all(w.end >= w.start for w in got.words)


def test_auto_language_picks_the_spoken_one():
    _need_asr("zh")
    _need_asr("en")
    audio, sr = _say(SENTENCES["english"], "english")
    got = apple_speech.AppleRecognizer().recognize(audio_io.resample(audio, sr, 16000), 16000,
                                                    apple_speech.auto_locales())
    assert got.locale.startswith("en")
    assert tts_verify.error_rate(SENTENCES["english"], got.text, "english") < 0.2


def test_concurrent_requests_both_come_back():
    """Two on-device requests at once used to return one empty result."""
    locale = _need_asr("zh")
    audio, sr = _say(SENTENCES["chinese"], "chinese")
    x = audio_io.resample(audio, sr, 16000)
    out: list[str] = []

    def run():
        out.append(apple_speech.AppleRecognizer().recognize(x, 16000, [locale]).text)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert len(out) == 2 and all(t.strip() for t in out)


def test_silence_is_empty_not_an_error():
    locale = _need_asr("en")
    got = apple_speech.AppleRecognizer().recognize(np.zeros(16000 * 2, np.float32), 16000, [locale])
    assert got.text == ""


def test_never_asks_when_tcc_would_abort(monkeypatch):
    """From a terminal the terminal app is the responsible process; asking
    then kills ThunderTalk (SIGABRT), even inside the bundle."""
    monkeypatch.setattr(apple_speech, "PROMPT", True)
    monkeypatch.setattr(apple_speech, "authorization_status", lambda: "not_determined")
    assert not apple_speech._responsible_for_self()          # pytest runs under a terminal / IDE
    assert not apple_speech.can_request_permission()
    assert apple_speech.request_permission(timeout=0.1) == "not_determined"


def test_asr_engine_loads_apple_model():
    _need_asr("zh")
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.models import APPLE_SPEECH_ID, BUILTIN_MODELS, get_model_path
    info = next(m for m in BUILTIN_MODELS if m.id == APPLE_SPEECH_ID)
    eng = AsrEngine()
    eng.load_model(get_model_path(APPLE_SPEECH_ID), info.family, info.backend)
    try:
        assert eng.word_timestamps
        audio, sr = _say(SENTENCES["chinese"], "chinese")
        r = eng.recognize(audio_io.resample(audio, sr, 16000), 16000)
        assert tts_verify.error_rate(SENTENCES["chinese"], r.text, "chinese") < 0.2
    finally:
        eng.unload()
