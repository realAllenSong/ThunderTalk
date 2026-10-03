"""Studio page behaviour with fake engines: real widgets, real worker threads,
no models and no audio hardware."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
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
    tab._model_picker.blockSignals(True)
    tab._model_picker.setCurrentIndex(tab._model_picker.findData(tt.MOSS_ID))
    tab._model_picker.blockSignals(False)
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


# ── managing my voices: per-voice ⋯ menu and batch delete ───────────────

class FakeMenu:
    """Stands in for QMenu: records actions; exec() runs the one named in `choose`."""
    choose = ""

    def __init__(self, *_a):
        self.actions = []

    def addAction(self, text, fn=None):
        self.actions.append((text, fn))

    def addSeparator(self):
        pass

    def exec(self, *_a):
        for text, fn in self.actions:
            if text == FakeMenu.choose:
                fn()


def _add_voices(*names):
    lib = voices.VoiceLibrary()
    return [lib.add(n, voices.prepare_reference(_talk(6.0, 1), SR).audio, "some words") for n in names]


def test_voice_menu_acts_on_that_voice_without_selecting_it(speak, monkeypatch):
    from thundertalk.ui.studio import speak_tab
    from thundertalk.ui import styled_dialog
    a, b = _add_voices("Alpha", "Beta")
    pick_engine(speak, "voxcpm2")
    speak._chips["voxcpm2:male-en"].click()                      # a built-in voice is selected
    monkeypatch.setattr(speak_tab, "QMenu", FakeMenu)
    monkeypatch.setattr(speak_tab.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Gamma", True)))
    FakeMenu.choose = speak_tab.t("studio.voices.rename")
    speak._chips["my:" + b.id].menu_requested.emit()
    assert voices.VoiceLibrary().get(b.id).name == "Gamma"
    assert speak._voice_id == "voxcpm2:male-en"                  # selection untouched
    monkeypatch.setattr(styled_dialog.StyledDialog, "confirm", staticmethod(lambda *a, **k: True))
    FakeMenu.choose = speak_tab.t("studio.voices.delete")
    speak._chips["my:" + a.id].menu_requested.emit()
    assert [v.id for v in voices.VoiceLibrary().list()] == [b.id]
    assert "my:" + a.id not in speak._chips and speak._voice_id == "voxcpm2:male-en"


def test_batch_select_and_delete(speak, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from thundertalk.ui import styled_dialog
    vs = _add_voices("One", "Two", "Three")
    pick_engine(speak, "indextts")
    assert speak._select_btn.isVisibleTo(speak) and not speak._del_sel_btn.isVisibleTo(speak)
    speak._select_btn.click()
    assert speak._del_sel_btn.isVisibleTo(speak) and not speak._del_sel_btn.isEnabled()
    assert speak._add_chip is not None and not speak._add_chip.isVisibleTo(speak)
    before = speak._voice_id
    for v in vs[:2]:                                             # a click ticks, it doesn't select
        c = speak._chips["my:" + v.id]
        QTest.mouseClick(c, Qt.MouseButton.LeftButton, pos=QPoint(20, c.height() // 2))
    assert speak._voice_id == before and speak._del_sel_btn.isEnabled() and "2" in speak._del_sel_btn.text()
    asked = {}
    monkeypatch.setattr(styled_dialog.StyledDialog, "confirm",
                        staticmethod(lambda *a, **k: asked.setdefault("title", k["title"]) or True))
    speak._del_sel_btn.click()
    assert "2" in asked["title"]
    assert [v.id for v in voices.VoiceLibrary().list()] == [vs[2].id]
    speak._all_btn.click()
    speak._del_sel_btn.click()
    assert voices.VoiceLibrary().list() == []
    assert not speak._select_btn.isVisibleTo(speak) and not speak._done_btn.isVisibleTo(speak)


# ── transcribe from a link ───────────────────────────────────────────────

@pytest.fixture
def fake_link(monkeypatch):
    import yt_dlp

    from tests.test_links_burn import FakeYDL
    from thundertalk.core import links
    FakeYDL.errors, FakeYDL.info, FakeYDL.calls, FakeYDL.opts_seen = [], {}, 0, []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(links.time, "sleep", lambda s: None)
    return FakeYDL


def test_transcribe_a_link_end_to_end(studio, fake_link, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    tab = studio.transcribe_tab
    tab._link.setText("看看这个 https://www.youtube.com/watch?v=abc123 很有意思")
    tab._link_btn.click()
    assert tab._url == "https://www.youtube.com/watch?v=abc123" and tab._link.text() == ""
    assert "youtube.com" in tab._drop._sub.text() and tab._go.isEnabled() and not tab.queue_mode()
    tab._go.click()
    assert wait_for(lambda: tab._result.isVisible() and not tab.busy())
    assert tab._transcript.title == "My Talk: part 1/2" and tab._url == ""
    assert "My Talk" in tab._stats.text() and not tab._burn_btn.isVisible()     # audio only: nothing to burn
    asked = {}
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda _p, _c, path, _f: asked.setdefault("path", path) and ("", "")))
    tab._export("md", ".md")
    assert asked["path"].endswith("My Talk part 1 2.md")
    assert tab._summary_btn.isVisibleTo(tab)


def test_link_errors_are_friendly(studio, fake_link):
    fake_link.errors = ["ERROR: [youtube] abc: Private video. Sign in if you've been granted access"]
    tab = studio.transcribe_tab
    tab.add_links("https://youtu.be/abc")
    tab._go.click()
    assert wait_for(lambda: studio._toasts and not tab.busy())
    kind, msg = studio._toasts[-1]
    assert kind == "error" and "private" in msg.lower()
    tab._link.setText("not a link at all")
    tab._on_add_link()
    assert studio._toasts[-1][0] == "warn" and tab._link.text() == "not a link at all"


# ── queue ────────────────────────────────────────────────────────────────

class CountingAsr(FakeAsr):
    """Fails the test if two recognitions ever overlap; optionally slow."""

    def __init__(self, delay=0.0):
        super().__init__()
        self.delay, self.active, self.max_active = delay, 0, 0

    def recognize(self, x, sr):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        self.active -= 1
        return super().recognize(x, sr)


def _wavs(folder, names, seconds=9.0, n=2):
    folder.mkdir(exist_ok=True)
    out = []
    for name in names:
        p = folder / name
        audio_io.write_wav(str(p), _talk(seconds, n), SR)
        out.append(str(p))
    return out


def test_queue_of_three_files_runs_one_at_a_time(studio, tmp_path):
    from thundertalk.core import i18n
    tab = studio.transcribe_tab
    asr = CountingAsr(0.02)
    tab.set_engine(asr)
    paths = _wavs(tmp_path / "in", ["a.wav", "b.wav", "c.wav"])
    tab.add_files(paths)
    assert tab.queue_mode() and len(tab._rows) == 3 and tab._queue.isVisible()
    assert tab._go.text() == "Transcribe all (3)" and tab._go.isEnabled()
    i18n.LANG = "zh"
    try:
        tab.retranslate()
        assert "全部转写" in tab._go.text()
    finally:
        i18n.LANG = "en"
        tab.retranslate()
    tab._go.click()
    assert tab._cancel.text() == "Cancel all"
    assert wait_for(lambda: not tab.busy(), 20)
    assert tab._rows == [] and asr.max_active == 1
    for p in paths:
        assert Path(p).with_suffix(".txt").is_file() and Path(p).with_suffix(".srt").is_file()
    assert "words" in Path(paths[1]).with_suffix(".txt").read_text(encoding="utf-8")
    assert studio._toasts[-1] == ("success", "Queue finished: 3 of 3 saved")
    assert tab._result.isVisible() and tab._history_list.count() == 3
    tab._open_history(tab._history_list.item(1))
    assert "words" in tab._transcript.to_text()
    assert not tab._go.isEnabled()


def test_queue_to_a_folder_with_a_link_and_one_cancelled(studio, tmp_path, fake_link, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    tab = studio.transcribe_tab
    gate = threading.Event()

    class GatedAsr(CountingAsr):
        def recognize(self, x, sr):
            assert gate.wait(10)
            return super().recognize(x, sr)
    tab.set_engine(GatedAsr(0.05))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out)))
    tab.add_files(_wavs(tmp_path / "in", ["a.wav", "b.wav"]))
    tab.add_links("https://www.bilibili.com/video/BV1abc")
    assert [r.item.is_url for r in tab._rows] == [False, False, True]
    tab._dest.set_current("folder")
    tab._on_dest("folder")
    tab._toggle_format("md", True)
    tab._toggle_format("txt", False)
    assert tab._formats == ["srt", "md"] or tab._formats == ["md", "srt"]
    a, b, _link = tab._rows                                  # finished rows leave tab._rows
    tab._go.click()
    assert wait_for(lambda: a.state == "running")
    b._x.click()                                             # skip the waiting one
    gate.set()
    assert wait_for(lambda: not tab.busy(), 20)
    assert tab._rows == [b] and b.state == "cancelled"
    assert tab._transcript.title == "My Talk: part 1/2"
    assert tab._history_list.count() == 2
    names = sorted(p.name for p in out.iterdir())
    assert names == ["My Talk part 1 2.md", "My Talk part 1 2.srt", "a.md", "a.srt"]
    assert studio._toasts[-1][0] == "warn"


def test_cancel_all_stops_the_queue(studio, tmp_path):
    tab = studio.transcribe_tab
    tab.set_engine(CountingAsr(0.25))
    tab.add_files(_wavs(tmp_path / "in", ["a.wav", "b.wav", "c.wav"], 9.0, 4))
    tab._go.click()
    assert wait_for(lambda: tab._rows[0].state == "running")
    tab._cancel.click()
    assert wait_for(lambda: not tab.busy(), 10)
    assert [r.state for r in tab._rows] == ["cancelled"] * 3
    assert not any(p.suffix == ".txt" for p in (tmp_path / "in").iterdir())


def test_single_file_flow_is_unchanged_and_queue_collapses(studio, tmp_path):
    tab = studio.transcribe_tab
    a, b = _wavs(tmp_path / "in", ["a.wav", "b.wav"], 3.0, 1)
    tab.add_files([a])                                       # one file: the old single flow
    assert not tab.queue_mode() and tab._path == a
    tab.load_file(b)                                         # choosing another replaces it
    assert tab._path == b and not tab.queue_mode()
    tab.add_files([a, b])
    assert tab.queue_mode() and tab._path == ""
    tab._rows[0]._x.click()                                  # remove one → back to a single file
    assert not tab.queue_mode() and tab._path == b and "b.wav" in tab._drop._title.text()


def test_dropping_a_browser_link_on_the_page(studio, fake_link):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    md = QMimeData()
    md.setUrls([QUrl("https://www.youtube.com/watch?v=abc123")])
    studio.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier))
    assert studio.transcribe_tab._url == "https://www.youtube.com/watch?v=abc123"


# ── burn subtitles into a video ──────────────────────────────────────────

def test_burn_is_offered_for_local_videos_and_explains_missing_ffmpeg(studio, tmp_path, monkeypatch):
    from thundertalk.ui import styled_dialog
    tab = studio.transcribe_tab
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\0" * 10)
    t = tr.Transcript([tr.Segment(0, 1, "hi")], 1.0, "Fake", 0.1)
    tab._show_result(t, str(clip))
    assert tab._burn_btn.isVisible()
    tab._show_result(t, str(tmp_path / "talk.wav"))
    assert not tab._burn_btn.isVisible()
    tab._show_result(t, str(clip))
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(styled_dialog.StyledDialog, "confirm", staticmethod(lambda *a, **k: True))
    tab.burn_subtitles()
    assert QApplication.clipboard().text() == "brew install ffmpeg" and not tab.busy()


@pytest.mark.skipif(audio_io.find_ffmpeg() is None, reason="needs ffmpeg")
def test_burn_from_the_result_card(studio, tmp_path):
    import subprocess
    ff = audio_io.find_ffmpeg()
    clip, out = tmp_path / "clip.mov", tmp_path / "clip (subtitled).mov"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=300", "-t", "3", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    tab = studio.transcribe_tab
    tab._show_result(tr.Transcript([tr.Segment(0.2, 2.5, "你好，字幕")], 3.0, "Fake", 0.1), str(clip))
    tab.burn_subtitles(soft=True, out=str(out))
    assert tab.busy() and tab._cancel.isVisible()
    assert wait_for(lambda: not tab.busy(), 20)
    assert out.is_file() and studio._toasts[-1][0] == "success" and "clip (subtitled).mov" in studio._toasts[-1][1]
    assert tab._go.isVisible() and not tab._cancel.isVisible()


def test_done_leaves_pick_mode_and_keeps_voices(speak):
    vs = _add_voices("Keep")
    pick_engine(speak, "voxcpm2")
    speak._select_btn.click()
    speak._chips["my:" + vs[0].id].set_picked(True)
    speak._done_btn.click()
    assert speak._select_btn.isVisibleTo(speak) and not speak._done_btn.isVisibleTo(speak)
    assert len(voices.VoiceLibrary().list()) == 1 and not speak._chips["my:" + vs[0].id].is_picked()


def test_history_reopens_after_restart_and_preserves_edits(studio, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog
    from thundertalk.ui.studio.transcribe_tab import TranscribeTab
    tab = studio.transcribe_tab
    tab._result_src = str(tmp_path / "meeting.mp4")
    transcript = tr.Transcript([tr.Segment(0, 2, "Searchable discussion", "S01")], 2, "MOSS",
                               has_speakers=True, notes="## Notes\nA decision")
    tab._on_done(transcript)
    tab._history_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Renamed meeting", True)))
    tab._rename_history()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Host", True)))
    tab._rename_speaker("S01")
    tab._transcript.notes = "## Notes\nUpdated decision"
    tab._save_history(tab._transcript, tab._result_src)
    other = TranscribeTab()
    other._open_history(other._history_list.item(0))
    assert other._transcript.title == "Renamed meeting"
    assert other._transcript.speaker_names == {"S01": "Host"}
    assert other._transcript.notes.endswith("Updated decision")
    assert other._result_src == str(tmp_path / "meeting.mp4")
    assert other._export_stem() == "Renamed meeting"
    other._history_search.setText("discussion")
    other._reload_history()
    assert other._history_list.count() == 1
    other._history_search.setText("missing")
    other._reload_history()
    assert other._history_list.count() == 0
    from thundertalk.ui.styled_dialog import StyledDialog
    monkeypatch.setattr(StyledDialog, "confirm", staticmethod(lambda *a, **k: True))
    other._history_search.clear()
    other._reload_history()
    other._history_list.setCurrentRow(0)
    other._delete_history()
    assert other._history_list.count() == 0 and other._transcript is None
    other.close()


def test_model_picker_defaults_to_dictation_and_offers_installed_asr_only(studio, monkeypatch):
    from thundertalk.ui.studio import transcribe_tab as tt
    monkeypatch.setattr(tt, "is_downloaded", lambda _id: True)
    tab = studio.transcribe_tab
    tab.set_engine(SimpleNamespace(is_loaded=True, current_model="qwen3-asr-06b-int8"))
    assert tab._model_picker.currentData() == "qwen3-asr-06b-int8"
    ids = [tab._model_picker.itemData(i) for i in range(tab._model_picker.count())]
    assert tt.MOSS_ID in ids and "sensevoice-small-int8" in ids
    assert not any("seamless" in i for i in ids)
    assert tab._label_speakers.isHidden()
    tab._model_picker.setCurrentIndex(tab._model_picker.findData(tt.MOSS_ID))
    assert not tab._label_speakers.isHidden() and tab._speakers_mode()
    assert "who said what" in tab._mode_desc.text()
    tab._label_speakers.setChecked(False)
    assert not tab._speakers_mode()


def test_selecting_missing_moss_starts_existing_download_flow(studio, monkeypatch):
    from thundertalk.ui.studio import transcribe_tab as tt
    monkeypatch.setattr(tt, "is_downloaded", lambda _id: False)
    tab = studio.transcribe_tab
    tab._reload_models()
    got = []
    monkeypatch.setattr(tab, "_download_moss", lambda: got.append(True))
    index = tab._model_picker.findData(tt.MOSS_ID)
    assert "Download" in tab._model_picker.itemText(index)
    tab._model_picker.setCurrentIndex(index)
    assert got == [True]


def test_success_resets_single_file_and_retains_result(studio, tmp_path):
    tab = studio.transcribe_tab
    path = _wavs(tmp_path / "in", ["short.wav"], 3, 1)[0]
    tab.load_file(path)
    tab._go.click()
    assert wait_for(lambda: not tab.busy())
    assert not tab._path and not tab._url and not tab._drop._path
    assert tab._result.isVisible() and tab._result_src == path
    assert tab._history_list.count() == 1 and not tab._go.isEnabled()


def test_failed_queue_item_stays_and_can_retry(studio, tmp_path, monkeypatch):
    tab = studio.transcribe_tab
    def one(path, *a, **kw):
        if path.endswith("bad.wav"):
            raise RuntimeError("no_speech")
        return tr.Transcript([tr.Segment(0, 1, "Good")], 1, "Fake")
    monkeypatch.setattr(tr, "transcribe_file", one)
    tab.add_files([str(tmp_path / "good.wav"), str(tmp_path / "bad.wav")])
    tab._go.click()
    assert wait_for(lambda: not tab.busy())
    assert len(tab._rows) == 1 and tab._rows[0].state == "failed"
    assert tab._go.isEnabled() and tab._history_list.count() == 1
    monkeypatch.setattr(tr, "transcribe_file", lambda *a, **kw: tr.Transcript([tr.Segment(0, 1, "Retry")], 1, "Fake"))
    tab._go.click()
    assert wait_for(lambda: not tab.busy())
    assert tab._rows == [] and tab._transcript.to_text() == "Retry"
