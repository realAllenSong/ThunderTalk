"""Where the user is typing: a point inside the front window of an app.

The dictation bar is shown on the display that holds that point (#14). The
window list comes from the window server (Quartz), not from the app, so a
hung app cannot delay the bar, and window bounds need no extra permission.
Coordinates are global, in points, origin at the top left of the main
display, the same space Qt uses.
"""
from __future__ import annotations

import platform
from typing import Optional


def window_center(pid: Optional[int]) -> Optional[tuple[int, int]]:
    """Centre of the front-most normal window of app ``pid`` (None: the front-most app), or None."""
    if platform.system() != "Darwin":
        return None
    try:
        import Quartz
        if not pid:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            pid = int(app.processIdentifier()) if app else None
        if not pid:
            return None
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        return front_window_center(windows, int(pid))
    except Exception as exc:                       # the bar falls back to its last display
        print(f"[Display] Front window not found: {type(exc).__name__}")
        return None


def front_window_center(windows, pid: int) -> Optional[tuple[int, int]]:
    """``windows`` is front to back. Menus, tooltips and other overlays sit on higher layers
    and tiny helper windows are skipped: the first real window of the app is where it is used."""
    for w in windows:
        if int(w.get("kCGWindowOwnerPID", -1)) != pid or int(w.get("kCGWindowLayer", 1)) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        width, height = float(b.get("Width", 0)), float(b.get("Height", 0))
        if width < 120 or height < 60 or float(w.get("kCGWindowAlpha", 1)) <= 0:
            continue
        return int(float(b.get("X", 0)) + width / 2), int(float(b.get("Y", 0)) + height / 2)
    return None
