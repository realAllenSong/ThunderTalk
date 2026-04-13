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
_FRONTMOST_ACTIVATION_TIMEOUT = 0.12


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


def paste_text(text: str) -> None:
    """Copy text to clipboard, restore user's app, simulate Cmd+V.

    Runs in a background thread with full mutual exclusion.
    """
    if not text:
        return
    threading.Thread(target=_do_paste, args=(text,), daemon=True).start()


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


def _do_paste(text: str) -> None:
    started_at = time.perf_counter()
    with _paste_lock:
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
