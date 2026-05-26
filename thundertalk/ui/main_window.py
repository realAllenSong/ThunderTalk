"""Main window — sidebar navigation + stacked page content.

Sidebar matches 闪电说 style: warm dark bg, orange bolt logo,
minimal nav items with left accent bar on active.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QRectF, QPoint
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.history import HistoryStore
from thundertalk.core.i18n import bus as i18n_bus, t
from thundertalk.core.settings import Settings
from thundertalk.ui import theme
from thundertalk.ui.pages.about_page import AboutPage
from thundertalk.ui.pages.home_page import HomePage
from thundertalk.ui.pages.hotwords_page import HotwordsPage
from thundertalk.ui.pages.models_page import ModelsPage
from thundertalk.ui.pages.settings_page import SettingsPage

_SIDEBAR_W = 208


def _nav_items() -> list[str]:
    return [t("nav.home"), t("nav.models"), t("nav.hotwords"),
            t("nav.settings"), t("nav.about")]


class _NavButton(QPushButton):
    """Sidebar nav button.

    Active state: 3px orange bar on the left + faint accent-tinted bg + the
    icon takes on the accent color. Hover: subtle background tint.
    """

    def __init__(self, index: int, label: str) -> None:
        super().__init__()
        self._index = index
        self._label = label
        self._active = False
        self._hover = False
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        # Stylesheet handles bg fill + text; we paint the bar + icon ourselves.
        self._update()

    def set_label(self, label: str) -> None:
        self._label = label
        self._update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self._update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def _update(self) -> None:
        if self._active:
            # Faint accent backdrop, primary text
            self.setStyleSheet(
                "QPushButton { background: rgba(249, 115, 22, 0.10);"
                f" color: {theme.TEXT_PRIMARY}; border: none;"
                " text-align: left; padding-left: 44px;"
                " font-size: 13px; font-weight: 600;"
                " border-radius: 8px; margin: 1px 10px; }"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY};"
                " border: none; text-align: left; padding-left: 44px;"
                " font-size: 13px; border-radius: 8px; margin: 1px 10px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
                " background: rgba(255, 255, 255, 0.04); }}"
            )
        self.setText(self._label)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Left accent bar (active state) — 3px wide, 18px tall, centered,
        # painted in the 10px margin area.
        if self._active:
            bar_x = 4
            bar_y = (self.height() - 18) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT_ORANGE))
            p.drawRoundedRect(QRectF(bar_x, bar_y, 3, 18), 1.5, 1.5)

        # Icon — adopts accent color when active for stronger emphasis.
        icon_rect = self.rect().adjusted(20, 0, 0, 0)
        icon_rect.setWidth(20)
        if self._active:
            color = QColor(theme.ACCENT_ORANGE)
        elif self._hover:
            color = QColor(theme.TEXT_PRIMARY)
        else:
            color = QColor(theme.TEXT_SECONDARY)
        p.setPen(QPen(color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        theme.ICON_PAINTERS[self._index](p, icon_rect)
        p.end()


class _LogoBolt(QLabel):
    """Sidebar logo using the actual app icon."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(32, 32)
        import os
        from PySide6.QtGui import QPixmap
        from thundertalk import asset_path
        icon_file = asset_path("icon.png")
        if os.path.isfile(icon_file):
            pm = QPixmap(icon_file).scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.setPixmap(pm)


class _DragArea(QWidget):
    """Sidebar header — drags the window on press+move on macOS."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_pos: Optional[QPoint] = None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                ev.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_pos is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(ev.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(ev)


class MainWindow(QMainWindow):
    load_model_signal = Signal(str, str, str, str)

    def __init__(self, settings: Settings, history: HistoryStore) -> None:
        super().__init__()
        self._settings = settings
        self._titlebar_configured = False
        self.setWindowTitle("ThunderTalk")
        self.setMinimumSize(820, 580)
        self.resize(1060, 720)
        self.setStyleSheet(theme.APP_QSS)

        from thundertalk.ui.tray import app_icon
        self.setWindowIcon(app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Full-width transparent drag strip that hovers over the top 28px
        # (macOS title-bar zone). Sits outside the layout so it doesn't push
        # content down; resizeEvent keeps it sized to the window width.
        self._title_strip = _DragArea(central)
        self._title_strip.setFixedHeight(28)
        self._title_strip.setStyleSheet("background: transparent;")
        self._title_strip.raise_()

        # ── Sidebar ──────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(_SIDEBAR_W)
        sidebar.setStyleSheet(
            f"QWidget#sidebar {{ background: {theme.BG_SIDEBAR};"
            "  border: none;"
            "  border-right: 1px solid rgba(255, 255, 255, 0.08); }}"
        )
        sidebar.setObjectName("sidebar")
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)

        # Logo area — also the window drag handle; top 28 px reserved for
        # macOS traffic-light buttons when titlebar is hidden.
        logo_area = _DragArea()
        logo_area.setFixedHeight(72)
        logo_area.setStyleSheet("background: transparent;")
        logo_ly = QHBoxLayout(logo_area)
        logo_ly.setContentsMargins(18, 28, 16, 0)
        logo_ly.setSpacing(10)

        bolt = _LogoBolt()
        logo_ly.addWidget(bolt)

        name_label = QLabel("ThunderTalk")
        name_label.setFont(theme.font_heading(14))
        name_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        logo_ly.addWidget(name_label)
        logo_ly.addStretch()
        sb.addWidget(logo_area)

        sb.addSpacing(8)

        # Nav buttons
        self._nav_buttons: list[_NavButton] = []
        for i, label in enumerate(_nav_items()):
            btn = _NavButton(i, label)
            btn.clicked.connect(lambda checked, b=btn: self._on_nav(b))
            sb.addWidget(btn)
            self._nav_buttons.append(btn)

        sb.addStretch()

        root.addWidget(sidebar)

        # ── Content ──────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"QStackedWidget {{ background: {theme.BG_BASE}; }}")
        root.addWidget(self._stack, stretch=1)

        self._home_page = HomePage(history)
        self._models_page = ModelsPage(settings)
        self._hotwords_page = HotwordsPage(settings)
        self._settings_page = SettingsPage(settings)
        self._about_page = AboutPage()

        self._stack.addWidget(self._home_page)
        self._stack.addWidget(self._models_page)
        self._stack.addWidget(self._hotwords_page)
        self._stack.addWidget(self._settings_page)
        self._stack.addWidget(self._about_page)

        self._models_page.load_model_signal.connect(
            lambda mid, path, fam, be: self.load_model_signal.emit(mid, path, fam, be)
        )

        self._select_nav(0)

        i18n_bus.language_changed.connect(self._retranslate)

    def _retranslate(self) -> None:
        for btn, label in zip(self._nav_buttons, _nav_items()):
            btn.set_label(label)
        if hasattr(self._home_page, "retranslate"):
            self._home_page.retranslate()
        if hasattr(self._models_page, "retranslate"):
            self._models_page.retranslate()
        if hasattr(self._hotwords_page, "retranslate"):
            self._hotwords_page.retranslate()

    # ── Navigation ───────────────────────────────────────────────

    def _on_nav(self, btn: _NavButton) -> None:
        idx = self._nav_buttons.index(btn)
        self._select_nav(idx)

    def _select_nav(self, idx: int) -> None:
        for i, b in enumerate(self._nav_buttons):
            b.set_active(i == idx)
        self._stack.setCurrentIndex(idx)
        page = self._stack.currentWidget()
        if page:
            theme.fade_in(page, duration=200)

    # ── Public API ───────────────────────────────────────────────

    @property
    def models_page(self) -> ModelsPage:
        return self._models_page

    @property
    def home_page(self) -> HomePage:
        return self._home_page

    @property
    def hotwords_page(self) -> HotwordsPage:
        return self._hotwords_page

    @property
    def settings_page(self) -> SettingsPage:
        return self._settings_page

    @property
    def about_page(self) -> AboutPage:
        return self._about_page

    def show_about(self) -> None:
        """Switch the side-nav selection to About. Used by the
        proactive update popup so the download progress is visible
        immediately after the user clicks Update Now, instead of
        forcing them to manually find the About tab."""
        idx = self._stack.indexOf(self._about_page)
        if idx >= 0:
            self._select_nav(idx)

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._models_page.set_active_model(model_id)

    def show_load_error(self, msg: str) -> None:
        self._models_page.show_load_error(msg)

    # ── macOS frameless title bar ─────────────────────────────────

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._title_strip.resize(self.centralWidget().width(), 28)
        self._title_strip.raise_()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._titlebar_configured:
            self._titlebar_configured = True
            self._setup_macos_titlebar()

    def _setup_macos_titlebar(self) -> None:
        """Hide the system title bar text while keeping traffic-light buttons.

        Uses NSWindowStyleMaskFullSizeContentView so the content view extends
        under the (now transparent) title bar — giving a frameless look while
        macOS still owns shadow, rounded corners, and window management.
        """
        import sys
        if sys.platform != "darwin":
            return
        try:
            import ctypes
            import ctypes.util

            lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))

            def _sel(name: bytes) -> int:
                lib.sel_registerName.restype = ctypes.c_void_p
                lib.sel_registerName.argtypes = [ctypes.c_char_p]
                return lib.sel_registerName(name)

            def _msg(obj, sel, *args, restype=ctypes.c_void_p, argtypes=None):
                lib.objc_msgSend.restype = restype
                lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + (
                    argtypes or []
                )
                return lib.objc_msgSend(obj, sel, *args)

            view = int(self.winId())
            window = _msg(view, _sel(b"window"))
            if not window:
                return

            # Add NSWindowStyleMaskFullSizeContentView (1 << 15 = 32768)
            current = _msg(window, _sel(b"styleMask"), restype=ctypes.c_ulong)
            _msg(
                window, _sel(b"setStyleMask:"),
                ctypes.c_ulong(current | 32768),
                argtypes=[ctypes.c_ulong],
            )

            # Make the title bar transparent so our sidebar bg shows through
            _msg(
                window, _sel(b"setTitlebarAppearsTransparent:"),
                ctypes.c_bool(True),
                argtypes=[ctypes.c_bool],
            )

            # Hide the title text (NSWindowTitleHidden = 1)
            _msg(
                window, _sel(b"setTitleVisibility:"),
                ctypes.c_long(1),
                argtypes=[ctypes.c_long],
            )

            # Allow dragging from any non-widget background area
            _msg(
                window, _sel(b"setMovableByWindowBackground:"),
                ctypes.c_bool(True),
                argtypes=[ctypes.c_bool],
            )

            # Set NSWindow backgroundColor to match our sidebar (#0c0c14) so
            # macOS draws the 1px window border in a dark tone — making it
            # invisible against the near-black sidebar instead of appearing
            # as a bright white line.
            lib.objc_getClass.restype = ctypes.c_void_p
            lib.objc_getClass.argtypes = [ctypes.c_char_p]
            ns_color = lib.objc_getClass(b"NSColor")
            if ns_color:
                dark_bg = _msg(
                    ns_color,
                    _sel(b"colorWithRed:green:blue:alpha:"),
                    ctypes.c_double(12 / 255),
                    ctypes.c_double(12 / 255),
                    ctypes.c_double(20 / 255),
                    ctypes.c_double(1.0),
                    argtypes=[
                        ctypes.c_double, ctypes.c_double,
                        ctypes.c_double, ctypes.c_double,
                    ],
                )
                _msg(
                    window, _sel(b"setBackgroundColor:"),
                    dark_bg,
                    argtypes=[ctypes.c_void_p],
                )

            # Mark window as non-opaque — macOS stops drawing the 1px bright
            # border highlight on non-opaque windows, eliminating the white line
            # visible on near-black dark UI surfaces.
            _msg(
                window, _sel(b"setOpaque:"),
                ctypes.c_bool(False),
                argtypes=[ctypes.c_bool],
            )

            # Qt's NSView returns NO from mouseDownCanMoveWindow by default,
            # blocking setMovableByWindowBackground for the traffic-light row
            # (macOS y=0–28, above Qt's centralWidget). Swizzle the method to
            # return YES so macOS handles native window drag in that zone.
            content_view = _msg(window, _sel(b"contentView"))
            if content_view:
                lib.object_getClass.restype = ctypes.c_void_p
                lib.object_getClass.argtypes = [ctypes.c_void_p]
                view_class = lib.object_getClass(content_view)

                _IMP_T = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                _imp = _IMP_T(lambda s, c: True)
                # Store on the class to prevent the IMP from being GC'd
                MainWindow._mousedown_imp = _imp

                lib.class_replaceMethod.restype = ctypes.c_void_p
                lib.class_replaceMethod.argtypes = [
                    ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_char_p,
                ]
                lib.class_replaceMethod(
                    view_class,
                    _sel(b"mouseDownCanMoveWindow"),
                    _imp,
                    b"c@:",
                )

        except Exception:
            pass

    # ── Close to tray ────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to system tray and return to accessory mode."""
        event.ignore()
        self.hide()
        from thundertalk.core.platform_utils import deactivate_app
        deactivate_app()
