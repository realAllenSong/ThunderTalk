"""Cross-process coordination without models or GUI startup."""
from __future__ import annotations

import os
import subprocess
import sys


def test_extra_model_slot_is_shared_and_released(tmp_path):
    from thundertalk.core.priority import ModelSlot
    path = tmp_path / "slot.lock"
    slot = ModelSlot(path)
    assert slot.acquire(blocking=False)
    script = ("import sys\nfrom pathlib import Path\nfrom thundertalk.core.priority import ModelSlot\n"
              "slot = ModelSlot(Path(sys.argv[1]))\nprint(slot.acquire(blocking=False))\n")
    def available():
        return subprocess.check_output([sys.executable, "-c", script, str(path)], text=True).strip()
    try:
        assert available() == "False"
    finally:
        slot.release()
    assert available() == "True"


def test_dictation_priority_visible_to_other_process_and_expires(tmp_path):
    from thundertalk.core.priority import DictationPriority
    path = tmp_path / "dictation.lock"
    priority = DictationPriority(shared_path=path)
    script = ("import sys\nfrom pathlib import Path\nfrom thundertalk.core.priority import DictationPriority\n"
              "print(DictationPriority(shared_path=Path(sys.argv[1])).active)\n")
    def active():
        return subprocess.check_output([sys.executable, "-c", script, str(path)], text=True).strip()
    priority.begin()
    priority.begin()
    try:
        assert active() == "True"
        priority.end()
        assert active() == "True"
        # A stale/hung owner must not pause jobs forever.
        os.utime(path, (0, 0))
        assert active() == "False"
    finally:
        priority.end()
    assert active() == "False"


def test_concurrent_settings_writers_keep_both_updates(tmp_path):
    # Both writers load before either is allowed to save.
    script = ("import sys,time\nfrom pathlib import Path\nfrom thundertalk.core import settings\n"
              "settings._PATH=Path(sys.argv[1])\ns=settings.Settings()\n"
              "Path(sys.argv[2]).touch()\n"
              "while not Path(sys.argv[3]).exists(): time.sleep(.01)\n"
              "s.set(sys.argv[4], sys.argv[5])\n")
    path, go = tmp_path / "settings.json", tmp_path / "go"
    procs = []
    try:
        for i, key in enumerate(("active_model_id", "studio_engine")):
            value = "qwen3-asr-06b-int8" if i == 0 else "kokoro"
            procs.append(subprocess.Popen([sys.executable, "-c", script, str(path), str(tmp_path / f"ready{i}"), str(go), key, value]))
        import time
        deadline = time.monotonic() + 10
        while not all((tmp_path / f"ready{i}").exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        go.touch()
        assert all(p.wait(10) == 0 for p in procs)
        import json
        data = json.loads(path.read_text())
        assert data["active_model_id"] == "qwen3-asr-06b-int8" and data["studio_engine"] == "kokoro"
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_nested_core_jobs_share_one_lease_but_other_jobs_are_busy(tmp_path):
    import pytest
    from thundertalk.core import priority
    slot = priority.ModelSlot(tmp_path / "slot")
    from unittest.mock import patch
    with patch.object(priority, "EXTRA_MODEL", slot):
        with priority.alternate_job():
            with priority.alternate_job():
                assert not slot.acquire(blocking=False)
        assert slot.acquire(blocking=False)
        try:
            with pytest.raises(RuntimeError, match="model_busy"):
                with priority.alternate_job():
                    pass
        finally:
            slot.release()


def test_gui_speech_and_translation_obey_shared_slot(monkeypatch, tmp_path):
    import pytest
    from types import SimpleNamespace
    from thundertalk.core import priority, speech, translate_process
    slot = priority.ModelSlot(tmp_path / "slot")
    monkeypatch.setattr(priority, "EXTRA_MODEL", slot)
    assert slot.acquire(blocking=False)
    try:
        backend = SimpleNamespace(info=SimpleNamespace(needs_gpu=True))
        with pytest.raises(RuntimeError, match="model_busy"):
            with speech.SpeechEngine()._holding(backend, None):
                pass
        engine = translate_process.TranslationProcess()
        monkeypatch.setattr(engine, "_call", lambda *a: pytest.fail("must not reach the worker"))
        with pytest.raises(RuntimeError, match="model_busy"):
            engine.translate_text("hello", "eng", "cmn")
    finally:
        slot.release()


def test_background_models_reject_low_memory_before_loading(monkeypatch):
    import pytest
    from thundertalk.core import memory_policy, priority, music_process
    monkeypatch.setattr(memory_policy, "free_percent", lambda: 20)
    monkeypatch.setattr(music_process, "total_memory_mb", lambda: 36 * 1024)
    with pytest.raises(RuntimeError, match="memory"):
        priority.background_headroom(6000)
    monkeypatch.setattr(memory_policy, "free_percent", lambda: 80)
    priority.background_headroom(6000)


def test_foreground_dictation_translation_does_not_wait_on_own_lease(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from thundertalk.app import TranslationWorker
    from thundertalk.core import priority, translate_process
    slot = priority.ModelSlot(tmp_path / "slot")
    monkeypatch.setattr(priority, "EXTRA_MODEL", slot)
    monkeypatch.setattr(priority, "DICTATION", priority.DictationPriority())
    priority.DICTATION.begin()
    def no_self_wait(*a, **kw):
        raise AssertionError("Foreground dictation must not wait for itself")
    monkeypatch.setattr(priority.DICTATION, "wait_clear", no_self_wait)
    engine = translate_process.TranslationProcess()
    monkeypatch.setattr(engine, "_ensure", lambda: None)
    monkeypatch.setattr(engine, "_call", lambda *a, **kw: SimpleNamespace(
        text="hello", inference_ms=1, duration_secs=1, tgt_lang="eng"))
    worker = TranslationWorker(engine, [], "eng")
    results = []
    worker.done.connect(lambda *a: results.append(a))
    assert slot.acquire(blocking=False)
    try:
        worker.run()
        assert results and results[0][0] == "hello"
        assert priority.DICTATION.active
    finally:
        slot.release()
        priority.DICTATION.end()


def test_cancelled_neural_speech_honors_priority_wait_cancellation(monkeypatch):
    import threading
    import pytest
    from types import SimpleNamespace
    from thundertalk.core import priority, speech, tts
    cancel = threading.Event()
    cancel.set()
    def wait_clear(received=None):
        assert received is cancel
    monkeypatch.setattr(priority.DICTATION, "wait_clear", wait_clear)
    with pytest.raises(tts.TtsCancelled):
        with speech.SpeechEngine()._holding(SimpleNamespace(info=SimpleNamespace(needs_gpu=True)), cancel):
            pytest.fail("Cancelled synthesis must not load")
