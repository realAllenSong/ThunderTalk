"""MusicWorker with a fake MusicProcess, plus compose error codes and progress lines."""
from __future__ import annotations

import pytest

from thundertalk.core.i18n import set_language, t
from thundertalk.core.music_process import MusicError
from thundertalk.ui.studio.workers import MusicWorker, error_code, friendly_error, music_status


class FakeProcess:
    def __init__(self, events, outcome="done", error=None):
        self.events, self.outcome, self.error = events, outcome, error
        self.calls = []

    def run(self, request, on_event, cancel, *, ram_need_mb):
        self.calls.append((request, ram_need_mb, cancel))
        for event in self.events:
            on_event(event)
        if self.error:
            raise self.error
        return self.outcome


TAKE = ("take", 0, "/tmp/take-1.wav", {"seed": 7})


def _run(qapp, process):
    worker = MusicWorker("request", 12700, process=process)
    seen = {"event": [], "take": [], "done": [], "error": [], "cancelled": 0}
    worker.event.connect(seen["event"].append)
    worker.take.connect(lambda i, p, m: seen["take"].append((i, p, m)))
    worker.done.connect(seen["done"].append)
    worker.error.connect(seen["error"].append)
    worker.cancelled.connect(lambda: seen.__setitem__("cancelled", seen["cancelled"] + 1))
    worker.run()
    return worker, seen


def test_takes_and_events_are_forwarded_and_done_lists_takes(qapp):
    process = FakeProcess([("phase", "load", None), ("step", 1, 8), TAKE])
    worker, seen = _run(qapp, process)
    assert process.calls[0][:2] == ("request", 12700)
    assert process.calls[0][2] is worker.cancel_event
    assert seen["event"] == [("phase", "load", None), ("step", 1, 8)]
    assert seen["take"] == [(0, "/tmp/take-1.wav", {"seed": 7})]
    assert seen["done"] == [[(0, "/tmp/take-1.wav", {"seed": 7})]]
    assert not seen["error"] and not seen["cancelled"]


def test_cancelled_job_emits_cancelled_and_keeps_finished_takes(qapp):
    worker, seen = _run(qapp, FakeProcess([TAKE], outcome="cancelled"))
    assert seen["cancelled"] == 1 and not seen["done"] and not seen["error"]
    assert worker.takes == [(0, "/tmp/take-1.wav", {"seed": 7})]


def test_error_after_a_take_reports_the_code_and_keeps_the_take(qapp):
    worker, seen = _run(qapp, FakeProcess([TAKE], error=MusicError("music:memory_low:14")))
    assert seen["error"] == ["music:memory_low:14"]
    assert len(worker.takes) == 1


@pytest.mark.parametrize("exc, code", [
    (MusicError("model_busy"), "model_busy"),
    (MusicError("music:memory:31"), "music:memory:31"),
    (MusicError("music:stalled"), "music:stalled"),
    (MusicError("worker_stopped"), "music:worker_stopped"),
    (MusicError("music:failed", "ValueError: bad: thing\nTraceback ..."), "music:failed:ValueError: bad: thing"),
    (MusicError("music:load_failed", "missing weights"), "music:load_failed:missing weights"),
])
def test_music_error_codes(exc, code):
    assert error_code(exc) == code


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_music_errors_read_as_sentences(lang):
    set_language(lang)
    try:
        assert friendly_error("music:memory_low:14") == t("studio.music.err.memory_low").format(pct="14")
        assert "14" in friendly_error("music:memory_low:14")
        assert friendly_error("music:memory:31") == t("studio.music.err.memory").format(pct="31")
        assert friendly_error("music:stalled") == t("studio.music.err.stalled")
        assert friendly_error("music:worker_stopped") == t("studio.music.err.worker_stopped")
        assert "ValueError: bad" in friendly_error("music:failed:ValueError: bad")
        assert friendly_error("model_busy") == t("studio.err.model_busy")
    finally:
        set_language("en")


def test_music_status_lines():
    set_language("en")
    assert music_status(("phase", "load", None)) == "Loading the music model…"
    assert music_status(("phase", "compose", 0)) is None
    assert music_status(("tokens", 412, 1050)) == "Planning the song · 412 / ~1,050"
    assert music_status(("step", 17, 50)) == "Composing · step 17 / 50"
    assert music_status(("phase", "render", None)) == "Rendering audio…"
    assert music_status(("paused", "dictation")) == "Paused while you dictate…"
    assert music_status(("memory_low_warn", 22)) == "Memory is getting low (22 % free)"
    assert music_status(("unknown",)) is None
