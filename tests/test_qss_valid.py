"""Every stylesheet the app sets must parse.

app.py used to swallow Qt's "Could not parse stylesheet" warnings, which is
how twelve broken stylesheets (a stray ``}}`` after a non-f-string
continuation line) shipped unnoticed. This builds the real UI headlessly and
fails on any such warning.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QPushButton

from thundertalk.ui import theme


@pytest.fixture
def qt_messages():
    msgs: list[str] = []
    prev = qInstallMessageHandler(lambda mode, ctx, message: msgs.append(message))
    yield msgs
    qInstallMessageHandler(prev)


def _parse_failures(msgs: list[str]) -> list[str]:
    return [m for m in msgs if "Could not parse stylesheet" in m]


@pytest.mark.parametrize("kind", ["primary", "secondary", "ghost", "danger"])
@pytest.mark.parametrize("height", [28, 34, 42])
def test_button_qss_variants_parse(qapp, qt_messages, kind, height) -> None:
    btn = QPushButton("x")
    btn.setStyleSheet(theme.button_qss(kind, height, 12))
    btn.ensurePolished()
    qapp.processEvents()
    assert not _parse_failures(qt_messages)


def test_shared_qss_constants_parse(qapp, qt_messages) -> None:
    for qss in (theme.APP_QSS, theme.COMBO_QSS, theme.INPUT_QSS, theme.segment_tab_qss()):
        w = QPushButton("x")
        w.setStyleSheet(qss)
        w.ensurePolished()
    qapp.processEvents()
    assert not _parse_failures(qt_messages)


def test_whole_main_window_and_overlays_parse(qapp, qt_messages, isolated_home, no_audio_hw, monkeypatch) -> None:
    from thundertalk.core.history import HistoryStore
    from thundertalk.core.settings import Settings
    from thundertalk.core.state import AppState
    from thundertalk.ui.main_window import MainWindow
    from thundertalk.ui.overlay import VoiceOverlay
    from thundertalk.ui.review_overlay import ReviewOverlay
    from thundertalk.ui.styled_dialog import StyledDialog

    # objc calls against the offscreen platform's fake NSView segfault.
    monkeypatch.setattr(MainWindow, "_setup_macos_titlebar", lambda self: None)

    settings = Settings()
    win = MainWindow(settings, HistoryStore(), AppState(settings.hotkey))
    win.show()
    for i in range(6):                       # visit every page so all of them polish
        win._select_nav(i, animate=False)
        qapp.processEvents()
    win.show_onboarding()
    for step in range(4):
        win._onboarding._go(step, animate=False)
        qapp.processEvents()

    ReviewOverlay().show_review_loading("hola", "eng")
    VoiceOverlay()
    StyledDialog(None, title="t", body="b", accept_label="a", cancel_label="c")
    qapp.processEvents()

    win.models_page.wait_background()
    assert not _parse_failures(qt_messages), _parse_failures(qt_messages)[:3]
