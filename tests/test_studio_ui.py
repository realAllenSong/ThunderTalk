"""Studio page behaviour with fake engines: real widgets, real worker threads,
no models and no audio hardware."""

from __future__ import annotations

import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from thundertalk.core import audio_io, tts, voices
from thundertalk.core import transcribe as tr

SR = 24000


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeAsr:
    is_loaded = True
    current_model = "Fake-ASR"

    def __init__(self):
        self.calls = 0

    def recognize(self, x, sr):
        self.calls += 1
        return SimpleNamespace(text=f"words {self.calls}")


def _talk(seconds_each=9.0, n=4, pause=0.6):
    out = []
    for i in range(n):
        t = np.arange(int(seconds_each * SR)) / SR
        env = 0.04 + 0.96 * np.sin(2 * np.pi * 3.0 * t) ** 2
        out.append((0.2 * env * np.sin(2 * np.pi * 150 * t)).astype(np.float32))
        out.append((0.0008 * np.random.default_rng(i).standard_normal(int(pause * SR))).astype(np.float32))
    return np.concatenate(out)


@pytest.fixture
def studio(qapp, isolated_home, monkeypatch):
    from thundertalk.ui.pages.studio_page import StudioPage
    toasts = []
    page = StudioPage(SimpleNamespace(microphone="auto"))
    page.toast_requested.connect(lambda m, k: toasts.append((k, m)))
    page.set_engine(FakeAsr())
    page.resize(900, 900)
    page.show()
    QApplication.processEvents()
    page._toasts = toasts
    yield page
    page.shutdown()
    page.close()


# ── transcribe ───────────────────────────────────────────────────────────

def test_transcribe_a_file_end_to_end(studio, tmp_path):
    wav = tmp_path / "meeting.wav"
    audio_io.write_wav(str(wav), _talk(), SR)
    tab = studio.transcribe_tab
    assert not tab._go.isEnabled()                              # no file yet
    tab.load_file(str(wav))
    assert tab._go.isEnabled() and "meeting.wav" in tab._drop._title.text()
    tab._go.click()
    assert wait_for(lambda: tab._result.isVisible())
    assert tab._transcript is not None and len(tab._transcript.segments) >= 2
    assert "faster than real time" in tab._stats.text() and "Fake-ASR" in tab._stats.text()
    assert tab._go.isVisible() and not tab._cancel.isVisible() and not tab._bar.isVisible()


def test_transcribe_export_and_copy(studio, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    tab = studio.transcribe_tab
    tab._on_done(tr.Transcript([tr.Segment(0, 2, "Hello there.", "S01"), tr.Segment(2, 4, "Hi.", "S02")],
                               4.0, "MOSS-Transcribe-Diarize", 0.4, has_speakers=True))
    out = tmp_path / "talk.srt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    tab._export("srt", ".srt")
    assert "S01: Hello there." in out.read_text(encoding="utf-8")
    tab._copy()
    assert "Hello there." in QApplication.clipboard().text()
    assert any(k == "success" for k, _ in studio._toasts)


def test_speaker_rename_updates_chips(studio, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    tab = studio.transcribe_tab
    tab._on_done(tr.Transcript([tr.Segment(0, 2, "Hello.", "S01")], 2.0, "MOSS", 0.1, has_speakers=True))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Allen", True)))
    tab._rename_speaker("S01")
    assert tab._view._chips[0][0].text() == "Allen"
    assert "Allen: Hello." in tab._transcript.to_text()


def test_undecodable_file_shows_a_friendly_error(studio, tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"not audio" * 50)
    tab = studio.transcribe_tab
    tab.load_file(str(bad))
    tab._go.click()
    assert wait_for(lambda: studio._toasts)
    kind, msg = studio._toasts[-1]
    assert kind == "error" and "Couldn't read" in msg
    assert tab._go.isVisible()                                   # controls come back


def test_speakers_mode_asks_for_the_model_first(studio, tmp_path, monkeypatch):
    from thundertalk.ui.studio import transcribe_tab as tt
    monkeypatch.setattr(tt, "is_downloaded", lambda _id: False)
    tab = studio.transcribe_tab
    tab._mode.set_current("speakers")
    tab._refresh()
    assert tab._notice.isVisible() and "download" in tab._notice_text.text().lower()
    assert not tab._go.isEnabled()


def test_no_dictation_model_points_to_models_page(studio):
    tab = studio.transcribe_tab
    tab.set_engine(SimpleNamespace(is_loaded=False, current_model=""))
    assert tab._notice.isVisible()
    got = []
    tab.navigate.connect(got.append)
    tab._notice_btn.click()
    assert got == ["models"]


# ── speak ────────────────────────────────────────────────────────────────

class FakeTts:
    def __init__(self):
        self.calls = []
        self.kw = []

    def synthesize(self, text, voice, language=None, speed=1.0, progress=None, cancel=None,
                   verifier=None, **kw):
        self.calls.append((text, voice, language, speed, verifier is not None))
        self.kw.append(kw)
        n = 3
        for i in range(n):
            if progress:
                progress(i, n, "x")
        t = np.arange(SR * 2) / SR
        return tts.SynthResult((0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), SR, language or "english",
                               [tts.SegmentReport("x", 2, 2, 1, True)], 0.5)

    def unload(self):
        pass


def set_ready(monkeypatch, **ready):
    """Pretend speech engines are (not) downloaded: set_ready(mp, voxcpm2=True, kokoro=False)."""
    from thundertalk.core import speech
    for bid in speech.BACKEND_ORDER:
        monkeypatch.setattr(speech.backend(bid), "is_ready", lambda v=ready.get(bid, True): v)


@pytest.fixture
def speak(studio, monkeypatch):
    from thundertalk.core import speech
    set_ready(monkeypatch)
    fake = FakeTts()
    monkeypatch.setattr(speech, "get_engine", lambda: fake)
    studio.show_tab("speak")
    tab = studio.speak_tab
    tab.refresh()
    tab._fake = fake
    return tab


def pick_engine(tab, bid):
    tab._engine_pick.set_current(bid)
    tab._on_engine(bid)


def test_engine_picker_shows_that_engines_voices(speak):
    from thundertalk.core import speech
    for bid in speech.BACKEND_ORDER:
        pick_engine(speak, bid)
        ids = {v.id for v in speech.backend(bid).voices()}
        builtin = {k for k in speak._chips if not k.startswith("my:")}
        assert builtin == ids                                   # only this engine's voices
        assert speak._voice_id in ids                           # switching picks one of them
    pick_engine(speak, "kokoro")
    speak._chips["kokoro:58"].click()
    assert speak._voice_id == "kokoro:58" and speak._chips["kokoro:58"].isChecked()


def test_kokoro_hides_my_voices(speak):
    voices.VoiceLibrary().add("Me", voices.prepare_reference(_talk(6.0, 1), SR).audio, "hi")
    pick_engine(speak, "kokoro")
    assert "my:me" not in speak._chips and speak._no_clone.isVisibleTo(speak)
    pick_engine(speak, "indextts")
    assert "my:me" in speak._chips and not speak._no_clone.isVisibleTo(speak)


def test_generate_speech_end_to_end(speak):
    pick_engine(speak, "voxcpm2")
    speak._chips["voxcpm2:male-en"].click()
    assert not speak._go.isEnabled()                            # empty text
    speak._text.setPlainText("Hello from ThunderTalk. This is a test of the speech pipeline.")
    assert speak._go.isEnabled() and "characters" in speak._count.text()
    speak._go.click()
    assert wait_for(lambda: speak._result_card.isVisible())
    text, voice, lang, speed, has_verifier = speak._fake.calls[0]
    assert voice == "voxcpm2:male-en" and lang == "auto" and speed == 1.0 and has_verifier
    assert speak._player.player.has_audio and speak._player.player.duration == pytest.approx(2.0, abs=0.01)
    assert speak._go.isVisible() and not speak._cancel.isVisible()


def test_each_engine_offers_its_own_download(studio, monkeypatch):
    set_ready(monkeypatch, voxcpm2=False, indextts=False, kokoro=True)
    studio.show_tab("speak")
    tab = studio.speak_tab
    tab.refresh()
    pick_engine(tab, "kokoro")
    assert not tab._engine_card.isVisible()                     # Kokoro is on disk
    pick_engine(tab, "voxcpm2")
    assert tab._engine_card.isVisible()
    assert "VoxCPM2" in tab._engine_title.text() and "GB" in tab._dl_btn.text()
    tab._text.setPlainText("hello")
    assert not tab._go.isEnabled()


def test_my_voice_is_spoken_by_the_selected_engine(studio, monkeypatch):
    from thundertalk.core import speech
    set_ready(monkeypatch, voxcpm2=True, indextts=False)
    voices.VoiceLibrary().add("Me", voices.prepare_reference(_talk(6.0, 1), SR).audio, "some words here")
    studio.show_tab("speak")
    tab = studio.speak_tab
    tab.refresh()
    pick_engine(tab, "voxcpm2")
    tab._chips["my:me"].click()
    assert not tab._engine_card.isVisible()
    pick_engine(tab, "indextts")                                # keeps my voice, needs IndexTTS now
    assert tab._voice_id == "my:me"
    assert tab._engine_card.isVisible() and "IndexTTS" in tab._engine_title.text()
    set_ready(monkeypatch)
    fake = FakeTts()
    monkeypatch.setattr(speech, "get_engine", lambda: fake)
    tab.refresh()
    tab._text.setPlainText("Say this in my voice, please.")
    tab._go.click()
    assert wait_for(lambda: tab._result_card.isVisible())
    voice = fake.calls[0][1]
    assert isinstance(voice, tts.ClonePrompt) and voice.text == "some words here"
    assert fake.kw[0]["clone_backend"] == "indextts"


def test_save_exports_wav(speak, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    speak._text.setPlainText("Please save this sentence to disk.")
    speak._go.click()
    assert wait_for(lambda: speak._result_card.isVisible())
    out = tmp_path / "speech.wav"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    speak._save("wav")
    with wave.open(str(out)) as w:
        assert w.getframerate() == SR and w.getnframes() == SR * 2


def test_shutdown_cancels_a_running_transcription(studio, tmp_path):
    wav = tmp_path / "long.wav"
    audio_io.write_wav(str(wav), _talk(9.0, 8), SR)

    class SlowAsr(FakeAsr):
        def recognize(self, x, sr):
            time.sleep(0.25)
            return super().recognize(x, sr)

    tab = studio.transcribe_tab
    tab.set_engine(SlowAsr())
    tab.load_file(str(wav))
    tab._go.click()
    assert wait_for(lambda: tab._worker is not None and tab._worker.isRunning())
    t0 = time.time()
    studio.shutdown()
    assert time.time() - t0 < 5 and not tab._worker.isRunning()


def test_language_switch_rebuilds_labels(studio):
    from thundertalk.core import i18n
    i18n.LANG = "zh"
    try:
        studio.retranslate()
        assert studio._tabs.current() == "transcribe"
        assert "转写" in [lbl for _, lbl in studio._tabs._options][0]
        assert studio.speak_tab._lang.itemText(0) == "自动识别"
    finally:
        i18n.LANG = "en"
        studio.retranslate()


def test_drop_anywhere_switches_to_transcribe(studio, tmp_path):
    studio.show_tab("speak")
    wav = tmp_path / "a.wav"
    audio_io.write_wav(str(wav), _talk(3.0, 1), SR)
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(wav))])
    studio.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier))
    assert studio._tabs.current() == "transcribe"
    assert studio.transcribe_tab._path == str(wav)


# ── remembered Speak choices (#3) ────────────────────────────────────────

def test_speak_remembers_voice_language_and_speed(qapp, isolated_home, monkeypatch):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.studio.speak_tab import SpeakTab
    set_ready(monkeypatch)
    s = Settings()
    tab = SpeakTab(s)
    pick_engine(tab, "kokoro")
    tab._chips["kokoro:58"].click()
    tab._lang.setCurrentIndex(tab._lang.findData("english"))
    tab._speed.setCurrentIndex(4)                              # 1.3x
    again = SpeakTab(Settings())                               # fresh read from disk
    assert again._voice_id == "kokoro:58" and again._chips["kokoro:58"].isChecked()
    assert again._engine_pick.current() == "kokoro"
    assert again._lang.currentData() == "english"
    assert again._speed.currentData() == pytest.approx(1.3)


def test_speak_falls_back_when_the_remembered_voice_was_deleted(qapp, isolated_home, monkeypatch):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.studio.speak_tab import SpeakTab
    set_ready(monkeypatch)
    s = Settings()
    s.set("studio_voice", "my:gone")
    tab = SpeakTab(s)
    from thundertalk.ui.studio.speak_tab import default_voice
    assert tab._voice_id == default_voice()


# ── player keyboard (#4) ─────────────────────────────────────────────────

def _key(widget, key):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_player_space_and_arrow_keys(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from thundertalk.ui.studio.parts import PlayerBar
    bar = PlayerBar()
    calls = []
    monkeypatch.setattr(bar, "toggle", lambda: calls.append("toggle"))
    bar.set_audio(np.zeros(SR * 20, np.float32), SR)
    monkeypatch.setattr(bar._player, "_default_rate", staticmethod(lambda: SR))
    _key(bar, Qt.Key.Key_Space)
    assert calls == ["toggle"]
    _key(bar, Qt.Key.Key_Right)
    assert bar.player.position == pytest.approx(5.0, abs=0.05)
    for _ in range(4):
        _key(bar, Qt.Key.Key_Right)
    assert bar.player.position == pytest.approx(20.0, abs=0.05)             # clamped at the end
    _key(bar, Qt.Key.Key_Left)
    assert bar.player.position == pytest.approx(15.0, abs=0.05)
    for _ in range(5):
        _key(bar, Qt.Key.Key_Left)
    assert bar.player.position == pytest.approx(0.0, abs=0.05)              # clamped at the start


# ── transcription time remaining (#5) ────────────────────────────────────

def test_eta_appears_after_two_parts_and_counts_down(studio):
    tab = studio.transcribe_tab
    tab._eta_base, tab._eta_done, tab._eta_total = None, 0, 0
    tab._note_part(1, 10, now=0.0)
    tab._note_part(2, 10, now=9.0)                  # slow first part (warm-up) is ignored
    assert tab.eta_seconds(now=9.5) is None          # only one part timed so far
    tab._note_part(3, 10, now=12.0)                  # 3 s per part from here on
    assert tab.eta_seconds(now=12.0) == pytest.approx(3.0 * 8)
    assert tab.eta_seconds(now=14.0) == pytest.approx(3.0 * 8 - 2.0)
    tab._note_part(10, 10, now=33.0)
    assert tab.eta_seconds(now=34.0) == pytest.approx(3.0 - 1.0)


# ── voice previews ───────────────────────────────────────────────────────

class FakePlayer:
    """Stands in for playback.Player: no audio hardware."""

    def __init__(self):
        self.loaded, self.is_playing = None, False

    def load(self, x, sr):
        self.loaded = (len(x), sr)

    def play(self, start=None):
        self.is_playing = True
        return True

    def stop(self):
        self.is_playing = False

    def poll(self):
        return self.is_playing


def _click_preview(chip):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton, pos=QPoint(chip.width() - 16, chip.height() // 2))


def test_every_built_in_voice_has_a_shipped_preview():
    from thundertalk.core import speech
    from thundertalk.core.tts_backends.previews import load_preview
    for bid in speech.BACKEND_ORDER:
        for v in speech.backend(bid).voices():
            clip = load_preview(v.id)
            assert clip is not None, v.id
            x, sr = clip
            assert sr == 24000 and 1.5 < len(x) / sr < 12 and np.max(np.abs(x)) > 0.3, v.id


def test_preview_plays_without_selecting_and_toggles(speak):
    speak._preview = FakePlayer()
    pick_engine(speak, "kokoro")
    before = speak._voice_id
    chip = speak._chips["kokoro:58"]
    _click_preview(chip)
    assert speak._preview.is_playing and speak._preview.loaded[1] == 24000
    assert chip.is_playing() and speak._voice_id == before           # listening doesn't select
    _click_preview(speak._chips["kokoro:3"])                         # another voice takes over
    assert not chip.is_playing() and speak._chips["kokoro:3"].is_playing()
    _click_preview(speak._chips["kokoro:3"])                         # second click stops
    assert not speak._preview.is_playing and not speak._chips["kokoro:3"].is_playing()
    chip.click()                                                     # the rest of the chip still selects
    assert speak._voice_id == "kokoro:58"


def test_preview_of_my_voice_plays_my_recording_and_ends_cleanly(speak):
    voices.VoiceLibrary().add("Me", voices.prepare_reference(_talk(6.0, 1), SR).audio, "hi")
    speak._preview = FakePlayer()
    pick_engine(speak, "voxcpm2")
    _click_preview(speak._chips["my:me"])
    assert speak._preview.is_playing and speak._preview.loaded[1] == 24000
    speak._preview.is_playing = False                                # clip finished
    assert wait_for(lambda: not speak._chips["my:me"].is_playing())


def test_preview_stops_when_generating_or_switching_engine(speak):
    speak._preview = FakePlayer()
    pick_engine(speak, "voxcpm2")
    _click_preview(speak._chips["voxcpm2:male-en"])
    pick_engine(speak, "indextts")
    assert not speak._preview.is_playing
    _click_preview(speak._chips["indextts:male-en"])
    speak._text.setPlainText("Hello there.")
    speak._go.click()
    assert not speak._preview.is_playing
    assert wait_for(lambda: speak._result_card.isVisible())
