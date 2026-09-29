"""Main window — paper canvas, quiet sidebar with live status, stacked pages.

The sidebar footer answers "can I dictate right now, and with what?" from
AppState — never guessed. Navigation is text, marked by a small orange tick.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import state as st
from thundertalk.core.history import HistoryStore
from thundertalk.core.i18n import bus as i18n_bus, t
from thundertalk.core.settings import Settings
from thundertalk.core.state import AppState
from thundertalk.ui import theme
from thundertalk.ui.pages.about_page import AboutPage
from thundertalk.ui.pages.home_page import HomePage
from thundertalk.ui.pages.hotwords_page import HotwordsPage
from thundertalk.ui.pages.lab_page import LabPage
from thundertalk.ui.pages.models_page import ModelsPage
from thundertalk.ui.pages.settings_page import SettingsPage
from thundertalk.ui.widgets import BrandMark, KeyCaps, StatusDot, Toast, paint_canvas

_SIDEBAR_W = 224

_PAGES = ["home", "models", "hotwords", "settings", "lab", "about"]


def _nav_items() -> list[str]:
    return [t("nav.home"), t("nav.models"), t("nav.hotwords"),
            t("nav.settings"), t("nav.lab"), t("nav.about")]


class _Canvas(QWidget):
    """Central widget: plain paper behind the pages."""

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        paint_canvas(p, QRectF(self.rect()))
        p.end()


class _Sidebar(QWidget):
    """Warm bone panel with a hairline on its right edge."""

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG_SIDEBAR))
        p.setPen(QPen(theme._BORDER_DEFAULT_C, 1))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        p.end()


class _NavButton(QPushButton):
    """Text nav item. Active: ink + semibold + a 2px orange tick at the edge.
    Hover: a faint ink wash. No icons, no sliding highlight."""

    def __init__(self, index: int, label: str) -> None:
        super().__init__()
        self._index = index
        self._label = label
        self._active = False
        self._hover = False
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.setAccessibleName(label)

    def set_label(self, label: str) -> None:
        self._label = label
        self.setAccessibleName(label)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self.update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(10, 1, -10, -1)
        if self._active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme._HOVER_FILL_C)
            p.drawRoundedRect(r, 5, 5)
            p.setBrush(QColor(theme.ACCENT_ORANGE))
            p.drawRoundedRect(QRectF(10, (self.height() - 16) / 2, 2.5, 16), 1.2, 1.2)
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme._HOVER_FILL_C)
            p.drawRoundedRect(r, 5, 5)
        p.setFont(theme.font(14, bold=self._active))
        p.setPen(QColor(theme.TEXT_PRIMARY if (self._active or self._hover) else theme.TEXT_SECONDARY))
        p.drawText(QRectF(26, 0, self.width() - 36, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)
        p.end()


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


class _StatusBlock(QWidget):
    """Sidebar footer: live dictation status, active model, hotkey. Plain text
    under a hairline — not a card."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover = False
        self.setFixedHeight(112)

        ly = QVBoxLayout(self)
        ly.setContentsMargins(22, 16, 18, 16)
        ly.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(4)
        self._dot = StatusDot(theme.TEXT_MUTED, 8)
        top.addWidget(self._dot)
        self._title = QLabel("")
        self._title.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;")
        top.addWidget(self._title, stretch=1)
        ly.addLayout(top)

        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ly.addWidget(self._sub)
        ly.addSpacing(8)

        self._hotkey_row = QWidget()
        self._hotkey_row.setStyleSheet("background: transparent;")
        hk = QHBoxLayout(self._hotkey_row)
        hk.setContentsMargins(0, 0, 0, 0)
        hk.setSpacing(8)
        self._keycaps = KeyCaps("", height=22)
        hk.addWidget(self._keycaps)
        self._hk_label = QLabel(t("status.to_dictate"))
        self._hk_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        hk.addWidget(self._hk_label)
        hk.addStretch()
        ly.addWidget(self._hotkey_row)

    def apply(self, color: str, pulse: bool, title: str, sub: str,
              hotkey: str, show_hotkey: bool) -> None:
        self._dot.set_state(color)
        self._title.setText(title)
        fm = QFontMetrics(self._sub.font())
        self._sub.setText(fm.elidedText(sub, Qt.TextElideMode.ElideRight, _SIDEBAR_W - 44))
        self._sub.setToolTip(sub)
        self._keycaps.set_combo(hotkey)
        self._hk_label.setText(t("status.to_dictate"))
        self._hotkey_row.setVisible(show_hotkey)
        self.update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self._hover:
            p.fillRect(self.rect().adjusted(0, 1, -1, 0), theme._HOVER_FILL_C)
        p.setPen(QPen(theme._BORDER_DEFAULT_C, 1))
        p.drawLine(0, 0, self.width() - 1, 0)
        p.end()


class MainWindow(QMainWindow):
    load_model_signal = Signal(str, str, str, str)

    def __init__(self, settings: Settings, history: HistoryStore,
                 state: Optional[AppState] = None) -> None:
        super().__init__()
        self._settings = settings
        self._state = state or AppState(settings.hotkey)
        self._titlebar_configured = False
        self._onboarding = None
        self.setWindowTitle("ThunderTalk")
        self.setMinimumSize(900, 620)
        self.resize(1120, 780)
        self.setStyleSheet(theme.APP_QSS)

        from thundertalk.ui.tray import app_icon
        self.setWindowIcon(app_icon())

        central = _Canvas()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Full-width transparent drag strip over the top 28px (macOS title-bar
        # zone). Sits outside the layout; resizeEvent keeps it sized.
        self._title_strip = _DragArea(central)
        self._title_strip.setFixedHeight(28)
        self._title_strip.setStyleSheet("background: transparent;")
        self._title_strip.raise_()

        # ── Sidebar ──────────────────────────────────────────────
        sidebar = _Sidebar()
        sidebar.setFixedWidth(_SIDEBAR_W)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)

        logo_area = _DragArea()
        logo_area.setFixedHeight(88)
        logo_area.setStyleSheet("background: transparent;")
        logo_ly = QHBoxLayout(logo_area)
        logo_ly.setContentsMargins(24, 36, 16, 0)
        logo_ly.setSpacing(10)
        logo_ly.addWidget(BrandMark(26))
        name_label = QLabel("ThunderTalk")
        name_label.setFont(theme.font_heading(17))
        name_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        logo_ly.addWidget(name_label)
        logo_ly.addStretch()
        sb.addWidget(logo_area)

        self._nav_wrap = QWidget()
        self._nav_wrap.setStyleSheet("background: transparent;")
        nav_ly = QVBoxLayout(self._nav_wrap)
        nav_ly.setContentsMargins(0, 8, 0, 8)
        nav_ly.setSpacing(2)
        self._nav_buttons: list[_NavButton] = []
        for i, label in enumerate(_nav_items()):
            btn = _NavButton(i, label)
            btn.clicked.connect(lambda checked, b=btn: self._on_nav(b))
            nav_ly.addWidget(btn)
            self._nav_buttons.append(btn)
        sb.addWidget(self._nav_wrap)
        sb.addStretch()

        self._status = _StatusBlock()
        self._status.clicked.connect(self._on_status_clicked)
        sb.addWidget(self._status)

        root.addWidget(sidebar)

        # ── Content ──────────────────────────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        self._home_page = HomePage(history, self._state)
        self._models_page = ModelsPage(settings)
        self._hotwords_page = HotwordsPage(settings, self._state)
        self._settings_page = SettingsPage(settings)
        self._lab_page = LabPage()
        self._about_page = AboutPage()

        for page in (self._home_page, self._models_page, self._hotwords_page,
                     self._settings_page, self._lab_page, self._about_page):
            self._stack.addWidget(page)

        self._models_page.load_model_signal.connect(
            lambda mid, path, fam, be: self.load_model_signal.emit(mid, path, fam, be)
        )
        self._home_page.navigate_requested.connect(self.navigate)
        self._hotwords_page.word_added.connect(
            lambda w: self.show_toast(t("toast.hotword_added").format(w=w), "success"))
        self._settings_page.run_setup_requested.connect(lambda: self.show_onboarding())
        from thundertalk.ui.keys import display_combo
        self._settings_page.hotkey_changed.connect(
            lambda k: self.show_toast(t("toast.hotkey_saved").format(key=display_combo(k)), "success"))
        self._home_page.copied.connect(lambda: self.show_toast(t("home.copied_toast"), "copy"))
        self._home_page.deleted.connect(lambda: self.show_toast(t("home.deleted"), "info"))

        self._toast = Toast(central)

        self._select_nav(0)

        self._state.model_changed.connect(self._refresh_status)
        self._state.recording_changed.connect(lambda _s: self._refresh_status())
        self._state.hotkey_changed.connect(lambda _k: self._refresh_status())
        i18n_bus.language_changed.connect(self._retranslate)
        self._refresh_status()

    # ── Status block ─────────────────────────────────────────────

    def _refresh_status(self) -> None:
        s = self._state
        name = s.model_name
        if s.recording == st.REC_RECORDING:
            self._status.apply(theme.ACCENT_ORANGE, True, t("status.listening"),
                               name, s.hotkey, True)
        elif s.recording == st.REC_TRANSCRIBING:
            self._status.apply(theme.ACCENT_BLUE, True, t("status.transcribing"),
                               name, s.hotkey, True)
        elif s.model_status == st.MODEL_READY:
            self._status.apply(theme.SUCCESS, False, t("status.ready"), name, s.hotkey, True)
        elif s.model_status == st.MODEL_LOADING:
            self._status.apply(theme.WARNING, True, t("status.loading"),
                               name, s.hotkey, False)
        elif s.model_status == st.MODEL_ERROR:
            self._status.apply(theme.ERROR, False, t("status.error"),
                               s.model_error or name, s.hotkey, False)
        else:
            self._status.apply(theme.WARNING, False, t("status.setup"),
                               t("status.tap_to_fix"), s.hotkey, False)

    def _on_status_clicked(self) -> None:
        if self._state.model_status in (st.MODEL_NONE, st.MODEL_ERROR):
            self.navigate("models")
        else:
            self.navigate("settings")

    def _retranslate(self) -> None:
        for btn, label in zip(self._nav_buttons, _nav_items()):
            btn.set_label(label)
        for page in (self._home_page, self._models_page, self._hotwords_page,
                     self._settings_page, self._lab_page, self._about_page):
            if hasattr(page, "retranslate"):
                page.retranslate()
        self._refresh_status()

    # ── Navigation ───────────────────────────────────────────────

    def _on_nav(self, btn: _NavButton) -> None:
        self._select_nav(self._nav_buttons.index(btn))

    def _select_nav(self, idx: int, animate: bool = False) -> None:
        self._current_idx = idx
        for i, b in enumerate(self._nav_buttons):
            b.set_active(i == idx)
        self._stack.setCurrentIndex(idx)

    def navigate(self, name: str) -> None:
        if name in _PAGES:
            self._select_nav(_PAGES.index(name))

    # ── Public API ───────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        return self._state

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
    def lab_page(self) -> LabPage:
        return self._lab_page

    @property
    def about_page(self) -> AboutPage:
        return self._about_page

    def show_toast(self, text: str, kind: str = "info", ms: int = 2600) -> None:
        self._toast.show_message(text, kind, ms)

    def show_about(self) -> None:
        """Switch the side-nav selection to About. Used by the proactive
        update popup so download progress is visible immediately."""
        self.navigate("about")

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._models_page.set_active_model(model_id)

    def show_load_error(self, msg: str) -> None:
        self._models_page.show_load_error(msg)

    def show_onboarding(self, on_finished=None) -> None:
        """Cover the window with the first-run setup flow."""
        from thundertalk.ui.onboarding import OnboardingOverlay
        if self._onboarding is not None:
            return
        ov = OnboardingOverlay(self.centralWidget(), self._settings, self._state, self._models_page)
        self._onboarding = ov

        def _done(completed: bool) -> None:
            self._onboarding = None
            ov.deleteLater()
            if completed:
                self.show_toast(t("toast.setup_done"), "success", 3600)
            if on_finished:
                on_finished(completed)

        ov.finished.connect(_done)
        ov.navigate_requested.connect(self.navigate)
        ov.setGeometry(self.centralWidget().rect())
        ov.show()
        ov.raise_()
        self._title_strip.raise_()

    # ── macOS frameless title bar ─────────────────────────────────

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._title_strip.resize(self.centralWidget().width(), 28)
        self._title_strip.raise_()
        if self._onboarding is not None:
            self._onboarding.setGeometry(self.centralWidget().rect())

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

            # Set NSWindow backgroundColor to match our sidebar (#F3F1EC) so
            # the window edge blends into the paper instead of showing a
            # contrasting line.
            lib.objc_getClass.restype = ctypes.c_void_p
            lib.objc_getClass.argtypes = [ctypes.c_char_p]
            ns_color = lib.objc_getClass(b"NSColor")
            if ns_color:
                dark_bg = _msg(
                    ns_color,
                    _sel(b"colorWithRed:green:blue:alpha:"),
                    ctypes.c_double(243 / 255),
                    ctypes.c_double(241 / 255),
                    ctypes.c_double(236 / 255),
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
