"""Studio ▸ Compose form and generate / cancel flow with real widgets and a fake worker."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from thundertalk.core import i18n, music
from thundertalk.core.i18n import t
from thundertalk.ui.studio.compose_tab import ComposeTab


class FakeWorker(QObject):
    event = Signal(object)
    take = Signal(int, str, object)
    done = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()

    made: list["FakeWorker"] = []

    def __init__(self, request, ram_need_mb):
        super().__init__()
        self.request, self.ram_need_mb = request, ram_need_mb
        self.started = self.was_cancelled = False
        FakeWorker.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.was_cancelled = True

    def wait(self, _ms):
        return True


class Prefs(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture
def ready(monkeypatch):
    state = {"missing": []}
    monkeypatch.setattr(music, "missing", lambda m, planner: list(state["missing"]))
    return state


@pytest.fixture
def make(qapp, isolated_home, ready):
    FakeWorker.made.clear()
    tabs = []

    def build(memory_gb=36, settings=None):
        tab = ComposeTab(settings=settings, memory_gb=memory_gb, make_worker=FakeWorker)
        tabs.append(tab)
        return tab
    yield build
    for tab in tabs:
        tab.deleteLater()


def _song(tab, lyrics="[verse]\nwalking home in the rain\n[chorus]\nla la la"):
    tab._style.setPlainText("warm indie pop, acoustic guitar")
    tab._lyrics.setPlainText(lyrics)


def test_compose_waits_for_the_download(make, ready):
    ready["missing"] = [music.VAE, music.TURBO.info.downloads[0]]
    tab = make()
    _song(tab)
    assert not tab._go.isEnabled()
    assert not tab._dl_btn.isHidden()
    assert "5.1 GB" in tab._dl_btn.text()          # 338 MB VAE + 4,792 MB DiT still missing
    ready["missing"] = []
    tab.refresh()
    assert tab._go.isEnabled() and tab._dl_btn.isHidden()
    assert tab._dl_status.text() == t("studio.compose.installed")


def test_song_needs_lyrics_and_background_music_needs_a_style(make):
    tab = make()
    tab._style.setPlainText("lofi beats, rainy night")
    assert not tab._go.isEnabled()                 # song mode, no lyrics yet
    tab._lyrics.setPlainText("[verse]\nmy own line")
    assert tab._go.isEnabled()
    tab._mode.set_current("bgm")
    tab._on_mode("bgm")
    assert tab._lyrics_host.isHidden() and tab._lang.isHidden()
    tab._style.setPlainText("")
    assert not tab._go.isEnabled()
    tab._style.setPlainText("x" * (music.STYLE_MAX_CHARS + 1))
    assert not tab._go.isEnabled()                 # over the style limit


def test_request_follows_the_form(make, isolated_home):
    tab = make()
    _song(tab)
    tab._length.set_value(95)
    tab._bpm.set_value(92)
    tab._seed.set_value(1000)
    tab._takes.setCurrentIndex(2)                  # 3 takes
    tab._lang.setCurrentIndex(tab._lang.findData("en"))
    tab._generate()
    w = FakeWorker.made[-1]
    r = w.request
    assert w.started
    assert r.duration == 95 and r.bpm == 92 and r.seeds == [1000, 1001, 1002]
    assert r.lyrics.startswith("[verse]") and not r.instrumental and r.vocal_language == "en"
    assert r.model_folder == music.XL.folder and r.steps == 50       # 36 GB Mac → XL recommended
    assert r.dit_backend == "mlx"
    assert r.job_dir.startswith(str(isolated_home / "Music" / "ThunderTalk"))
    assert r.project_root == str(isolated_home / ".thundertalk" / "models" / "ace-step")
    assert w.ram_need_mb == music.ram_need_mb(music.XL, r.planner)
    assert r.estimate_s == pytest.approx(music.estimate_take_s(music.XL, 95, r.planner))


def test_background_music_sends_no_lyrics(make):
    tab = make()
    tab._style.setPlainText("ambient pads, slow")
    tab._lyrics.setPlainText("[verse]\nleft over")
    tab._mode.set_current("bgm")
    tab._on_mode("bgm")
    tab._generate()
    r = FakeWorker.made[-1].request
    assert r.instrumental and r.lyrics == "" and r.vocal_language == "unknown"


def test_small_mac_tier(make):
    tab = make(memory_gb=16)
    assert tab._model_pick.current() == music.TURBO.info.id
    assert tab._planner.isHidden() and not tab._planner_on()
    assert tab._length.bounds() == (music.MIN_SECONDS, music.SMALL_MAC_MAX_SECONDS)
    assert tab._takes.currentData() == 1
    assert "32 GB" in tab._needs.text()
    _song(tab)
    tab._generate()
    worker = FakeWorker.made[-1]
    assert worker.request.dit_backend == "mps"
    assert worker.ram_need_mb == music.TURBO.mps_ram_mb
    assert "11.6 GiB" in tab._needs.text()
    assert t("studio.compose.repaint_memory") in tab._needs.text()
    assert not tab._repaint_fits(music.TURBO, "mps")


def test_xl_caps_the_default_length_and_turbo_allows_ten_minutes(make):
    tab = make(memory_gb=64)
    assert tab._length.bounds()[1] == music.XL_DEFAULT_MAX_SECONDS
    tab._model_pick.set_current(music.TURBO.info.id)
    tab._on_model(music.TURBO.info.id)
    assert tab._length.bounds()[1] == music.MAX_SECONDS


def test_progress_takes_and_done(make):
    tab = make()
    _song(tab)
    tab._takes.setCurrentIndex(1)
    tab._generate()
    w = FakeWorker.made[-1]
    assert tab._go.isHidden() and not tab._cancel.isHidden()
    assert tab._lyrics.isReadOnly() and not tab._takes.isEnabled()
    w.event.emit(("phase", "compose", 0))
    w.event.emit(("step", 3, 8))
    assert tab._status.text().startswith("Take 1 of 2 · Composing · step 3 / 8")
    w.take.emit(0, "/tmp/take-1.wav", {"seed": 7, "params": {"duration": 95}})
    assert not tab._takes_card.isHidden()
    assert tab._rows[0].label_text() == "Take 1 · seed 7 · 1:35"
    w.event.emit(("phase", "compose", 1))
    w.event.emit(("paused", "dictation"))
    assert tab._status.text().startswith("Take 2 of 2 · Paused while you dictate…")
    w.take.emit(1, "/tmp/take-2.wav", {"seed": 8})
    w.done.emit([(0, "", {}), (1, "", {})])
    w.finished.emit()
    assert tab._status.text().startswith("2 takes ready")
    assert not tab._go.isHidden() and tab._go.isEnabled() and not tab.busy()
    assert len(tab._rows) == 2 and not tab._lyrics.isReadOnly()


def test_error_is_shown_and_finished_takes_stay(make):
    tab = make()
    _song(tab)
    toasts = []
    tab.toast.connect(lambda msg, kind: toasts.append((msg, kind)))
    tab._generate()
    w = FakeWorker.made[-1]
    w.take.emit(0, "/tmp/take-1.wav", {"seed": 1})
    w.error.emit("music:memory_low:14")
    w.finished.emit()
    assert "14" in tab._status.text() and toasts[-1][1] == "error"
    assert len(tab._rows) == 1


def test_cancel(make):
    tab = make()
    _song(tab)
    tab._generate()
    w = FakeWorker.made[-1]
    tab._on_cancel()
    assert w.was_cancelled and not tab._cancel.isEnabled()
    w.cancelled.emit()
    w.finished.emit()
    assert tab._status.text() == t("studio.cancelled") and not tab.busy()


def test_blocked_while_another_tab_runs(make):
    tab = make()
    _song(tab)
    states = []
    tab.busy_changed.connect(states.append)
    tab.set_blocked(True)
    assert not tab._go.isEnabled()
    tab._generate()
    assert not FakeWorker.made
    tab.set_blocked(False)
    tab._generate()
    assert FakeWorker.made and states == [True]


def test_choices_are_remembered(make):
    prefs = Prefs(compose_model=music.TURBO.info.id, compose_mode="bgm", compose_seconds=200, compose_takes=4,
                  compose_language="ja", compose_planner=False)
    tab = make(settings=prefs)
    assert tab._model_pick.current() == music.TURBO.info.id and tab._mode.current() == "bgm"
    assert tab._length.value() == 200 and tab._takes.currentData() == 4
    assert tab._lang.currentData() == "ja" and not tab._planner.isChecked()
    tab._planner.setChecked(True)
    tab._takes.setCurrentIndex(0)
    assert prefs["compose_planner"] is True and prefs["compose_takes"] == 1


def test_unknown_section_tags_are_pointed_out(make):
    tab = make()
    tab._lyrics.setPlainText("[verse]\nline\n[drop]\nline")
    assert "[drop]" in tab._lyrics_count.text()


def test_chinese_labels(make):
    tab = make()
    i18n.set_language("zh")
    try:
        tab.retranslate()
        assert tab._go.text() == "开始作曲"
        assert tab._footer.text() == t("studio.compose.footer")
    finally:
        i18n.set_language("en")


def _finished_take(tab, tmp_path, seed=7):
    import numpy as np

    from thundertalk.core import audio_io
    wav = tmp_path / f"take-{seed}.wav"
    audio_io.write_wav(str(wav), np.zeros((4800, 2), np.float32), 48000)
    wav.with_suffix(".json").write_text("{}")
    wav.with_suffix(".latents.npy").write_bytes(b"x")
    _song(tab)
    tab._generate()
    w = FakeWorker.made[-1]
    w.take.emit(0, str(wav), {"seed": seed})
    w.finished.emit()
    return tab._rows[-1], wav


def test_play_a_take_through_the_shared_player(make, tmp_path, monkeypatch):
    tab = make()
    toggles = []
    monkeypatch.setattr(tab._player, "toggle", lambda: toggles.append(1))
    row, _ = _finished_take(tab, tmp_path)
    assert tab._player.isHidden()
    row.action.emit("play")
    assert not tab._player.isHidden() and toggles == [1]
    assert tab._player.player.duration == 0.1                 # stereo take downmixed for the mono player
    row.action.emit("play")                                   # same take: toggles, no reload
    assert toggles == [1, 1]


def test_export_copies_the_take(make, tmp_path, monkeypatch):
    tab = make()
    row, wav = _finished_take(tab, tmp_path)
    dest = tmp_path / "out" / "my song"
    dest.parent.mkdir()
    monkeypatch.setattr(tab, "export_path", lambda r: str(dest))
    toasts = []
    tab.toast.connect(lambda msg, kind: toasts.append(kind))
    row.action.emit("export")
    out = dest.with_name("my song.wav")
    assert out.read_bytes() == wav.read_bytes() and toasts == ["success"]


def test_delete_removes_the_take_and_its_sidecars(make, tmp_path, monkeypatch):
    tab = make()
    row, wav = _finished_take(tab, tmp_path)
    monkeypatch.setattr(tab, "confirm_delete", lambda r: False)
    row.action.emit("delete")
    assert wav.exists() and tab._rows == [row]
    monkeypatch.setattr(tab, "confirm_delete", lambda r: True)
    row.action.emit("delete")
    assert not wav.exists() and not wav.with_suffix(".json").exists()
    assert not wav.with_suffix(".latents.npy").exists()
    assert tab._rows == [] and tab._takes_card.isHidden()


class FakeCheck(QObject):
    done = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    finished = Signal()
    made = []

    def __init__(self, path, engine, meta):
        super().__init__()
        self.path, self.engine, self.meta = path, engine, meta
        self.started = self.was_cancelled = False
        self.made.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.was_cancelled = True

    def wait(self, _ms):
        return True


def test_sung_lyrics_request_and_reset(make, isolated_home):
    from thundertalk.core import music_lyrics
    tab = make()
    tab._lang.setCurrentIndex(tab._lang.findData("zh"))
    _song(tab, "[verse]\n我们唱欧拉SONG")
    tab._sung.toggle.setChecked(True)
    edit = tab._sung._fields["我们唱欧拉SONG"]
    assert edit.text() == "我们唱欧拉宋"
    edit.setText("我们唱欧拉颂")
    edit.editingFinished.emit()
    req, _ = tab.build_request()
    assert req.lyrics == "[verse]\n我们唱欧拉颂" and req.shown_lyrics.endswith("SONG")
    assert music_lyrics.load_user_dict()["song"] == "颂"
    tab._sung._reset("我们唱欧拉SONG")
    assert not tab._sung.overrides and "song" not in music_lyrics.load_user_dict()


def test_auto_checks_sequentially_only_after_music_exits(make, tmp_path, monkeypatch):
    from thundertalk.core.music_check import TakeCheck
    from thundertalk.core.music_lyrics import check_line, lyric_lines
    tab = make()
    tab._make_check = FakeCheck
    FakeCheck.made.clear()
    _song(tab, "hello there")
    tab._generate()
    w = FakeWorker.made[-1]
    meta = {"lyrics": "hello there", "shown_lyrics": "hello there", "vocal_language": "en", "seed": 1}
    for i in range(2):
        w.take.emit(i, str(tmp_path / f"take-{i}.wav"), meta)
    assert not FakeCheck.made
    w.finished.emit()
    assert tab.busy() and len(FakeCheck.made) == 1
    result = TakeCheck(lyric_lines("hello there"), [check_line(0, "hello bear", ["hello there"], "en")],
                       [(0, 5)], False, [(0, 5)])
    first = FakeCheck.made[-1]
    first.done.emit(result)
    first.finished.emit()
    assert len(FakeCheck.made) == 2 and tab._rows[0].badge_text() == "Check 1 line"
    row = tab._rows[0]
    row.check_button.click()
    assert not row.check_view.isHidden() and row.check_view.line_labels[0].text() == "hello there"
    FakeCheck.made[-1].error.emit("music:no_asr")
    FakeCheck.made[-1].finished.emit()
    assert not tab.busy() and tab._rows[1].check_button.text() == t("studio.compose.check.download")
    tab.deleteLater()


def test_repaint_uses_saved_source_and_keeps_original(make, tmp_path, monkeypatch):
    tab = make()
    row, wav = _finished_take(tab, tmp_path)
    row.meta.update({"model": music.XL.folder, "repaint_source_latents_file": wav.with_suffix(".latents.npy").name,
                     "shown_lyrics": "walking home", "vocal_language": "en"})
    monkeypatch.setattr(tab, "repaint_range", lambda r, w: (1.2, 4.5))
    row.action.emit("repaint")
    request = FakeWorker.made[-1].request
    assert request.task_type == "repaint" and request.src_audio == str(wav.resolve())
    assert request.repainting_start == 1.2 and request.repainting_end == 4.5
    assert not request.planner and request.seeds == [7] and wav.exists()
    assert request.job_dir != row.request.job_dir
    FakeWorker.made[-1].finished.emit()


def test_xl_extended_length_requires_explicit_choice(make):
    tab = make()
    tab._long.setChecked(True)
    assert tab._length.bounds()[1] == 600
    tab._length.set_value(570)
    assert "9:30" in tab._length.text()
    tab._long.setChecked(False)
    assert tab._length.value() == 300


def test_artist_warning_is_advisory_and_remove_is_available(make):
    tab = make()
    _song(tab)
    tab._style.setPlainText('warm folk pop, Taylor Swift')
    tab._check_rules()
    assert 'Taylor Swift' in tab._artist_warning.text() and not tab._remove_artist.isHidden()
    assert tab._go.isEnabled()
    req, _ = tab.build_request()
    assert 'Taylor Swift' in req.artist_warnings
    tab._remove_artist.click()
    assert 'Taylor Swift' not in tab._style.toPlainText() and tab._artist_warning.isHidden()
    assert tab._go.isEnabled()


def test_pronunciation_acceptance_keeps_matching_chinese_characters(make):
    tab = make()
    tab._lang.setCurrentIndex(tab._lang.findData("zh"))
    _song(tab, "宋朝唱SONG")
    tab._sung.accept_word("宋朝唱SONG", "SONG", "颂")
    assert tab._sung.overrides["宋朝唱SONG"] == "宋朝唱颂"
