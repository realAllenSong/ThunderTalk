import json
import stat

import numpy as np
import pytest

from thundertalk.core.audio_io import read_wav
from thundertalk.core.recordings import save_recording


def save(tmp_path, **kw):
    return save_recording(np.full(16000, .1, np.float32), model="qwen3-asr-06b-int8",
                          language="auto", final_text="今天使用露娜模型。",
                          preview_text="今天使用Luna模型。", pasted_text="今天使用Luna模型。",
                          loop_detected=True, hotwords=["Luna"], directory=tmp_path, **kw)


def test_private_audio_and_metadata(tmp_path):
    sidecar = save(tmp_path)
    data = json.loads(sidecar.read_text())
    assert data["duration"] == 1 and data["language"] == "auto"
    assert data["final_asr_text"] != data["merged_pasted_text"]
    assert data["last_preview_text"] == data["merged_pasted_text"]
    assert data["loop_detected"] and data["hotwords"] == ["Luna"]
    assert data["timestamp"].endswith("+00:00")
    audio, sr = read_wav(str(sidecar.with_suffix(".wav")))
    assert sr == 16000 and audio.shape == (16000,)
    assert np.max(np.abs(audio - .1)) < .0001
    for path in (sidecar, sidecar.with_suffix(".wav")):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotation_removes_oldest_pairs(tmp_path):
    paths = [save(tmp_path) for _ in range(23)]
    assert len(list(tmp_path.glob("*.json"))) == len(list(tmp_path.glob("*.wav"))) == 20
    assert all(not p.exists() and not p.with_suffix(".wav").exists() for p in paths[:3])
    assert all(p.exists() for p in paths[3:])


def test_failed_sidecar_removes_audio(tmp_path, monkeypatch):
    import thundertalk.core.recordings as rec
    original = rec.os.open

    def fail(path, *args):
        if str(path).endswith(".json"):
            raise OSError("disk full")
        return original(path, *args)
    monkeypatch.setattr(rec.os, "open", fail)
    with pytest.raises(OSError, match="disk full"):
        save(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("captured_keep,enabled", [(False, True), (True, False), (False, False)])
def test_app_does_not_save_when_disabled(qapp, monkeypatch, captured_keep, enabled):
    from thundertalk import app
    monkeypatch.setattr(app, "save_recording", lambda *a, **kw: pytest.fail("must not save"))
    assert app._save_recent_recording({"keep": captured_keep}, "final", "paste", enabled) is None
    assert app._save_recent_recording(None, "final", "paste", True) is None


def test_app_saves_per_take_snapshot_off_thread(qapp, monkeypatch):
    import threading
    from thundertalk import app
    calls = []
    source = dict(keep=True, samples=np.ones(16000, np.float32), model="qwen3-asr-06b-int8",
                  language="auto", preview="clean preview", preview_stats={"loops": 1},
                  hotwords=["Astra"])
    gate = threading.Event()

    def save(samples, **metadata):
        gate.wait(2)
        calls.append((threading.current_thread().name, samples, metadata))
    monkeypatch.setattr(app, "save_recording", save)
    worker = app._save_recent_recording(source, "raw ASR", "merged paste", True)
    source["preview"] = "next take"
    source["preview_stats"] = {}
    source["hotwords"].append("next take")
    gate.set()
    worker.join(2)
    name, samples, meta = calls[0]
    assert name == "dictation-save" and samples is source["samples"]
    assert meta["final_text"] == "raw ASR" and meta["pasted_text"] == "merged paste"
    assert meta["preview_text"] == "clean preview" and meta["loop_detected"]
    assert meta["hotwords"] == ["Astra"]


def test_app_storage_failure_is_nonfatal(qapp, monkeypatch, capsys):
    from thundertalk import app
    source = dict(keep=True, samples=np.ones(16000), model="test", language="auto",
                  preview="", preview_stats={}, hotwords=[])

    def fail(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(app, "save_recording", fail)
    worker = app._save_recent_recording(source, "", "", True)  # failed ASR still keeps audio
    worker.join(2)
    assert "could not save dictation: disk full" in capsys.readouterr().out
