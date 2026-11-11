"""Hold to Record (#11): when a hotkey press becomes a dictation take.

A press only arms a timer. Recording starts when the hotkey is still held
after ``delay_ms`` and no other key went down meanwhile (⌃ then C is a
shortcut, not dictation); a quick tap does nothing. Releasing the hotkey
ends a take that the hold started. Toggle mode passes presses straight on.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer


class HoldGate(QObject):
    def __init__(self, *, hold_mode: Callable[[], bool], delay_ms: Callable[[], int],
                 busy: Callable[[], bool], toggle: Callable[[], None],
                 log: Callable[..., None] = lambda *a, **k: None) -> None:
        super().__init__()
        self._hold_mode, self._delay_ms, self._busy = hold_mode, delay_ms, busy
        self._toggle, self._log = toggle, log
        self._take = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)

    @property
    def armed(self) -> bool:
        return self._timer.isActive()

    def press(self) -> None:
        if not self._hold_mode():
            self._toggle()
            return
        if self._busy():
            self._toggle()          # a take started elsewhere (menu bar): the key stops it
            return
        self._timer.start(max(0, int(self._delay_ms() or 0)))

    def release(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._log("hold_tap_ignored")
            return
        if self._take:
            self._take = False
            if self._busy():
                self._toggle()

    def chord(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._log("hold_chord_ignored")

    def _start(self) -> None:
        if self._busy():
            return
        self._take = True
        self._toggle()
