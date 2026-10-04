"""Permission recovery uses mocked OS calls; never resets real TCC grants."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from thundertalk.core import platform_utils as pu
from thundertalk.core.i18n import t
from thundertalk.core.settings import Settings
from thundertalk.core.state import AppState
from thundertalk.ui.onboarding import OnboardingOverlay


@pytest.fixture
def permissions(qapp, isolated_home, monkeypatch):
    status = SimpleNamespace(mic="denied", acc=False)
    monkeypatch.setattr(pu, "check_microphone", lambda: status.mic)
    monkeypatch.setattr(pu, "check_accessibility", lambda: status.acc)
    parent = QWidget()
    parent.resize(900, 900)
    state = AppState()
    overlay = OnboardingOverlay(parent, Settings(), state)
    parent.show()
    overlay.show()
    overlay._go(1, animate=False)
    yield overlay, state, status
    overlay._perm_timer.stop()
    parent.close()


@pytest.mark.parametrize("mic, reset", [("denied", True), ("not_determined", True),
                                        ("restricted", False), ("unknown", False), ("authorized", False)])
def test_reset_is_visible_only_for_recoverable_microphone_states(permissions, mic, reset):
    overlay, state, status = permissions
    status.mic = mic
    state.refresh_permissions()
    assert overlay._mic_row._reset_btn.isVisible() is reset
    assert overlay._mic_row._is_granted is (mic == "authorized")


def test_poll_turns_permissions_green_without_restart(permissions):
    overlay, state, status = permissions
    assert overlay._perm_timer.isActive()
    assert overlay._perm_timer.interval() == 900
    assert not state.permissions_ok
    status.mic, status.acc = "authorized", True
    overlay._perm_timer.timeout.emit()
    assert state.permissions_ok and overlay._mic_row._is_granted and overlay._acc_row._is_granted
    assert overlay._next.text() == t("onb.next")
    assert not overlay._mic_row._reset_btn.isVisible()
    overlay._go(0, animate=False)
    assert not overlay._perm_timer.isActive()


def test_hiding_permission_step_stops_polling(permissions):
    overlay, _, _ = permissions
    overlay.hide()
    assert not overlay._perm_timer.isActive()
    overlay.show()
    assert overlay._perm_timer.isActive()


@pytest.mark.parametrize("kind", ["mic", "acc"])
def test_explicit_reset_actions_and_failure_message(permissions, monkeypatch, kind):
    overlay, _, _ = permissions
    calls = []
    method = "reset_microphone" if kind == "mic" else "reset_accessibility"
    monkeypatch.setattr(pu, method, lambda: calls.append(method) or False)
    row = overlay._mic_row if kind == "mic" else overlay._acc_row
    row._reset_btn.click()
    assert calls == [method]
    assert overlay._perm_hint.text() == t("onb.perm.reset_failed")
    assert not row._is_granted


def test_home_banner_recovers_and_disappears_on_next_poll(qapp, isolated_home, monkeypatch):
    from thundertalk.core.history import HistoryStore
    from thundertalk.ui.pages.home_page import HomePage
    status = SimpleNamespace(mic="denied", acc=True)
    calls = []
    monkeypatch.setattr(pu, "check_microphone", lambda: status.mic)
    monkeypatch.setattr(pu, "check_accessibility", lambda: status.acc)
    monkeypatch.setattr(pu, "reset_microphone", lambda: calls.append("reset") or True)
    page = HomePage(HistoryStore(), AppState())
    page.show()
    assert page._perm.isVisible()
    assert page._perm._btn.text() == t("onb.perm.reset")
    page._perm._btn.click()
    assert calls == ["reset"]
    status.mic = "authorized"
    page._perm_timer.timeout.emit()
    assert not page._perm.isVisible()
    assert page._perm_timer.interval() == 1000
    page.close()
    assert not page._perm_timer.isActive()
