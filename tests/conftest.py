"""Shared fixtures: a headless QApplication and a fully isolated $HOME."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _never_touch_real_settings(tmp_path_factory):
    """Background work (e.g. the Proofread page's CLI detection) can finish
    after a test's isolated_home is undone and would then save its settings
    over the user's real ~/.thundertalk/settings.json."""
    from thundertalk.core import i18n, settings as settings_mod
    path = tmp_path_factory.mktemp("settings-home") / "settings.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings_mod, "_PATH", path)
        mp.setattr(i18n, "_SETTINGS_PATH", path)
        yield


@pytest.fixture(autouse=True)
def _no_real_keys_language_or_network(monkeypatch, tmp_path, request):
    """A test run must not depend on, or spend, anything of the person running it.

    * Cloud keys: none (no Keychain, no environment keys). Until 2026-11-21 only test_cloud.py
      did this, so on a Mac with an OpenAI key test_selftest's "no engine downloaded" case found a
      cloud voice engine and synthesized its sentence through the paid API on every full run
      (21 requests that month), and "failed" because it then passed.
    * UI language: English, whatever the user's settings say (test_display_names failed on a
      Mac set to Chinese).
    * Network: only this machine. A test that needs a server fakes urlopen or starts one locally.
    * Free memory: plenty. The guard that refuses to load a background model when the Mac is
      short of memory read the real figure, so a full run failed whenever something heavy was
      running beside it (seen four times in November 2026 before the cause was found: a fake GPU
      voice backend "could not load" while a real model job held the memory). Tests of the guard
      itself set their own figure.
    """
    import urllib.request

    from thundertalk.core import cloud, i18n, memory_policy
    if request.module.__name__.rsplit(".", 1)[-1] != "test_memory_policy":      # that file tests the gauge itself
        monkeypatch.setattr(memory_policy, "free_percent", lambda: 90)
    monkeypatch.setenv("THUNDERTALK_KEYCHAIN_SERVICE", "ThunderTalk Cloud TEST")
    monkeypatch.setenv("THUNDERTALK_CLOUD_USAGE", str(tmp_path / "cloud_usage.json"))
    for name in ("OPENAI_ADMIN_KEY", "THUNDERTALK_OPENAI_ADMIN_KEY"):
        monkeypatch.delenv(name, raising=False)
    for pid, provider in cloud.PROVIDERS.items():
        monkeypatch.delenv(f"THUNDERTALK_{pid.upper()}_API_KEY", raising=False)
        for name in provider.env:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cloud, "_keychain_available", lambda: False)
    cloud.forget_cached_keys()
    monkeypatch.setattr(i18n, "LANG", "en")

    real_urlopen = urllib.request.urlopen

    def local_only(req, *args, **kwargs):
        url = req if isinstance(req, str) else req.full_url
        host = urllib.request.urlparse(url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(f"a test tried to reach {host}: fake the request instead")
        return real_urlopen(req, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", local_only)
    yield
    cloud.forget_cached_keys()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from thundertalk.ui import theme
    theme.force_light(app)
    yield app


@pytest.fixture(autouse=True)
def _no_real_preload(monkeypatch):
    """The Speak tab preloads real speech models when it becomes visible, and
    real ones may be on this machine; tests that exercise it opt back in with fakes."""
    monkeypatch.setattr("thundertalk.ui.studio.speak_tab.AUTO_PRELOAD", False)


@pytest.fixture(autouse=True)
def _energy_only_speech_gate(request, monkeypatch):
    """Fake engines are fed synthetic tones that Silero VAD rightly calls
    "not a voice"; tests see the old energy gate unless marked real_vad."""
    if "real_vad" not in request.keywords:
        monkeypatch.setattr("thundertalk.core.speech_gate.speech_seconds", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolated_audio_log(monkeypatch):
    """Fake audio sessions must not fill the user's real diagnostic log."""
    from thundertalk.core import audio_diagnostics as diag
    import io
    import logging
    logger = logging.Logger("audio-test")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    monkeypatch.setattr(diag, "_logger", logger)
    yield stream
    handler.close()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every persisted path (settings, history, models, i18n) at tmp_path
    so UI tests can construct real pages without touching ~/.thundertalk."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from thundertalk.core import history as hist_mod
    from thundertalk.core import i18n, settings as settings_mod
    (tmp_path / ".thundertalk").mkdir()
    monkeypatch.setattr(settings_mod, "_PATH", tmp_path / ".thundertalk" / "settings.json")
    monkeypatch.setattr(hist_mod, "_DIR", tmp_path / ".thundertalk")
    monkeypatch.setattr(hist_mod, "_JSONL_PATH", tmp_path / ".thundertalk" / "history.jsonl")
    monkeypatch.setattr(hist_mod, "_LEGACY_PATH", tmp_path / ".thundertalk" / "history.json")
    monkeypatch.setattr(i18n, "_SETTINGS_PATH", tmp_path / ".thundertalk" / "settings.json")
    monkeypatch.setattr(i18n, "LANG", "en")
    return tmp_path


@pytest.fixture
def no_audio_hw(monkeypatch):
    """Keep UI tests off real audio hardware."""
    from thundertalk.core.audio import AudioRecorder
    monkeypatch.setattr(AudioRecorder, "list_devices", staticmethod(lambda: []))
    monkeypatch.setattr(AudioRecorder, "refresh_devices", staticmethod(lambda: []))


def pytest_configure(config):
    config.addinivalue_line("markers", "real_vad: run the bundled Silero VAD speech gate")
