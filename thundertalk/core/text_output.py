"""Paste recognised text into the user's previously-active application.

Core workflow:
1. When recording starts -> save the name of the frontmost app (SYNCHRONOUS).
2. When ASR finishes -> restore that app -> clipboard -> Cmd+V / Ctrl+V.

Reliability improvements:
- Clipboard write-back verification with retry.
- Full mutual exclusion on the paste sequence.
- Focus confirmation before simulating keystroke.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import platform
import subprocess
import threading
import time

import pyperclip

if platform.system() == "Darwin":
    from AppKit import (
        NSApplicationActivateIgnoringOtherApps,
        NSRunningApplication,
        NSWorkspace,
    )
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

_previous_app: str | None = None
_previous_app_pid: int | None = None
_lock = threading.Lock()
_paste_lock = threading.Lock()
_SYSTEM = platform.system()

_MAX_CLIPBOARD_RETRIES = 3
_CLIPBOARD_RETRY_DELAY = 0.015
_CLIPBOARD_WRITE_SETTLE = 0.01
_POST_CLIPBOARD_SETTLE = 0.01
_FRONTMOST_POLL_INTERVAL = 0.01
_FRONTMOST_ACTIVATION_TIMEOUT = 0.30


def save_frontmost_app() -> None:
    """Synchronously snapshot the currently active app BEFORE overlay shows."""
    global _previous_app, _previous_app_pid
    if _SYSTEM == "Darwin":
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            name = str(app.localizedName() or "").strip() if app else ""
            if name and name.lower() not in ("thundertalk", "python", "python3"):
                _previous_app = name
                _previous_app_pid = int(app.processIdentifier()) if app else None
                print(f"[Focus] Saved frontmost app: {name}")
            else:
                print(f"[Focus] Frontmost is self ({name}), keeping previous: {_previous_app}")
        except Exception:
            pass


def paste_text(text: str, keep_clipboard: bool = False) -> None:
    """Copy text to clipboard, restore user's app, simulate Cmd+V.

    If *keep_clipboard* is True, the user's original clipboard content
    is saved before pasting and restored afterward so the paste operation
    is transparent to the user.

    Runs in a background thread with full mutual exclusion.
    """
    if not text:
        return
    threading.Thread(target=_do_paste, args=(text, keep_clipboard), daemon=True).start()


def replace_pasted_text(new_text: str, keep_clipboard: bool = False, ticket=None) -> None:
    """Undo the most recently pasted text, then paste *new_text*.

    Used by Translation Review's "Replace" button to swap original →
    translation without selection trickery. Relies on the active app
    honoring standard Cmd+Z (undo). If the user has typed/clicked
    between the original paste and clicking Replace, Cmd+Z will undo
    the wrong action — the popup auto-dismisses after a few seconds
    to keep this window narrow.

    AI callers MUST supply a PasteTicket (fails closed on user activity).
    The no-ticket path retains the existing explicit Translation Review action.
    Runs in a background thread; non-blocking.
    """
    if ticket is not None:
        threading.Thread(target=apply_if_unchanged,
                         args=(ticket, new_text, keep_clipboard), daemon=True).start()
        return
    if not new_text:
        return

    def _undo_and_paste() -> None:
        # Re-activate the previously focused app before sending Cmd+Z so
        # the undo lands on the user's target app, not on ThunderTalk.
        # _do_paste also re-activates, but we need it BEFORE the undo.
        if _SYSTEM == "Darwin":
            try:
                _activate_previous_app()
                with _lock:
                    prev = _previous_app
                if prev:
                    _wait_for_frontmost_app(prev)
            except Exception:
                pass

        # Send Cmd+Z to undo the original paste
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to keystroke "z" using command down',
                ],
                check=False,
                capture_output=True,
                timeout=2,
            )
        except Exception:
            pass

        # Brief settle so the undo lands before we kick off the paste
        time.sleep(0.05)

        # Now paste the translation via the existing paste path
        _do_paste(new_text, keep_clipboard)

    threading.Thread(target=_undo_and_paste, daemon=True).start()


def _clipboard_write_verified(text: str) -> bool:
    """Write to clipboard and verify. Returns True if verified."""
    for attempt in range(_MAX_CLIPBOARD_RETRIES):
        try:
            pyperclip.copy(text)
            time.sleep(_CLIPBOARD_WRITE_SETTLE)
            readback = pyperclip.paste()
            if readback == text:
                return True
        except Exception:
            pass
        if attempt < _MAX_CLIPBOARD_RETRIES - 1:
            time.sleep(_CLIPBOARD_RETRY_DELAY)
    return False


def _get_frontmost_app() -> str:
    """Return the name of the currently frontmost application."""
    if _SYSTEM == "Darwin":
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.localizedName() or "").strip() if app else ""
        except Exception:
            return ""
    return ""


def _activate_previous_app() -> bool:
    """Bring the previously active app back to front on macOS."""
    if _SYSTEM != "Darwin":
        return False

    with _lock:
        prev = _previous_app
        prev_pid = _previous_app_pid

    try:
        if prev_pid:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(prev_pid)
            if app:
                return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        pass

    if not prev:
        return False

    try:
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            name = str(app.localizedName() or "").strip()
            if name and name.lower() == prev.lower():
                return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        pass
    return False


def _send_cmd_v_darwin() -> None:
    """Simulate Cmd+V using Quartz instead of spawning osascript."""
    v_keycode = 9
    key_down = CGEventCreateKeyboardEvent(None, v_keycode, True)
    key_up = CGEventCreateKeyboardEvent(None, v_keycode, False)
    CGEventSetFlags(key_down, kCGEventFlagMaskCommand)
    CGEventSetFlags(key_up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, key_down)
    CGEventPost(kCGHIDEventTap, key_up)


def _wait_for_frontmost_app(target_app: str, timeout: float = _FRONTMOST_ACTIVATION_TIMEOUT) -> bool:
    """Poll until the requested app is actually frontmost."""
    deadline = time.perf_counter() + timeout
    target = target_app.lower().strip()
    while time.perf_counter() < deadline:
        current = _get_frontmost_app()
        if current and current.lower() == target:
            return True
        time.sleep(_FRONTMOST_POLL_INTERVAL)
    return False


def _do_paste(text: str, keep_clipboard: bool = False) -> None:
    started_at = time.perf_counter()
    with _paste_lock:
        # Save original clipboard if we need to restore it later
        original_clipboard = None
        if keep_clipboard:
            try:
                original_clipboard = _save_clipboard()
            except Exception:
                original_clipboard = None

        try:
            with _lock:
                prev = _previous_app

            if _SYSTEM == "Darwin" and prev:
                current = _get_frontmost_app()
                try:
                    if current.lower() != prev.lower():
                        _activate_previous_app()
                        if not _wait_for_frontmost_app(prev):
                            _activate_previous_app()
                            _wait_for_frontmost_app(prev)
                except Exception:
                    pass

            if not _clipboard_write_verified(text):
                pyperclip.copy(text)
                print("[Paste] Clipboard verification failed — wrote anyway")

            time.sleep(_POST_CLIPBOARD_SETTLE)

            if _SYSTEM == "Darwin":
                _send_cmd_v_darwin()
            elif _SYSTEM == "Linux":
                subprocess.run(["xdotool", "key", "ctrl+v"], check=False, timeout=3)
            elif _SYSTEM == "Windows":
                try:
                    from pynput.keyboard import Controller, Key
                    kb = Controller()
                    kb.press(Key.ctrl_l)
                    kb.press("v")
                    kb.release("v")
                    kb.release(Key.ctrl_l)
                except Exception:
                    pass

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            print(f"[Paste] Submitted to target app in {elapsed_ms}ms (target={prev or 'current'})")

        finally:
            # Restore original clipboard after paste completes
            if keep_clipboard and original_clipboard is not None:
                time.sleep(0.15)
                try:
                    _restore_clipboard(original_clipboard)
                    print("[Paste] Restored original clipboard")
                except Exception:
                    pass


# Guard delayed AI output with actual user activity, not an elapsed-time guess.
# Synthetic events posted by this process and the dictation hotkey are excluded.


class InputActivity:
    def __init__(self):
        self.generation = 0
        self.available = False
        self._monitors = []
        self._observer = None
        self._hotkey_codes = set()
        self._hotkey_flags = 0

    def set_hotkey(self, combo: str):
        from thundertalk.core.hotkey import _MAC_VK_MAP, _MAC_MODIFIER_FLAGS
        parts = combo.split("+")
        self._hotkey_codes = {_MAC_VK_MAP[p] for p in parts if p in _MAC_VK_MAP}
        self._hotkey_flags = 0
        for part in parts:
            self._hotkey_flags |= _MAC_MODIFIER_FLAGS.get(part, 0)

    def start(self):
        if _SYSTEM != "Darwin":
            return
        try:
            from AppKit import (NSEvent, NSKeyDownMask, NSLeftMouseDownMask,
                                NSRightMouseDownMask, NSOtherMouseDownMask, NSScrollWheelMask,
                                NSWorkspaceDidActivateApplicationNotification)
            mask = (NSKeyDownMask | NSLeftMouseDownMask | NSRightMouseDownMask |
                    NSOtherMouseDownMask | NSScrollWheelMask)
            gm = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._event)
            lm = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self._local_event)
            self._monitors = [m for m in (gm, lm) if m is not None]
            self._observer = NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
                NSWorkspaceDidActivateApplicationNotification, None, None, self._focus_changed)
            from ApplicationServices import AXIsProcessTrusted
            self.available = (bool(AXIsProcessTrusted()) and gm is not None
                              and lm is not None and self._observer is not None)
        except Exception:
            self.available = False

    def _event(self, event):
        try:
            from Quartz import CGEventGetIntegerValueField, kCGEventSourceUnixProcessID
            cg = event.CGEvent()
            if cg and CGEventGetIntegerValueField(cg, kCGEventSourceUnixProcessID) == os.getpid():
                return
            if (int(event.type()) == 10 and int(event.keyCode()) in self._hotkey_codes
                    and int(event.modifierFlags()) & self._hotkey_flags == self._hotkey_flags):
                return
        except Exception:
            pass  # unknown event => invalidate conservatively
        self.generation += 1

    def _local_event(self, event):
        self._event(event)
        return event

    def _focus_changed(self, _notification):
        self.generation += 1

    def stop(self):
        if _SYSTEM == "Darwin":
            from AppKit import NSEvent
            for monitor in self._monitors:
                NSEvent.removeMonitor_(monitor)
            if self._observer is not None:
                NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self._observer)
        self._monitors = []
        self._observer = None
        self.available = False


activity = InputActivity()


def frontmost_app() -> str:
    return _previous_app or ""


def _frontmost_pid() -> int | None:
    try:
        return int(NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
    except Exception:
        return None


def supports_undo(app: str) -> bool:
    # Terminal command input has no standard paste undo transaction.
    return not any(s in app.casefold() for s in ("terminal", "iterm", "warp", "ghostty", "alacritty"))


@dataclass
class PasteTicket:
    app: str
    pid: int | None
    generation: int
    ready: threading.Event = field(default_factory=threading.Event)
    valid: bool = True
    successor: PasteTicket | None = None

    def invalidate(self):
        self.valid = False
        if self.successor is not None:
            self.successor.invalidate()

    def unchanged(self) -> bool:
        return (self.valid and self.ready.is_set() and activity.available and
                self.generation == activity.generation and self.pid is not None and
                self.pid == _frontmost_pid() and self.app == _get_frontmost_app())


def paste_dictation(text: str, keep_clipboard=False) -> PasteTicket:
    """Paste raw text immediately and return a ticket for conditional replacement."""
    ticket = PasteTicket(frontmost_app(), _previous_app_pid, activity.generation)

    def run():
        try:
            _do_paste(text, keep_clipboard)
            # Never absorb user activity that happened during paste into baseline.
            ticket.valid = ticket.valid and ticket.generation == activity.generation and supports_undo(ticket.app)
        except Exception:
            ticket.valid = False
        finally:
            ticket.ready.set()
    threading.Thread(target=run, daemon=True).start()
    return ticket


def _send_cmd_key(keycode: int):
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def apply_if_unchanged(ticket: PasteTicket, text: str, keep_clipboard=False, cancel=None) -> bool:
    """Synchronous worker-side guarded replacement; never restores stale focus.

    Undoes the untouched paste with standard macOS Cmd+Z, then pastes *text*.
    """
    while ticket.successor is not None:
        ticket = ticket.successor
    ticket.ready.wait(2)
    with _paste_lock:
        if (cancel is not None and cancel.is_set()) or not ticket.unchanged() or _SYSTEM != "Darwin":
            return False
        if not text:
            return False
        original = _save_clipboard() if keep_clipboard else None
        try:
            # Prepare clipboard before final guard to minimize the key-event race.
            if not _clipboard_write_verified(text):
                return False
            if (cancel is not None and cancel.is_set()) or not ticket.unchanged():
                return False
            ticket.valid = False  # exactly once; reject any competing result
            _send_cmd_key(6)  # Z
            time.sleep(0.05)
            _send_cmd_v_darwin()
            successor = PasteTicket(ticket.app, ticket.pid, ticket.generation)
            successor.ready.set()
            ticket.successor = successor
            return True
        finally:
            if original is not None:
                time.sleep(0.15)
                _restore_clipboard(original)


def _save_clipboard():
    """Save all macOS pasteboard formats, including rich text/images."""
    if _SYSTEM == "Darwin":
        from AppKit import NSPasteboard
        board = NSPasteboard.generalPasteboard()
        return [[(str(kind), bytes(item.dataForType_(kind))) for kind in item.types()
                 if item.dataForType_(kind) is not None] for item in board.pasteboardItems() or []]
    return pyperclip.paste()


def _restore_clipboard(saved):
    if _SYSTEM == "Darwin":
        from AppKit import NSPasteboard, NSPasteboardItem
        from Foundation import NSData
        board = NSPasteboard.generalPasteboard()
        items = []
        for entry in saved:
            item = NSPasteboardItem.alloc().init()
            for kind, data in entry:
                item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), kind)
            items.append(item)
        board.clearContents()
        if items:
            board.writeObjects_(items)
    else:
        pyperclip.copy(saved)
