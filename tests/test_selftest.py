"""The packaged-app self-check entry point."""

from __future__ import annotations

import json
import shutil

import pytest

from thundertalk import selftest


@pytest.mark.skipif(shutil.which("afconvert") is None, reason="macOS afconvert only")
def test_audio_selftest_passes(capsys):
    assert selftest.run(["audio"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["ok"] is True and out[-1] == "SELFTEST PASS"


def test_missing_voice_engine_fails_cleanly(monkeypatch, capsys):
    from thundertalk.core import speech
    for bid in speech.BACKEND_ORDER:
        monkeypatch.setattr(speech.backend(bid), "is_ready", lambda: False)
    assert selftest.run(["tts"]) == 1
    out = capsys.readouterr().out
    assert "not downloaded" in out and "no speech engine downloaded" in out and "SELFTEST FAIL" in out


def test_app_main_routes_the_flag(monkeypatch):
    import sys

    from thundertalk import app
    seen = {}
    monkeypatch.setattr(selftest, "run", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["ThunderTalk", "--selftest", "audio"])
    with pytest.raises(SystemExit) as ei:
        app.main()
    assert seen["argv"] == ["audio"] and ei.value.code == 0


@pytest.mark.parametrize("speech_text, expected", [("你好，这是翻译。", 0), ("Hello, this is English.", 1)])
def test_translation_selftest_uses_apps_downloaded_weights(monkeypatch, tmp_path, speech_text, expected):
    from unittest.mock import MagicMock
    from thundertalk.core import audio_io, models, translate
    eng = MagicMock()
    eng.translate_text.return_value.text = "你好世界。"
    eng.translate_text.return_value.inference_ms = 1
    eng.translate.return_value.text = speech_text
    eng.translate.return_value.inference_ms = 2
    monkeypatch.setattr(translate, "TranslationEngine", lambda: eng)
    monkeypatch.setattr(models, "get_model_path", lambda _: str(tmp_path))
    monkeypatch.setattr(audio_io, "decode_audio", lambda *args: [0.1, 0.2])
    assert selftest.run(["translate"]) == expected
    eng.load_model.assert_called_once_with(str(tmp_path))
    eng.translate_text.assert_called_once()
    eng.translate.assert_called_once()
    eng.unload.assert_called_once()


@pytest.mark.parametrize("dit", ["mlx", "mps"])
def test_music_selftest_routes_model_and_isolated_root(monkeypatch, dit):
    seen = []
    monkeypatch.setattr(selftest, 'check_music', lambda model, root, backend: seen.append((model, root, backend)) or True)
    assert selftest.run(['music', '--model', 'xl', '--model-dir', '/isolated/music', '--dit', dit]) == 0
    assert seen == [('xl', '/isolated/music', dit)]


@pytest.mark.parametrize("explicit_root", [True, False])
def test_music_selftest_checks_audio_and_child_exit(monkeypatch, tmp_path, capsys, explicit_root):
    import numpy as np
    import soundfile as sf
    from thundertalk.core import music, music_process, runtime
    (tmp_path / 'checkpoints/acestep-v15-turbo').mkdir(parents=True)
    monkeypatch.setattr(runtime, 'require', lambda component: None)
    monkeypatch.setattr(music, 'project_root', lambda: tmp_path)
    monkeypatch.setattr(music, 'missing', lambda model, planner: [])

    class Process:
        pid = None

        def run(self, req, event, cancel, **kw):
            event(('phase', 'load', None))
            event(('phase', 'compose', 0))
            wav = tmp_path / 'take.wav'
            t = np.arange(480000) / 48000
            x = np.repeat((0.1 * np.sin(2 * np.pi * 440 * t))[:, None], 2, axis=1)
            sf.write(wav, x, 48000)
            event(('take', 0, str(wav), {}))
            return 'done'

    monkeypatch.setattr(music_process, 'MusicProcess', Process)
    args = ['music', '--model-dir', str(tmp_path)] if explicit_root else ['music']
    assert selftest.run(args) == 0
    out = capsys.readouterr().out
    assert '"child_exited": true' in out and 'SELFTEST PASS' in out
    if not explicit_root:
        assert '"catalog_downloaded": true' in out and '"planner_downloaded": true' in out
    Process.pid = 123
    assert selftest.run(args) == 1


@pytest.mark.parametrize("missing,converted", [(True, True), (False, False)])
def test_default_music_selftest_requires_receipts_and_bf16(monkeypatch, tmp_path, capsys, missing, converted):
    from thundertalk.core import music, music_checkpoint, runtime
    (tmp_path / 'checkpoints' / music.XL.folder).mkdir(parents=True)
    monkeypatch.setattr(runtime, 'require', lambda component: None)
    monkeypatch.setattr(music, 'project_root', lambda: tmp_path)
    monkeypatch.setattr(music, 'missing', lambda model, planner: [music.VAE] if missing else [])
    monkeypatch.setattr(music_checkpoint, 'converted', lambda folder: converted)
    assert selftest.run(['music', '--model', 'xl']) == 1
    out = capsys.readouterr().out
    assert ('receipts are missing' if missing else 'not marked as converted') in out
