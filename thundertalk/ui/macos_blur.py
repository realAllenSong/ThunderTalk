"""Install a real macOS NSVisualEffectView behind a Qt window so the
window's background becomes the same live-blurred material that
Control Center / Notification Center / HUD windows use.

This is the only path to genuine "frosted glass" on macOS — Qt's QSS
rgba() trick only fakes translucency over a fixed backdrop, it cannot
sample what's behind the window.

Non-Mac platforms (or a venv missing pyobjc) silently no-op so call
sites stay unconditional.
"""

from __future__ import annotations

import platform

# Track winIds we've already wrapped so showEvent re-fires don't keep
# stacking effect views on top of each other.
_APPLIED: set[int] = set()

_MATERIAL_NAMES: dict[str, str] = {
    # Heaviest dark blur — the macOS HUD look (volume / brightness OSD,
    # Mission Control), darker tone than a Finder window.
    "hud":          "HUDWindow",
    # Used by Finder / Mail sidebars, slightly lighter than HUD.
    "sidebar":      "Sidebar",
    # The big translucent panel material — Control Center, Notification
    # Center, Today widgets. This is the closest match to "the listening
    # overlay aesthetic" the user wanted on the main window.
    "under":        "UnderWindowBackground",
    "popover":      "Popover",
    "header":       "HeaderView",
    "fullscreen":   "FullScreenUI",
    # Auto-adapts to whatever macOS thinks is appropriate for a window
    # of this kind. Useful default if the others feel wrong.
    "windowbg":     "WindowBackground",
}


def apply_window_vibrancy(widget, material: str = "under") -> bool:
    """Wrap `widget`'s NSWindow with an NSVisualEffectView at the back.

    Returns True if the effect was attached (or had already been attached
    earlier), False on non-Mac / missing pyobjc / Qt window not native.

    Must be called AFTER the widget has been shown at least once — the
    underlying NSWindow only exists after Qt creates the native window,
    and `widget.winId()` returning a valid pointer relies on that. The
    typical pattern is to call this from `showEvent` once.
    """
    if platform.system() != "Darwin":
        return False

    try:
        import objc
        import AppKit
        from AppKit import (
            NSVisualEffectView,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectStateActive,
            NSColor,
            NSWindowBelow,
            NSViewWidthSizable,
            NSViewHeightSizable,
        )
    except ImportError as e:
        print(f"[Vibrancy] pyobjc not available: {e}")
        return False

    win_id = int(widget.winId())
    if win_id == 0:
        return False
    if win_id in _APPLIED:
        return True

    try:
        # Resolve NSView* → NSWindow*. PySide6's winId() returns the
        # underlying NSView pointer for top-level widgets on macOS.
        ns_view = objc.objc_object(c_void_p=win_id)
        ns_window = ns_view.window()
        if ns_window is None:
            return False

        # Make the Qt-painted NSWindow background see-through. Without
        # this the NSWindow's own backgroundColor (opaque dark gray on
        # macOS) would paint over the effect view.
        ns_window.setOpaque_(False)
        ns_window.setBackgroundColor_(NSColor.clearColor())
        # Title bar transparent so the blur extends under the traffic
        # lights row instead of cutting off at the title bar bottom.
        try:
            ns_window.setTitlebarAppearsTransparent_(True)
        except Exception:
            pass

        material_attr = "NSVisualEffectMaterial" + _MATERIAL_NAMES.get(
            material, "UnderWindowBackground"
        )
        material_const = getattr(
            AppKit, material_attr, AppKit.NSVisualEffectMaterialUnderWindowBackground
        )

        effect = NSVisualEffectView.alloc().init()
        effect.setMaterial_(material_const)
        # BehindWindow blends with desktop / app windows beneath us.
        # WithinWindow would only blend with views inside this window
        # (no use here).
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)

        # The robust pattern is to REPLACE the NSWindow's contentView
        # with the effect view, then make Qt's old contentView a
        # subview of it. Inserting the effect view as a sibling under
        # Qt's contentView is fragile — Qt's CALayer composition may
        # paint opaque pixels on top, hiding the blur. With this
        # arrangement the effect view always paints first (it IS the
        # backdrop), and Qt's full subtree renders on top of it.
        qt_content = ns_window.contentView()
        effect.setFrame_(qt_content.frame())
        effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        ns_window.setContentView_(effect)
        effect.addSubview_(qt_content)
        qt_content.setFrame_(effect.bounds())
        qt_content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        _APPLIED.add(win_id)
        return True
    except Exception as e:
        print(f"[Vibrancy] failed to install effect view: {e}")
        return False
