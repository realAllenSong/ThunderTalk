"""Issue #11: Hold to Record — key down starts, key up ends the take."""

import pytest

from thundertalk.core.hotkey import HotkeyListener


class Ev:
    def __init__(self, etype, vk, flags=0):
        self._t, self._vk, self._f = etype, vk, flags

    def type(self):
        return self._t

    def keyCode(self):
        return self._vk

    def modifierFlags(self):
        return self._f


CTRL, SHIFT = 59, 56
F_CTRL, F_SHIFT = 1 << 18, 1 << 17


def test_combo_press_and_release_callbacks():
    calls = []
    hk = HotkeyListener(lambda: calls.append("down"), "ctrl_l+shift_l", on_release=lambda: calls.append("up"))
    hk._handle_event(Ev(12, CTRL, F_CTRL))
    hk._handle_event(Ev(12, SHIFT, F_CTRL | F_SHIFT))
    assert calls == ["down"]
    hk._handle_event(Ev(12, SHIFT, F_CTRL))            # let go of one key
    assert calls == ["down", "up"]
    hk._handle_event(Ev(12, CTRL, 0))                  # no second release
    assert calls == ["down", "up"]


def test_single_key_release_and_no_release_without_fire():
    calls = []
    hk = HotkeyListener(lambda: calls.append("down"), "f4", on_release=lambda: calls.append("up"))
    hk._handle_event(Ev(11, 118))                      # stray key-up: nothing
    hk._handle_event(Ev(10, 118))
    hk._handle_event(Ev(11, 118))
    assert calls == ["down", "up"]


def test_release_callback_is_optional():
    hk = HotkeyListener(lambda: None, "f4")
    hk._handle_event(Ev(10, 118))
    hk._handle_event(Ev(11, 118))                      # must not raise


def test_settings_offers_hold_and_remembers_it(qapp, isolated_home):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.settings_page import SettingsPage
    prefs = Settings()
    page = SettingsPage(prefs)
    combo = page._mode_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["toggle", "hold"]
    combo.setCurrentIndex(1)
    assert prefs.get("press_mode") == "hold"
    page2 = SettingsPage(prefs)                        # reopening keeps the choice
    assert page2._mode_combo.currentIndex() == 1
    page.close()
    page2.close()


class Fake:
    def __init__(self, mode="hold", delay=300):
        self.mode, self.delay, self.recording, self.calls, self.logs = mode, delay, False, [], []

    def toggle(self):
        self.recording = not self.recording
        self.calls.append("start" if self.recording else "stop")

    def gate(self):
        from thundertalk.core.hold import HoldGate
        return HoldGate(hold_mode=lambda: self.mode == "hold", delay_ms=lambda: self.delay,
                        busy=lambda: self.recording, toggle=self.toggle,
                        log=lambda ev, **k: self.logs.append(ev))


def wait(qapp, ms):
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(.005)


def test_quick_tap_never_starts_a_take(qapp):
    f = Fake(delay=300)
    g = f.gate()
    for _ in range(3):                                 # tap, tap, tap
        g.press()
        wait(qapp, 80)
        g.release()
    wait(qapp, 400)
    assert f.calls == [] and f.logs.count("hold_tap_ignored") == 3


def test_shortcut_with_the_hotkey_is_ignored(qapp):
    f = Fake(delay=300)
    g = f.gate()
    g.press()                                          # ⌃ down
    wait(qapp, 50)
    g.chord()                                          # C down: ⌃C
    wait(qapp, 400)                                    # still holding ⌃ past the delay
    g.release()
    assert f.calls == [] and "hold_chord_ignored" in f.logs


def test_hold_past_the_delay_records_until_release(qapp):
    f = Fake(delay=200)
    g = f.gate()
    g.press()
    wait(qapp, 300)
    assert f.calls == ["start"]
    g.chord()                                          # other keys during a take don't stop it
    wait(qapp, 100)
    g.release()
    assert f.calls == ["start", "stop"]


def test_toggle_mode_is_unchanged(qapp):
    f = Fake(mode="toggle")
    g = f.gate()
    g.press(); g.release(); g.press(); g.release()
    assert f.calls == ["start", "stop"]


def test_menu_bar_take_is_stopped_by_the_key(qapp):
    f = Fake()
    g = f.gate()
    f.toggle()                                         # started from the menu bar
    g.press()
    g.release()                                        # release must not start a new take
    wait(qapp, 400)
    assert f.calls == ["start", "stop"]


def test_settings_hold_delay_row(qapp, isolated_home):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.settings_page import SettingsPage
    prefs = Settings()
    page = SettingsPage(prefs)
    page.show()
    assert not page._hold_delay_combo.isVisible()      # only in Hold mode
    page._mode_combo.setCurrentIndex(1)
    assert page._hold_delay_combo.isVisible()
    assert page._hold_delay_combo.currentData() == 300
    page._hold_delay_combo.setCurrentIndex(page._hold_delay_combo.findData(500))
    assert prefs.get("hold_delay_ms") == 500
    page.close()


def test_listener_reports_chords_only_while_fired():
    calls = []
    hk = HotkeyListener(lambda: calls.append("down"), "ctrl_l", on_release=lambda: calls.append("up"),
                        on_chord=lambda: calls.append("chord"))
    hk._handle_event(Ev(10, 8))                        # C alone: nothing
    hk._handle_event(Ev(11, 8))
    hk._handle_event(Ev(12, CTRL, F_CTRL))             # ⌃ down → fired
    hk._handle_event(Ev(10, 8))                        # C while ⌃ held → chord
    hk._handle_event(Ev(11, 8))
    hk._handle_event(Ev(12, CTRL, 0))
    assert calls == ["down", "chord", "up"]


def test_custom_hold_delay_is_typed_and_remembered(qapp, isolated_home):
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.settings_page import SettingsPage
    prefs = Settings()
    page = SettingsPage(prefs)
    page.show()
    page._mode_combo.setCurrentIndex(1)
    combo, spin = page._hold_delay_combo, page._hold_delay_spin
    assert not spin.isVisible()                        # presets: no field
    combo.setCurrentIndex(combo.findData(-1))          # Custom…
    assert spin.isVisible()
    spin.lineEdit().setText("0.45")
    spin.interpretText()
    spin.editingFinished.emit()
    assert prefs.get("hold_delay_ms") == 450
    page2 = SettingsPage(prefs)                        # reopening shows Custom with 0.45
    page2.show()
    assert page2._hold_delay_combo.currentData() == -1
    assert page2._hold_delay_spin.isVisible() and abs(page2._hold_delay_spin.value() - .45) < 1e-9
    spin.setValue(99)                                  # clamped to the 3 s maximum
    assert prefs.get("hold_delay_ms") == 3000
    combo.setCurrentIndex(combo.findData(500))         # back to a preset hides the field
    assert not spin.isVisible() and prefs.get("hold_delay_ms") == 500
    page.close()
    page2.close()
