"""Global hotkey listener — supports combo keys and runtime re-binding.

macOS:  Uses NSEvent.addGlobalMonitorForEvents (safe with IME / TSM).
Other:  Falls back to pynput CGEventTap.

Hotkey format: modifier keys joined with '+', e.g.:
  "cmd_l+space"  "alt_l+shift_l+z"  "ctrl_l+f5"  "f4" (single key)
"""

from __future__ import annotations

import platform
import threading
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


def input_activity() -> tuple[int, int]:
    """System-wide counts so far (HID level, no extra permission):
    (key presses + mouse clicks + scrolls, modifier/Caps Lock changes).
    A shortcut another app consumes (Raycast/AltTab's ⌥Tab) never reaches our
    event monitor but still moves these counters (#12). (0, 0) if unavailable."""
    if _SYSTEM != "Darwin":
        return (0, 0)
    try:
        import Quartz
        st = Quartz.kCGEventSourceStateHIDSystemState
        n = lambda t: int(Quartz.CGEventSourceCounterForEventType(st, t))   # noqa: E731
        return (n(Quartz.kCGEventKeyDown) + n(Quartz.kCGEventLeftMouseDown) + n(Quartz.kCGEventRightMouseDown)
                + n(Quartz.kCGEventOtherMouseDown) + n(Quartz.kCGEventScrollWheel),
                n(Quartz.kCGEventFlagsChanged))
    except Exception:                              # noqa: BLE001
        return (0, 0)


def _is_modifier_name(name: str) -> bool:
    return name in _ALL_MODIFIER_NAMES


ESC_VK = 53
_CMD, _SHIFT, _ALT, _CTRL = 1 << 20, 1 << 17, 1 << 19, 1 << 18


def escape_cancels(combo: list[str], flags: int) -> bool:
    """Esc with these modifier ``flags`` cancels a dictation (#13): plain Esc,
    or Esc while the hotkey's own modifiers are held (Hold to Record). Other
    modifiers make it a shortcut (⌘⌥Esc is Force Quit). Never when Esc is part
    of the hotkey itself."""
    if "esc" in combo:
        return False
    allowed = _SHIFT
    for part in combo:
        allowed |= _MAC_MODIFIER_FLAGS.get(part, 0)
    return not (flags & (_CMD | _ALT | _CTRL) & ~allowed)


# ---------------------------------------------------------------------------
# macOS implementation — NSEvent global monitor (no CGEventTap)
# ---------------------------------------------------------------------------

if _SYSTEM == "Darwin":
    from AppKit import NSEvent, NSKeyDownMask, NSKeyUpMask, NSFlagsChangedMask

    # Mouse buttons and the scroll wheel used together with a held hotkey
    # (⌥-click, ⌘-scroll) make it a shortcut, like another key would (#12).
    _MOUSE_EVENT_TYPES = {1, 3, 25, 22}       # left/right/other mouse down, scroll wheel
    _MOUSE_MASK = (1 << 1) | (1 << 3) | (1 << 25) | (1 << 22)

    class _EscapeTap:
        """Swallows Esc system-wide, but only while armed (a dictation is in
        progress), so the Esc that cancels it doesn't also reach the focused
        app (#13): Claude Code would stop, Vim would leave insert mode.

        An active Quartz event tap needs the Accessibility permission, which
        ThunderTalk already has for pasting. It runs on its own thread with
        its own run loop, so a busy UI thread never delays anyone's typing,
        and it is switched off between dictations: then no key passes
        through it at all. Without the permission it can't be created and
        the listener falls back to observing Esc (which then also reaches
        the focused app)."""

        def __init__(self, on_escape: Callable[[], None], accepts: Callable[[int], bool]) -> None:
            self._on_escape, self._accepts = on_escape, accepts
            self._tap = None
            self._thread: Optional[threading.Thread] = None
            self._armed = False
            self._held = False            # we swallowed an Esc down; swallow its up too

        @property
        def active(self) -> bool:
            return self._tap is not None and self._armed

        def arm(self, armed: bool) -> None:
            self._armed = armed
            if armed and self._tap is None:
                if self._thread is None or not self._thread.is_alive():
                    ready = threading.Event()
                    self._thread = threading.Thread(target=self._run, args=(ready,),
                                                    name="escape-tap", daemon=True)
                    self._thread.start()
                    ready.wait(1.0)
                return
            if self._tap is not None and (armed or not self._held):
                import Quartz
                Quartz.CGEventTapEnable(self._tap, armed)
            # (disarmed with Esc still down: the key-up turns the tap off)

        def _run(self, ready: threading.Event) -> None:
            try:
                import Quartz
                mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp))
                tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                              Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
                if tap is None:
                    print("[Hotkey] Esc tap unavailable (no Accessibility permission); Esc is observed only")
                    return
                source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
                Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
                Quartz.CGEventTapEnable(tap, self._armed)
                self._tap = tap
            except Exception:                      # noqa: BLE001
                traceback.print_exc()
                return
            finally:
                ready.set()
            Quartz.CFRunLoopRun()

        def _callback(self, _proxy, etype, event, _refcon):
            try:
                import Quartz
                if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    if self._tap is not None and (self._armed or self._held):
                        Quartz.CGEventTapEnable(self._tap, True)
                    return event
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) != ESC_VK:
                    return event
                if etype == Quartz.kCGEventKeyUp:
                    if not self._held:
                        return event
                    self._held = False
                    if not self._armed:
                        Quartz.CGEventTapEnable(self._tap, False)
                    return None
                if not self._armed or not self._accepts(int(Quartz.CGEventGetFlags(event))):
                    return event
                if not self._held and not Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat):
                    self._on_escape()
                self._held = True
                return None
            except Exception:                      # noqa: BLE001 — never break the user's keyboard
                traceback.print_exc()
                return event

    class HotkeyListener:
        def __init__(self, on_toggle: Callable[[], None], key_name: str = "f4",
                     on_release: Optional[Callable[[], None]] = None,
                     on_chord: Optional[Callable[[], None]] = None,
                     on_escape: Optional[Callable[[], None]] = None) -> None:
            self._on_toggle = on_toggle
            self._on_release_cb = on_release
            self._on_chord_cb = on_chord
            self._on_escape_cb = on_escape
            self._combo = _parse_combo(key_name)
            self._pressed_vks: set[int] = set()
            self._modifier_state: int = 0
            self._fired = False
            self._monitors: list = []
            self._enabled: bool = True
            self._escape_armed = False
            self._escape_tap = (_EscapeTap(on_escape, lambda flags: escape_cancels(self._combo, flags))
                                if on_escape is not None else None)

        def set_escape_armed(self, armed: bool) -> None:
            """While a dictation is in progress, Esc cancels it (#13)."""
            armed = bool(armed) and self._on_escape_cb is not None and "esc" not in self._combo
            if armed == self._escape_armed:
                return
            self._escape_armed = armed
            if self._escape_tap is not None:
                try:
                    self._escape_tap.arm(armed)
                except Exception:                  # noqa: BLE001
                    traceback.print_exc()

        def set_enabled(self, enabled: bool) -> None:
            """Gate firing while the user is capturing a new hotkey."""
            self._enabled = enabled
            if not enabled:
                self._pressed_vks.clear()
                self._fired = False

        def start(self) -> None:
            mask = NSKeyDownMask | NSKeyUpMask | NSFlagsChangedMask | _MOUSE_MASK

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
                if etype in _MOUSE_EVENT_TYPES:     # mouse events have no keyCode
                    if self._fired and self._on_chord_cb is not None:
                        self._on_chord_cb()
                    return
                vk = event.keyCode()
                # 10 = NSKeyDown, 11 = NSKeyUp, 12 = NSFlagsChanged
                if etype == 10:  # key down
                    if vk == ESC_VK and self._escape_armed and not (
                            self._escape_tap is not None and self._escape_tap.active):
                        # No tap (no permission): observe Esc; the focused app gets it too.
                        if not event.isARepeat() and escape_cancels(self._combo, int(event.modifierFlags())):
                            self._on_escape_cb()
                            return
                    self._pressed_vks.add(vk)
                    self._check_chord(vk)
                    self._check_and_fire()
                elif etype == 11:  # key up
                    self._pressed_vks.discard(vk)
                    self._check_release()
                elif etype == 12:  # flags changed (modifiers)
                    flags = event.modifierFlags()
                    if vk not in self._combo_vks() and self._fired and self._on_chord_cb is not None:
                        # Caps Lock, Fn or another modifier with the hotkey held
                        # (either edge: Caps Lock may report only one).
                        self._on_chord_cb()
                    is_pressed = self._is_modifier_pressed(vk, flags)
                    if is_pressed:
                        self._pressed_vks.add(vk)
                        self._check_chord(vk)
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

        def _combo_vks(self) -> set[int]:
            return {(_MAC_MODIFIER_VK.get(p) if _is_modifier_name(p) else _MAC_VK_MAP.get(p))
                    for p in self._combo} - {None}

        def _check_chord(self, vk: int) -> None:
            """Another key went down while the hotkey is held (⌃ then C):
            the user is typing a shortcut, not asking to dictate. (Modifier
            keys are reported from the flags-changed branch.)"""
            if (self._fired and self._on_chord_cb is not None and vk not in self._combo_vks()
                    and vk not in _MAC_MODIFIER_VK.values()):
                self._on_chord_cb()

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
                    if self._on_release_cb is not None:
                        self._on_release_cb()       # hold-to-record: key up ends the take
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
        def __init__(self, on_toggle: Callable[[], None], key_name: str = "f4",
                     on_release: Optional[Callable[[], None]] = None,
                     on_chord: Optional[Callable[[], None]] = None,
                     on_escape: Optional[Callable[[], None]] = None) -> None:
            self._on_toggle = on_toggle
            self._on_release_cb = on_release
            self._on_chord_cb = on_chord
            self._on_escape_cb = on_escape
            self._escape_armed = False
            self._combo = _parse_pynput_combo(key_name)
            self._pressed: set = set()
            self._listener_obj: Optional[keyboard.Listener] = None
            self._fired = False
            self._enabled: bool = True

        def set_escape_armed(self, armed: bool) -> None:
            """Esc cancels a dictation in progress (observed, not swallowed)."""
            self._escape_armed = (bool(armed) and self._on_escape_cb is not None
                                  and keyboard.Key.esc not in self._combo)

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
                key = self._normalize(key)
                if key == keyboard.Key.esc and self._escape_armed and key not in self._pressed:
                    self._pressed.add(key)
                    self._on_escape_cb()
                    return
                if self._fired and self._on_chord_cb is not None and key not in self._combo:
                    self._on_chord_cb()
                self._pressed.add(key)
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
                    if self._on_release_cb is not None:
                        self._on_release_cb()
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
