"""Shared fixtures: a headless QApplication and a fully isolated $HOME."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
