"""Hold to Record (#11): when a hotkey press becomes a dictation take.

A press only arms a timer. Recording starts when the hotkey is still held
after ``delay_ms`` and no other key went down meanwhile (⌃ then C is a
shortcut, not dictation); a quick tap does nothing. Releasing the hotkey
ends a take that the hold started.

Toggle mode passes presses straight on, except for a hotkey made only of
modifiers (Left ⌥, Right ⌘…) (#12): those keys are also used with others
(⌥Tab, ⌥-click), so the toggle happens when the key is let go, and only if
nothing else was pressed or clicked meanwhile and the key was not held for
longer than ``TAP_MAX_MS``. A function key or a key combo still toggles the
instant it goes down.

"Nothing else" is checked twice: the event monitor's chord report, and the
system's input counter (``activity``), which also sees shortcuts another app
consumes before they reach us (⌥Tab in Raycast or AltTab). The counter is
only used with modifier-only hotkeys: a held F4 auto-repeats and would move it.
"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer

TAP_MAX_MS = 1000          # held longer than this, a modifier is not a tap (Karabiner's default)


class HoldGate(QObject):
    def __init__(self, *, hold_mode: Callable[[], bool], delay_ms: Callable[[], int],
                 busy: Callable[[], bool], toggle: Callable[[], None],
                 log: Callable[..., None] = lambda *a, **k: None,
                 modifier_only: Callable[[], bool] = lambda: False,
                 activity: Callable[[], tuple[int, int]] = lambda: (0, 0)) -> None:
        super().__init__()
        self._hold_mode, self._delay_ms, self._busy = hold_mode, delay_ms, busy
        self._toggle, self._log, self._modifier_only = toggle, log, modifier_only
        self._take = False
        self._tap_since: float | None = None      # Toggle + modifier-only: pressed, not yet a tap
        self._activity = activity
        self._activity_at_press = (0, 0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)

    @property
    def armed(self) -> bool:
        return self._timer.isActive()

    def _other_input_since_press(self, own_flag_changes: int = 0) -> bool:
        """Anything besides the hotkey since it went down: a key, click or
        scroll, or more modifier/Caps Lock changes than the hotkey's own
        (``own_flag_changes``: 1 when called on its release)."""
        if not self._modifier_only():
            return False
        keys, flags = self._activity()
        keys0, flags0 = self._activity_at_press
        return keys != keys0 or flags - flags0 > own_flag_changes

    def press(self) -> None:
        self._activity_at_press = self._activity() if self._modifier_only() else (0, 0)
        if not self._hold_mode():
            if self._modifier_only():
                self._tap_since = time.monotonic()
                return
            self._toggle()
            return
        if self._busy():
            self._toggle()          # a take started elsewhere (menu bar): the key stops it
            return
        self._timer.start(max(0, int(self._delay_ms() or 0)))

    def release(self) -> None:
        if self._tap_since is not None:
            held_ms = (time.monotonic() - self._tap_since) * 1000
            self._tap_since = None
            if self._other_input_since_press(own_flag_changes=1):
                self._log("toggle_chord_ignored", source="system")
            elif held_ms <= TAP_MAX_MS:
                self._toggle()
            else:
                self._log("toggle_long_press_ignored", held_ms=int(held_ms))
            return
        if self._timer.isActive():
            self._timer.stop()
            self._log("hold_tap_ignored")
            return
        if self._take:
            self._take = False
            if self._busy():
                self._toggle()

    def chord(self) -> None:
        if self._tap_since is not None:
            self._tap_since = None
            self._log("toggle_chord_ignored")
            return
        if self._timer.isActive():
            self._timer.stop()
            self._log("hold_chord_ignored")

    def _start(self) -> None:
        if self._busy():
            return
        if self._other_input_since_press():
            self._log("hold_chord_ignored", source="system")
            return
        self._take = True
        self._toggle()
