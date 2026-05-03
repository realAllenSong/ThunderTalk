"""Register / unregister ThunderTalk as a macOS Login Item.

The Settings → Startup → "Launch at login" toggle used to be cosmetic
(it stored a JSON flag and nothing else). This module is the missing
OS-side wiring.

Two paths, tried in order:

1. **SMAppService.mainAppService** (macOS 13+) — preferred. No TCC
   prompt, fast, status query is cheap, and the entry shows up under
   System Settings → General → Login Items as expected. PyObjC's
   ServiceManagement framework binding isn't bundled by default, so we
   pull the class via `objc.lookUpClass` after manually loading
   ServiceManagement.framework — that needs only pyobjc-core and
   Foundation, both already imported elsewhere in the app.

2. **AppleScript "make login item"** (macOS 12 fallback, or whenever
   SMAppService refuses) — universal, works on every supported macOS,
   but the first invocation triggers an "ThunderTalk wants to control
   System Events" automation prompt.
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Optional

_IS_MACOS = platform.system() == "Darwin"


def _bundle_path() -> Optional[str]:
    """Path to the running .app bundle, or None when running from
    source (Python interpreter rather than the PyInstaller-frozen
    binary). SMAppService and AppleScript both need a real bundle."""
    exe = sys.executable
    macos_dir = os.path.dirname(exe)            # .../Contents/MacOS
    contents_dir = os.path.dirname(macos_dir)   # .../Contents
    bundle = os.path.dirname(contents_dir)      # .../Foo.app
    if bundle.endswith(".app") and os.path.isdir(bundle):
        return bundle
    return None


_smappservice_class = None
_smappservice_load_attempted = False


def _smappservice():
    """Load SMAppService once; return the class or None on macOS 12 /
    if the framework can't be loaded."""
    global _smappservice_class, _smappservice_load_attempted
    if _smappservice_load_attempted:
        return _smappservice_class
    _smappservice_load_attempted = True
    try:
        import objc
        from Foundation import NSBundle

        bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/ServiceManagement.framework"
        )
        if bundle is None or not bundle.load():
            return None
        _smappservice_class = objc.lookUpClass("SMAppService")
    except Exception as e:
        print(f"[Autostart] SMAppService load failed: {e}")
        _smappservice_class = None
    return _smappservice_class


def _smappservice_set(enabled: bool) -> tuple[bool, Optional[str]]:
    cls = _smappservice()
    if cls is None:
        return False, "SMAppService unavailable"
    service = cls.mainAppService()
    if service is None:
        return False, "mainAppService is nil"
    try:
        if enabled:
            result = service.registerAndReturnError_(None)
        else:
            result = service.unregisterAndReturnError_(None)
    except Exception as e:
        return False, f"call raised: {e}"
    # PyObjC's bridge for `*AndReturnError:` either returns a tuple
    # (BOOL, NSError) or just the BOOL with the error raised. Handle
    # both.
    if isinstance(result, tuple):
        ok, err = result
        return bool(ok), (None if err is None else str(err))
    return bool(result), None


def _smappservice_is_enabled() -> Optional[bool]:
    cls = _smappservice()
    if cls is None:
        return None
    service = cls.mainAppService()
    if service is None:
        return None
    try:
        # SMAppServiceStatus enum:
        #   0 = NotRegistered
        #   1 = Enabled
        #   2 = RequiresApproval (registered but blocked by user)
        #   3 = NotFound
        # We treat 1 + 2 as "enabled" — RequiresApproval still surfaces
        # the entry in System Settings; only NotRegistered is a real off.
        status = int(service.status())
    except Exception:
        return None
    return status in (1, 2)


def _applescript_set(enabled: bool) -> tuple[bool, Optional[str]]:
    bundle = _bundle_path()
    if bundle is None:
        return False, "Not running from a .app bundle"
    try:
        from Foundation import NSAppleScript
    except Exception as e:
        return False, f"NSAppleScript unavailable: {e}"
    name = os.path.basename(bundle)
    if name.endswith(".app"):
        name = name[:-4]
    if enabled:
        src = (
            'tell application "System Events"\n'
            f'  if not (exists login item "{name}") then\n'
            f'    make login item at end with properties '
            f'{{path:"{bundle}", hidden:false, name:"{name}"}}\n'
            '  end if\n'
            'end tell'
        )
    else:
        src = (
            'tell application "System Events"\n'
            f'  if exists login item "{name}" then\n'
            f'    delete login item "{name}"\n'
            '  end if\n'
            'end tell'
        )
    script = NSAppleScript.alloc().initWithSource_(src)
    _result, error = script.executeAndReturnError_(None)
    if error:
        return False, f"AppleScript error: {error}"
    return True, None


def set_enabled(enabled: bool) -> tuple[bool, Optional[str]]:
    """Register / unregister the app as a Login Item.

    Returns (success, error_message). On non-macOS platforms this is
    a no-op that returns (True, None). Refuses if the caller isn't
    running inside a real .app bundle — SMAppService from a bare
    Python interpreter targets *whatever bundle the interpreter
    belongs to*, which would silently pollute Login Items with
    something that has nothing to do with ThunderTalk.
    """
    if not _IS_MACOS:
        return True, None
    if _bundle_path() is None:
        return False, "Not running from a .app bundle (dev mode)"
    ok, err = _smappservice_set(enabled)
    if ok:
        verb = "registered" if enabled else "unregistered"
        print(f"[Autostart] {verb} via SMAppService")
        return True, None
    print(
        f"[Autostart] SMAppService set_enabled({enabled}) failed "
        f"({err}); falling back to AppleScript"
    )
    return _applescript_set(enabled)


def is_enabled() -> bool:
    """Best-effort query of the current OS-side registration state.

    Uses SMAppService when available. We deliberately do NOT use the
    AppleScript path here — querying via System Events would trigger a
    TCC prompt just to read state, which is bad UX. When SMAppService
    isn't available, we return False and let the caller trust the
    stored setting.
    """
    if not _IS_MACOS:
        return False
    if _bundle_path() is None:
        return False
    sm = _smappservice_is_enabled()
    return bool(sm) if sm is not None else False


def sync_with_setting(setting_enabled: bool) -> None:
    """Reconcile OS state with the stored setting on app launch.

    The toggle was a no-op in v1.1.9 and earlier, so users who flipped
    it on saw nothing happen. After updating to a build that includes
    this module, the first launch needs to actually register them.
    Same in reverse if they flipped off in an old build.
    """
    if not _IS_MACOS:
        return
    if setting_enabled and not is_enabled():
        ok, err = set_enabled(True)
        if not ok:
            print(f"[Autostart] sync register failed: {err}")
    elif not setting_enabled and is_enabled():
        ok, err = set_enabled(False)
        if not ok:
            print(f"[Autostart] sync unregister failed: {err}")
