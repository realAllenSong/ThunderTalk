"""Global hotkey listener — supports combo keys and runtime re-binding.

macOS:  Uses NSEvent.addGlobalMonitorForEvents (safe with IME / TSM).
Other:  Falls back to pynput CGEventTap.

Hotkey format: modifier keys joined with '+', e.g.:
  "cmd_l+space"  "alt_l+shift_l+z"  "ctrl_l+f5"  "f4" (single key)
"""

from __future__ import annotations

import platform
import traceback
from typing import Callable, Optional

_SYSTEM = platform.system()

# ---------------------------------------------------------------------------
# Common key name → virtual-keycode mappings (macOS)
# ---------------------------------------------------------------------------

_MAC_VK_MAP: dict[str, int] = {
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35,
    "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
    "7": 26, "8": 28, "9": 25,
    "space": 49, "esc": 53, "tab": 48, "backspace": 51, "delete": 117,
    "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "right": 124, "left": 123, "up": 126, "down": 125,
    "caps_lock": 57,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

_MAC_MODIFIER_VK: dict[str, int] = {
    "cmd_l": 55, "cmd_r": 54, "cmd": 55,
    "shift_l": 56, "shift_r": 60, "shift": 56,
    "alt_l": 58, "alt_r": 61, "alt": 58,
    "ctrl_l": 59, "ctrl_r": 62, "ctrl": 59,
}

_MAC_MODIFIER_FLAGS: dict[str, int] = {
    "cmd_l": 1 << 20, "cmd_r": 1 << 20, "cmd": 1 << 20,
    "shift_l": 1 << 17, "shift_r": 1 << 17, "shift": 1 << 17,
    "alt_l": 1 << 19, "alt_r": 1 << 19, "alt": 1 << 19,
    "ctrl_l": 1 << 18, "ctrl_r": 1 << 18, "ctrl": 1 << 18,
}

_ALL_MODIFIER_NAMES = set(_MAC_MODIFIER_VK.keys())


def _parse_combo(combo_str: str) -> list[str]:
    """Parse "cmd_l+space" into ["cmd_l", "space"]."""
    return [p.strip().lower() for p in combo_str.split("+") if p.strip()]


def _is_modifier_name(name: str) -> bool:
    return name in _ALL_MODIFIER_NAMES


# ---------------------------------------------------------------------------
# macOS implementation — NSEvent global monitor (no CGEventTap)
# ---------------------------------------------------------------------------

if _SYSTEM == "Darwin":
    import threading
    from AppKit import NSEvent, NSKeyDownMask, NSKeyUpMask, NSFlagsChangedMask
    from Quartz import (
        CGEventGetIntegerValueField,
        kCGKeyboardEventKeycode,
    )

    class HotkeyListener:
        def __init__(self, on_toggle: Callable[[], None], key_name: str = "f4") -> None:
            self._on_toggle = on_toggle
            self._combo = _parse_combo(key_name)
            self._pressed_vks: set[int] = set()
            self._modifier_state: int = 0
            self._fired = False
            self._monitors: list = []
            self._enabled: bool = True

        def set_enabled(self, enabled: bool) -> None:
            """Gate firing while the user is capturing a new hotkey."""
            self._enabled = enabled
            if not enabled:
                self._pressed_vks.clear()
                self._fired = False

        def start(self) -> None:
            mask = NSKeyDownMask | NSKeyUpMask | NSFlagsChangedMask

            # Global monitor: catches keys when OTHER apps are focused
            gm = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._handle_event,
            )
            if gm:
                self._monitors.append(gm)

            # Local monitor: catches keys when THIS app is focused
            lm = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, self._handle_local_event,
            )
            if lm:
                self._monitors.append(lm)

        def stop(self) -> None:
            for m in self._monitors:
                NSEvent.removeMonitor_(m)
            self._monitors.clear()

        def set_hotkey(self, key_name: str) -> None:
            self._combo = _parse_combo(key_name)
            self._pressed_vks.clear()
            self._modifier_state = 0
            self._fired = False

        def _handle_local_event(self, event) -> object:
            """Local handler must return the event (or None to swallow it)."""
            self._handle_event(event)
            return event

        def _handle_event(self, event) -> None:
            try:
                etype = event.type()
                vk = event.keyCode()
                # 10 = NSKeyDown, 11 = NSKeyUp, 12 = NSFlagsChanged
                if etype == 10:  # key down
                    self._pressed_vks.add(vk)
                    self._check_and_fire()
                elif etype == 11:  # key up
                    self._pressed_vks.discard(vk)
                    self._check_release()
                elif etype == 12:  # flags changed (modifiers)
                    flags = event.modifierFlags()
                    is_pressed = self._is_modifier_pressed(vk, flags)
                    if is_pressed:
                        self._pressed_vks.add(vk)
                        self._check_and_fire()
                    else:
                        self._pressed_vks.discard(vk)
                        self._check_release()
            except Exception:
                traceback.print_exc()

        def _is_modifier_pressed(self, vk: int, flags: int) -> bool:
            """Check if modifier with given vk is currently held."""
            vk_to_flag = {
                55: 1 << 20, 54: 1 << 20,  # cmd_l, cmd_r
                56: 1 << 17, 60: 1 << 17,  # shift_l, shift_r
                58: 1 << 19, 61: 1 << 19,  # alt_l, alt_r
                59: 1 << 18, 62: 1 << 18,  # ctrl_l, ctrl_r
            }
            flag_bit = vk_to_flag.get(vk)
            if flag_bit is None:
                return False
            return bool(flags & flag_bit)

        def _check_release(self) -> None:
            """Reset _fired when the combo is no longer fully held."""
            if not self._fired:
                return
            for part in self._combo:
                if _is_modifier_name(part):
                    vk = _MAC_MODIFIER_VK.get(part)
                else:
                    vk = _MAC_VK_MAP.get(part)
                if vk is None or vk not in self._pressed_vks:
                    print(f"[Hotkey] _fired reset ('{part}' released)")
                    self._fired = False
                    return

        def _check_and_fire(self) -> None:
            if not self._enabled:
                return
            if self._fired:
                return
            if not self._combo:
                return

            for part in self._combo:
                if _is_modifier_name(part):
                    vk = _MAC_MODIFIER_VK.get(part)
                else:
                    vk = _MAC_VK_MAP.get(part)
                if vk is None or vk not in self._pressed_vks:
                    return

            self._fired = True
            print(f"[Hotkey] FIRED! combo={self._combo}")
            self._on_toggle()

else:
    # -----------------------------------------------------------------------
    # Non-macOS fallback using pynput
    # -----------------------------------------------------------------------
    from pynput import keyboard

    _SPECIAL_KEY_MAP: dict[str, keyboard.Key] = {
        "f1": keyboard.Key.f1, "f2": keyboard.Key.f2, "f3": keyboard.Key.f3,
        "f4": keyboard.Key.f4, "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
        "f7": keyboard.Key.f7, "f8": keyboard.Key.f8, "f9": keyboard.Key.f9,
        "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
        "space": keyboard.Key.space,
        "esc": keyboard.Key.esc,
        "caps_lock": keyboard.Key.caps_lock,
        "tab": keyboard.Key.tab,
        "backspace": keyboard.Key.backspace,
        "delete": keyboard.Key.delete,
        "home": keyboard.Key.home,
        "end": keyboard.Key.end,
        "page_up": keyboard.Key.page_up,
        "page_down": keyboard.Key.page_down,
        "right": keyboard.Key.right,
        "left": keyboard.Key.left,
        "up": keyboard.Key.up,
        "down": keyboard.Key.down,
        "cmd": keyboard.Key.cmd,
        "cmd_l": keyboard.Key.cmd_l,
        "cmd_r": keyboard.Key.cmd_r,
        "alt": keyboard.Key.alt,
        "alt_l": keyboard.Key.alt_l,
        "alt_r": keyboard.Key.alt_r,
        "ctrl": keyboard.Key.ctrl,
        "ctrl_l": keyboard.Key.ctrl_l,
        "ctrl_r": keyboard.Key.ctrl_r,
        "shift": keyboard.Key.shift,
        "shift_l": keyboard.Key.shift_l,
        "shift_r": keyboard.Key.shift_r,
    }

    def _resolve_key(name: str):
        name = name.lower().strip()
        if name in _SPECIAL_KEY_MAP:
            return _SPECIAL_KEY_MAP[name]
        if len(name) == 1:
            return keyboard.KeyCode.from_char(name)
        return None

    def _parse_pynput_combo(combo_str: str) -> list:
        parts = [p.strip() for p in combo_str.split("+") if p.strip()]
        return [k for p in parts if (k := _resolve_key(p)) is not None]

    class HotkeyListener:
        def __init__(self, on_toggle: Callable[[], None], key_name: str = "f4") -> None:
            self._on_toggle = on_toggle
            self._combo = _parse_pynput_combo(key_name)
            self._pressed: set = set()
            self._listener_obj: Optional[keyboard.Listener] = None
            self._fired = False
            self._enabled: bool = True

        def set_enabled(self, enabled: bool) -> None:
            """Gate firing while the user is capturing a new hotkey."""
            self._enabled = enabled
            if not enabled:
                self._pressed.clear()
                self._fired = False

        def start(self) -> None:
            self._listener_obj = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener_obj.daemon = True
            self._listener_obj.start()

        def stop(self) -> None:
            if self._listener_obj:
                self._listener_obj.stop()
                self._listener_obj = None

        def set_hotkey(self, key_name: str) -> None:
            self._combo = _parse_pynput_combo(key_name)
            self._pressed.clear()
            self._fired = False

        def _normalize(self, key: object) -> object:
            try:
                if isinstance(key, keyboard.KeyCode) and key.char:
                    return keyboard.KeyCode.from_char(key.char.lower())
            except Exception:
                pass
            return key

        def _on_press(self, key) -> None:
            try:
                if key is None or not self._combo:
                    return
                self._pressed.add(self._normalize(key))
                if not self._enabled:
                    return
                if not self._fired and self._check_combo():
                    self._fired = True
                    self._on_toggle()
            except Exception:
                traceback.print_exc()

        def _on_release(self, key) -> None:
            try:
                if key is None:
                    return
                self._pressed.discard(self._normalize(key))
                if self._fired and not self._check_combo():
                    self._fired = False
            except Exception:
                pass

        def _check_combo(self) -> bool:
            try:
                for target in self._combo:
                    if not any(p == target or (
                        isinstance(p, keyboard.KeyCode) and isinstance(target, keyboard.KeyCode)
                        and p.char and target.char and p.char.lower() == target.char.lower()
                    ) for p in self._pressed):
                        return False
                return True
            except Exception:
                return False
