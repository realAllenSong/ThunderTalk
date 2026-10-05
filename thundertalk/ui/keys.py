"""Human-readable names for hotkey identifiers ("cmd_r+space" → "Right ⌘ + Space")."""

from __future__ import annotations

from thundertalk.core.i18n import t

_DISPLAY_NAMES: dict[str, str] = {
    "space": "Space", "esc": "Esc", "tab": "Tab",
    "caps_lock": "Caps Lock", "backspace": "⌫", "delete": "⌦",
    "home": "Home", "end": "End",
    "page_up": "PgUp", "page_down": "PgDn",
    "right": "→", "left": "←", "up": "↑", "down": "↓",
    "cmd": "⌘", "cmd_l": "⌘", "cmd_r": "Right ⌘",
    "alt": "⌥", "alt_l": "⌥", "alt_r": "Right ⌥",
    "ctrl": "⌃", "ctrl_l": "⌃", "ctrl_r": "Right ⌃",
    "shift": "⇧", "shift_l": "⇧", "shift_r": "Right ⇧",
}


def display_key(key_name: str) -> str:
    low = key_name.lower().strip()
    if low in ("cmd_r", "alt_r", "ctrl_r", "shift_r"):
        return t("keys.right") + " " + _DISPLAY_NAMES[low].split(" ", 1)[1]
    if low in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[low]
    if low.startswith("f") and low[1:].isdigit():
        return low.upper()
    if len(low) == 1:
        return low.upper()
    return key_name.upper()


def split_combo(combo: str) -> list[str]:
    return [p.strip() for p in combo.split("+") if p.strip()]


def display_combo(combo: str) -> str:
    parts = split_combo(combo)
    if not parts:
        return "None"
    if parts[-1] == "…":
        return " + ".join(display_key(p) for p in parts[:-1]) + " + …"
    return " + ".join(display_key(p) for p in parts)
