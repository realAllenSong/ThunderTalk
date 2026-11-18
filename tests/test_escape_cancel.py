"""Issue #13: Esc cancels a dictation in progress without inserting text."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS listener")

from thundertalk.core import hotkey as hk_mod  # noqa: E402
from thundertalk.core.hotkey import ESC_VK, HotkeyListener, escape_cancels  # noqa: E402

F_CMD, F_SHIFT, F_ALT, F_CTRL = 1 << 20, 1 << 17, 1 << 19, 1 << 18


class Ev:
    def __init__(self, etype, vk, flags=0, repeat=False):
        self._t, self._vk, self._f, self._r = etype, vk, flags, repeat

    def type(self):
        return self._t

    def keyCode(self):
        return self._vk

    def modifierFlags(self):
        return self._f

    def isARepeat(self):
        return self._r


def test_which_escapes_cancel():
    assert escape_cancels(["f4"], 0)
    assert escape_cancels(["f4"], F_SHIFT)
    assert not escape_cancels(["f4"], F_CMD | F_ALT)          # ⌘⌥Esc is Force Quit
    assert not escape_cancels(["f4"], F_CTRL)
    assert escape_cancels(["alt_l"], F_ALT)                   # Hold to Record: ⌥ still held
    assert not escape_cancels(["alt_l"], F_ALT | F_CMD)
    assert not escape_cancels(["esc"], 0)                     # Esc is the hotkey itself


def _listener(calls, combo="f4"):
    hk = HotkeyListener(lambda: calls.append("toggle"), combo, on_escape=lambda: calls.append("esc"))
    hk._escape_tap = None            # observe-only path; never install a real tap in tests
    return hk


def test_esc_only_cancels_while_armed():
    calls = []
    hk = _listener(calls)
    hk._handle_event(Ev(10, ESC_VK))
    assert calls == []                                         # idle: Esc is the user's
    hk.set_escape_armed(True)
    hk._handle_event(Ev(10, ESC_VK))
    hk._handle_event(Ev(10, ESC_VK, repeat=True))              # auto-repeat: once only
    hk._handle_event(Ev(11, ESC_VK))
    assert calls == ["esc"]
    hk.set_escape_armed(False)
    hk._handle_event(Ev(10, ESC_VK))
    assert calls == ["esc"]


def test_esc_shortcuts_and_esc_hotkey_never_cancel():
    calls = []
    hk = _listener(calls)
    hk.set_escape_armed(True)
    hk._handle_event(Ev(10, ESC_VK, F_CMD | F_ALT))
    assert calls == []
    calls2 = []
    hk2 = _listener(calls2, "esc")
    hk2.set_escape_armed(True)
    assert hk2._escape_armed is False
    hk2._handle_event(Ev(10, ESC_VK))
    assert calls2 == ["toggle"]                                # still the hotkey


def test_listener_without_escape_callback_never_arms():
    hk = HotkeyListener(lambda: None, "f4")
    hk.set_escape_armed(True)
    assert hk._escape_armed is False


class _TapRig:
    def __init__(self, monkeypatch):
        import Quartz
        self.Q = Quartz
        self.enabled = []
        monkeypatch.setattr(Quartz, "CGEventTapEnable", lambda _tap, on: self.enabled.append(on))
        self.calls = []
        self.tap = hk_mod._EscapeTap(lambda: self.calls.append("esc"), lambda f: escape_cancels(["f4"], f))
        self.tap._tap = object()     # pretend it was created

    def key(self, vk, down, flags=0, repeat=False):
        Q = self.Q
        ev = Q.CGEventCreateKeyboardEvent(None, vk, down)
        Q.CGEventSetFlags(ev, flags)
        if repeat:
            Q.CGEventSetIntegerValueField(ev, Q.kCGKeyboardEventAutorepeat, 1)
        return self.tap._callback(None, Q.kCGEventKeyDown if down else Q.kCGEventKeyUp, ev, None)


def test_tap_swallows_esc_only_while_armed(monkeypatch):
    rig = _TapRig(monkeypatch)
    assert rig.key(ESC_VK, True) is not None                   # not armed: passes through
    rig.tap.arm(True)
    assert rig.key(0, True) is not None                        # other keys always pass
    assert rig.key(ESC_VK, True) is None                       # swallowed + cancel
    assert rig.key(ESC_VK, True, repeat=True) is None          # repeats swallowed, no 2nd cancel
    assert rig.key(ESC_VK, False) is None                      # its key-up too
    assert rig.calls == ["esc"]
    assert rig.key(ESC_VK, True, F_CMD | F_ALT) is not None    # Force Quit untouched
    assert rig.calls == ["esc"]


def test_tap_keeps_running_until_the_swallowed_key_is_released(monkeypatch):
    rig = _TapRig(monkeypatch)
    rig.tap.arm(True)
    rig.key(ESC_VK, True)
    rig.tap.arm(False)                     # the cancel disarms while Esc is still down
    assert rig.enabled == [True]           # …so the tap stays on for the key-up
    assert rig.key(ESC_VK, False) is None  # no orphan key-up reaches the app
    assert rig.enabled == [True, False]
    assert rig.key(ESC_VK, True) is not None
