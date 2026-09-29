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
    from thundertalk.core import tts
    monkeypatch.setattr(tts, "repo_ready", lambda repo: False)
    assert selftest.run(["tts"]) == 1
    out = capsys.readouterr().out
    assert "not downloaded" in out and "SELFTEST FAIL" in out


def test_app_main_routes_the_flag(monkeypatch):
    import sys

    from thundertalk import app
    seen = {}
    monkeypatch.setattr(selftest, "run", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["ThunderTalk", "--selftest", "audio"])
    with pytest.raises(SystemExit) as ei:
        app.main()
    assert seen["argv"] == ["audio"] and ei.value.code == 0
