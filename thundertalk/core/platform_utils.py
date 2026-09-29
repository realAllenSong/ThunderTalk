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


# ---------------------------------------------------------------------------
# macOS permission checks
# ---------------------------------------------------------------------------

def check_accessibility() -> bool:
    """Return True if accessibility (keyboard simulation) is granted."""
    if _SYSTEM != "Darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return AXIsProcessTrusted()
    except ImportError:
        return True


def request_accessibility() -> bool:
    """Prompt the user to grant accessibility permission. Returns current status."""
    if _SYSTEM != "Darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import kCFBooleanTrue
        options = {"AXTrustedCheckOptionPrompt": kCFBooleanTrue}
        return AXIsProcessTrustedWithOptions(options)
    except ImportError:
        return True


_MIC_STATUS = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized"}


def _mic_status_via_objc() -> str | None:
    """AVCaptureDevice.authorizationStatusForMediaType: through the raw ObjC
    runtime. pyobjc-framework-AVFoundation is not a dependency (and is not
    bundled), so without this the check silently fell open and reported
    "authorized" even when the user had denied access. Returns None if the
    runtime call itself is unavailable."""
    try:
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AVFoundation.framework/AVFoundation"
        )
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        send = lib.objc_msgSend

        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        media_audio = send(  # AVMediaTypeAudio == @"soun"
            lib.objc_getClass(b"NSString"),
            lib.sel_registerName(b"stringWithUTF8String:"),
            b"soun",
        )
        device_cls = lib.objc_getClass(b"AVCaptureDevice")
        if not device_cls or not media_audio:
            return None
        send.restype = ctypes.c_long
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        status = send(
            device_cls,
            lib.sel_registerName(b"authorizationStatusForMediaType:"),
            media_audio,
        )
        return _MIC_STATUS.get(int(status))
    except Exception:
        return None


def check_microphone() -> str:
    """Return microphone permission status: 'authorized', 'denied', 'not_determined', 'restricted'."""
    if _SYSTEM != "Darwin":
        return "authorized"
    try:
        import AVFoundation
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        return _MIC_STATUS.get(status, "authorized")
    except ImportError:
        pass
    return _mic_status_via_objc() or "authorized"


def request_microphone(callback=None) -> None:
    """Trigger the system microphone permission dialog.

    Opening (then immediately closing) an input stream is what makes macOS
    show the prompt when pyobjc's AVFoundation binding isn't available.
    """
    if _SYSTEM != "Darwin":
        return
    try:
        import AVFoundation
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio,
            callback or (lambda granted: None),
        )
        return
    except ImportError:
        pass

    import threading

    def _probe() -> None:
        try:
            import sounddevice as sd
            with sd.InputStream(channels=1, samplerate=16000):
                sd.sleep(150)
        except Exception:
            pass

    threading.Thread(target=_probe, daemon=True, name="mic-permission-probe").start()


def open_accessibility_settings() -> None:
    """Open System Settings > Privacy > Accessibility."""
    if _SYSTEM == "Darwin":
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            check=False,
        )


def open_microphone_settings() -> None:
    """Open System Settings > Privacy > Microphone."""
    if _SYSTEM == "Darwin":
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
            check=False,
        )
