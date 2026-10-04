"""Notes contracts with fake providers and real Qt workers; no model loads."""
from __future__ import annotations

import sys
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from thundertalk.core import llm_providers as lp, meeting_notes as notes, transcribe as tr
from thundertalk.core.i18n import set_language
from thundertalk.core.settings import Settings
from thundertalk.ui.pages.proofread_page import ProofreadPage
from thundertalk.ui.studio.transcribe_tab import TranscribeTab
from thundertalk.ui.studio.workers import BatchItem, BatchWorker, NotesWorker

EN = "## Summary\nA draft was discussed.\n\n## Key points\n- Pilot first.\n\n## Decisions\n- Pilot first.\n\n## Action items\n- Lead: draft; when: not mentioned.\n\n## Open questions\n- Budget: not mentioned."
ZH = "## 摘要\n讨论试点。\n\n## 要点\n- 先试点。\n\n## 决定\n- 先试点。\n\n## 行动项\n- 负责人：未提及；任务：写草稿；时间：未提及。\n\n## 待解决问题\n- 预算未提及。"


def transcript(text="Let's pilot first.", n=1):
    return tr.Transcript([tr.Segment(i, i + 1, text, "S01" if i % 2 == 0 else "S02") for i in range(n)],
                         n, "Fake", has_speakers=True, speaker_names={"S01": "Lead", "S02": "Reviewer"})


class Fake(lp.Provider):
    def __init__(self, reply=EN, action=None, **kwargs):
        super().__init__("fake", "Fake", ["default"], ready=True, **kwargs)
        self.calls = []
        self.reply, self.action = reply, action

    def complete(self, system, user, model, timeout, cancel=None):
        self.calls.append((system, user, model, timeout, cancel))
        if self.action:
            self.action(cancel)
        return self.reply


def wait_for(cond, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_chunk_boundaries_and_oversized_segment():
    t = transcript("word", 3)
    assert notes.transcript_chunks(t, 30) == ["Lead: word\nReviewer: word", "Lead: word"]
    t.segments[0].text = "x" * 90
    chunks = notes.transcript_chunks(t, 30)
    assert all(len(c) <= 30 for c in chunks)
    assert sum(c.count("x") for c in chunks) == 90
    assert all(c.startswith("Lead: ") for c in chunks[:4])


def test_bounded_calls_no_silent_truncation():
    p = Fake()
    t = transcript("x" * (notes.CHUNK_CHARS * notes.MAX_CHUNKS))
    with pytest.raises(notes.NotesError, match="too_long"):
        notes.generate(t, p, "chosen")
    assert not p.calls
    t = transcript("x" * 5994, notes.MAX_CHUNKS)
    # All labels and text are retained; this uses the exact maximum call budget.
    t.segments = [tr.Segment(i, i + 1, "x" * 5994, "S01") for i in range(notes.MAX_CHUNKS)]
    notes.generate(t, p, "chosen")
    assert len(p.calls) == notes.MAX_CHUNKS + 1


def test_chunks_merge_progress_and_chosen_model():
    p, progress = Fake(), []
    t = transcript("word " * 700, 3)
    result = notes.generate(t, p, "custom-model", timeout=7, progress=lambda *a: progress.append(a))
    assert result == EN and len(p.calls) == 4
    assert "Lead:" in p.calls[0][1] and "Reviewer:" in p.calls[1][1]
    assert "Merge the supplied partial notes" in p.calls[-1][0]
    assert "Part 1\n" + EN in p.calls[-1][1]
    assert all(c[2:4] == ("custom-model", 7) for c in p.calls)
    assert [p[0] for p in progress] == [0, 25, 50, 75, 100]
    assert progress[-2][1] == "notes:merge"
    assert "Never invent" in p.calls[0][0] and "not mentioned" in p.calls[0][0]


@pytest.mark.parametrize("text,reply,language", [("We agreed to pilot first.", EN, "en"),
                                                 ("我们决定先试点，下周再评估。", ZH, "zh")])
def test_language_from_transcript_not_ui(text, reply, language, isolated_home):
    set_language("zh" if language == "en" else "en")
    t, p = transcript(text), Fake(reply)
    assert notes.transcript_language(t) == language
    assert notes.generate(t, p, "m") == reply
    assert ("Chinese" if language == "zh" else "English") in p.calls[0][0]
    assert ("未提及" if language == "zh" else "not mentioned") in p.calls[0][0]


@pytest.mark.parametrize("when", ["before", "during", "merge"])
def test_cancel_never_returns_partial_notes(when):
    event = threading.Event()
    def action(cancel):
        if when == "during" or (when == "merge" and len(p.calls) == 3):
            cancel.set()                  # even a provider ignoring cancellation is guarded
    p = Fake(action=action)
    if when == "before":
        event.set()
    with pytest.raises(lp.CompletionCancelled):
        notes.generate(transcript("word " * 1000, 2), p, "m", cancel=event)
    assert len(p.calls) == {"before": 0, "during": 1, "merge": 3}[when]


def test_timeout_is_bounded_and_translated(tmp_path):
    class Slow(Fake):
        def complete(self, system, user, model, timeout, cancel=None):
            lp._run([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path,
                    timeout=timeout, cancel=cancel)
    start = time.monotonic()
    with pytest.raises(notes.NotesError, match="failed"):
        notes.generate(transcript(), Slow(), "m", timeout=0.1)
    assert time.monotonic() - start < 2


@pytest.mark.parametrize("provider", [None, lp.Provider("fake", "Fake", ["m"], ready=False)])
def test_no_provider(provider):
    with pytest.raises(notes.NotesError, match="no_provider"):
        notes.generate(transcript(), provider, "m")


@pytest.mark.parametrize("reply", ["", "x" * 4001, "English summary", "## 摘要\n只包含摘要"])
def test_invalid_result(reply):
    with pytest.raises(notes.NotesError, match="invalid"):
        notes.generate(transcript("我们决定先试点。"), Fake(reply), "m")


def test_export_notes_only_in_markdown():
    t = transcript()
    original = {f: t.export(f) for f in ("md", "txt", "srt", "vtt", "json")}
    t.notes = EN
    assert t.export("md").startswith(original["md"].rstrip())
    assert EN in t.export("md")
    assert all(t.export(f) == original[f] for f in ("txt", "srt", "vtt", "json"))


@pytest.fixture
def tab(qapp, isolated_home):
    settings = ProofreadPage(Settings(), detect_clis=lambda verified: [], detect_servers=lambda **k: [])
    widget = TranscribeTab()
    widget.set_provider_source(settings)
    widget.resize(950, 900)
    widget.show()
    toasts, navigation = [], []
    widget.toast.connect(lambda *a: toasts.append(a))
    widget.navigate.connect(navigation.append)
    yield widget, settings, toasts, navigation
    widget.shutdown()
    widget.close()
    settings.close()


def test_no_provider_ui_and_settings_link(tab):
    w, s, toasts, navigation = tab
    w._show_result(transcript())
    w._summary_btn.click()
    assert not w.busy() and "AI Proofread" in toasts[-1][0]
    assert "sign in" in w._notes_hint.text() and w._notes_settings.isVisible()
    w._notes_settings.click()
    assert navigation == ["proofread"]
    s.settings.set("cleanup_provider", "missing")
    s._clis_detected([Fake()])
    assert w._notes_provider() == (None, "")


def test_notes_ui_copy_save_export_and_rename(tab, monkeypatch, tmp_path):
    w, s, toasts, _ = tab
    p = Fake(executable="fake-cli")
    p.models.append("chosen-model")
    s._clis_detected([p])
    s.model_combo.setCurrentIndex(s.model_combo.findData("chosen-model"))
    assert not s.settings.get("llm_rewrite_enabled")
    t = transcript()
    w._show_result(t)
    assert "sent to this provider" in w._notes_hint.text()
    w._summary_btn.click()
    assert w.busy() and not w._summary_btn.isEnabled() and w._cancel.isVisible()
    assert wait_for(lambda: not w.busy())
    assert t.notes == EN and w._notes_card.isVisible()
    assert "Action items" in w._notes_view.toPlainText()
    assert p.calls[0][2] == "chosen-model"
    w._notes_copy.click()
    assert QApplication.clipboard().text() == EN
    path = tmp_path / "notes"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(path), ""))
    w._save_notes()
    assert path.with_suffix(".md").read_text() == EN + "\n"
    path = tmp_path / "transcript.md"
    w._export("md", ".md")
    assert EN in path.read_text()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("New name", True))
    w._rename_speaker("S01")
    assert not t.notes and not w._notes_card.isVisible()
    w._show_result(transcript())
    assert not w._notes_card.isVisible()


def test_local_privacy_and_ui_retranslate(tab):
    w, s, _, _ = tab
    s._clis_detected([Fake(local=True)])
    w._show_result(transcript())
    assert "stays on this Mac" in w._notes_hint.text()
    set_language("zh")
    w.retranslate()
    assert "复制纪要" == w._notes_copy.text() and "本机" in w._notes_hint.text()


def test_ui_cancel_preserves_existing_notes(tab):
    w, s, _, _ = tab
    entered, release = threading.Event(), threading.Event()
    def block(cancel):
        entered.set()
        release.wait(2)
    s._clis_detected([Fake(action=block)])
    t = transcript()
    t.notes = "Old notes"
    w._show_result(t)
    w._summarize()
    assert entered.wait(1)
    w._on_cancel()
    release.set()
    assert wait_for(lambda: not w.busy())
    assert t.notes == "Old notes" and "Cancelled" in w._status.text()


def test_worker_reports_errors_without_mutating_transcript(qapp):
    t = transcript()
    w = NotesWorker(t, None, "")
    errors = []
    w.error.connect(errors.append)
    w.run()
    assert errors == ["notes:no_provider"] and not t.notes


@pytest.mark.parametrize("fail", [False, True])
def test_batch_notes_sequential_and_failure_keeps_transcript(qapp, tmp_path, isolated_home, monkeypatch, fail):
    calls = []
    def transcribe(path, *args, **kwargs):
        calls.append("asr:" + path)
        return transcript()
    def complete(cancel):
        calls.append("notes")
        if fail:
            raise lp.ProviderError("Provider timed out")
    monkeypatch.setattr(tr, "transcribe_file", transcribe)
    w = BatchWorker([BatchItem("a.wav"), BatchItem("b.wav")], None, False, ["md"], str(tmp_path),
                    notes_provider=Fake(action=complete), notes_model="m")
    done, failures = [], []
    w.item_done.connect(lambda *a: done.append(a))
    w.item_notes_failed.connect(lambda *a: failures.append(a))
    assert w.work() == 2
    assert calls == ["asr:a.wav", "notes", "asr:b.wav", "notes"]
    assert len(done) == 2 and len(failures) == (2 if fail else 0)
    assert all(bool(item[1].notes) == (not fail) for item in done)
    assert all((EN in (tmp_path / f"{name}.md").read_text()) == (not fail) for name in ("a", "b"))


def test_queue_checkbox_requires_provider_then_saves_markdown(tab, tmp_path, monkeypatch):
    w, s, toasts, _ = tab
    monkeypatch.setattr(tr, "transcribe_file", lambda *a, **k: transcript())
    w._queue_notes.setChecked(True)
    w.add_files([str(tmp_path / "a.wav"), str(tmp_path / "b.wav")])
    w._start_batch()
    assert not w.busy() and "AI Proofread" in toasts[-1][0]
    s._clis_detected([Fake()])
    w._start_batch()
    assert wait_for(lambda: not w.busy())
    assert not w._rows and w._history_list.count() == 2
    assert w._transcript.notes == EN
    assert all((tmp_path / f"{name}.md").is_file() for name in ("a", "b"))
    w._open_history(w._history_list.item(1))
    assert w._notes_card.isVisible() and w._notes_view.toPlainText().startswith("Summary")


def test_cancel_batch_notes_stops_future_items(qapp, tmp_path, isolated_home, monkeypatch):
    monkeypatch.setattr(tr, "transcribe_file", lambda *a, **k: transcript())
    p = Fake(action=lambda cancel: w.cancel())
    w = BatchWorker([BatchItem("a.wav"), BatchItem("b.wav")], None, False, ["md"], str(tmp_path),
                    notes_provider=p, notes_model="m")
    cancelled = []
    w.item_cancelled.connect(cancelled.append)
    assert w.work() == 0
    assert cancelled == [0, 1] and len(p.calls) == 1
    assert not list(tmp_path.glob("*.md")) and not (isolated_home / ".thundertalk" / "transcripts").exists()


def test_add_to_running_notes_queue(tab, tmp_path, monkeypatch):
    w, s, _, _ = tab
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(tr, "transcribe_file", lambda *a, **k: transcript())
    def block(cancel):
        entered.set()
        release.wait(2)
    s._clis_detected([Fake(action=block)])
    w._queue_notes.setChecked(True)
    w.add_files([str(tmp_path / "a.wav"), str(tmp_path / "b.wav")])
    w._start_batch()
    assert entered.wait(1)
    w.add_files([str(tmp_path / "c.wav")])
    release.set()
    assert wait_for(lambda: not w.busy())
    assert not w._rows and w._history_list.count() == 3
    assert w._transcript.notes == EN


def test_notes_link_opens_proofread_page(qapp, isolated_home, no_audio_hw, monkeypatch):
    from thundertalk.core.history import HistoryStore
    from thundertalk.core.state import AppState
    from thundertalk.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_setup_macos_titlebar", lambda self: None)
    monkeypatch.setattr(ProofreadPage, "refresh", lambda self: None)
    settings = Settings()
    w = MainWindow(settings, HistoryStore(), AppState(settings.hotkey))
    w.show()
    QApplication.processEvents()
    assert w.studio_page.transcribe_tab._provider_source is w.proofread_page
    for target in ("proofread", "settings.cleanup"):
        w.navigate("home")
        w.navigate(target)
        assert w._stack.currentWidget() is w.proofread_page
    w.models_page.wait_background()
    w.studio_page.shutdown()
    w.proofread_page.shutdown()
    w.close()
