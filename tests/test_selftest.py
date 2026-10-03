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


def test_translation_selftest_uses_apps_downloaded_weights(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from thundertalk.core import audio_io, models, translate
    eng = MagicMock()
    eng.translate_text.return_value.text = "translated text"
    eng.translate_text.return_value.inference_ms = 1
    eng.translate.return_value.text = "translated speech"
    eng.translate.return_value.inference_ms = 2
    monkeypatch.setattr(translate, "TranslationEngine", lambda: eng)
    monkeypatch.setattr(models, "get_model_path", lambda _: str(tmp_path))
    monkeypatch.setattr(audio_io, "decode_audio", lambda *args: [0.1, 0.2])
    assert selftest.run(["translate"]) == 0
    eng.load_model.assert_called_once_with(str(tmp_path))
    eng.translate_text.assert_called_once()
    eng.translate.assert_called_once()
    eng.unload.assert_called_once()
