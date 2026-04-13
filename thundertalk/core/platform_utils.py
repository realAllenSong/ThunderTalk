"""Platform-specific utilities for window management and focus control.

On macOS, sets the application activation policy to "Accessory" so that
ThunderTalk never steals focus from the user's active application.
The overlay can float on top without disrupting the user's cursor position
in their chat window or text editor.
"""

from __future__ import annotations

import platform
import ctypes
import ctypes.util

_SYSTEM = platform.system()
_objc = None
_NSApp = None


def _init_objc() -> bool:
    """Lazily load the Objective-C runtime (macOS only)."""
    global _objc, _NSApp
    if _objc is not None:
        return _NSApp is not None
    try:
        lib_path = ctypes.util.find_library("objc")
        if not lib_path:
            return False
        _objc = ctypes.cdll.LoadLibrary(lib_path)
        _objc.objc_getClass.restype = ctypes.c_void_p
        _objc.sel_registerName.restype = ctypes.c_void_p
        _objc.objc_msgSend.restype = ctypes.c_void_p
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        _NSApp = _objc.objc_msgSend(
            _objc.objc_getClass(b"NSApplication"),
            _objc.sel_registerName(b"sharedApplication"),
        )
        return _NSApp is not None
    except Exception:
        return False


def set_accessory_app() -> None:
    """Make the app an 'accessory' — no Dock icon, never steals focus.

    This is the standard pattern for input-method / overlay style apps
    like 闪电说 and Typeless.
    """
    if _SYSTEM != "Darwin":
        return
    if not _init_objc():
        return
    # NSApplicationActivationPolicyAccessory = 1
    _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    _objc.objc_msgSend(_NSApp, _objc.sel_registerName(b"setActivationPolicy:"), 1)


def activate_app() -> None:
    """Explicitly bring ThunderTalk to front (for settings window)."""
    if _SYSTEM != "Darwin":
        return
    if not _init_objc():
        return
    # [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular]
    _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    _objc.objc_msgSend(_NSApp, _objc.sel_registerName(b"setActivationPolicy:"), 0)
    # [NSApp activateIgnoringOtherApps:YES]
    _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
    _objc.objc_msgSend(_NSApp, _objc.sel_registerName(b"activateIgnoringOtherApps:"), True)


def deactivate_app() -> None:
    """Return to accessory mode after settings window is hidden."""
    if _SYSTEM != "Darwin":
        return
    if not _init_objc():
        return
    _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    _objc.objc_msgSend(_NSApp, _objc.sel_registerName(b"setActivationPolicy:"), 1)
