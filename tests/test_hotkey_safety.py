import pytest

from thundertalk.ui.keys import display_combo
from thundertalk.ui.pages.settings_page import combo_is_safe


@pytest.mark.parametrize("combo", [
    "cmd_r", "alt_l", "ctrl_l+shift_l", "cmd_l+space", "alt_l+shift_l+z", "f4", "f12", "caps_lock",
])
def test_safe_hotkeys(combo) -> None:
    assert combo_is_safe(combo)


@pytest.mark.parametrize("combo", ["a", "z", "space", "1", "tab", "esc", "backspace", ""])
def test_bare_typing_keys_are_rejected(combo) -> None:
    """A global hotkey on a bare letter would start recording on every keystroke."""
    assert not combo_is_safe(combo)


def test_display_names() -> None:
    assert display_combo("cmd_r") == "Right ⌘"
    assert display_combo("alt_l+shift_l+z") == "⌥ + ⇧ + Z"
    assert display_combo("") == "None"
