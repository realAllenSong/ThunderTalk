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


def test_pipeline_measures_how_long_the_key_was_held(qapp, isolated_home):
    import time
    from thundertalk.app import Pipeline
    from thundertalk.core.settings import Settings
    pipe = Pipeline(Settings())
    pipe.toggle()
    time.sleep(.4)
    pipe.release()
    assert .35 <= pipe.held_s < 1.5
    pipe.toggle()
    pipe.release()
    assert pipe.held_s < .1                            # a tap
