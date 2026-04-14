"""Main window — sidebar navigation + stacked page content.

Sidebar matches 闪电说 style: warm dark bg, orange bolt logo,
minimal nav items with left accent bar on active.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QCloseEvent, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.history import HistoryStore
from thundertalk.core.settings import Settings
from thundertalk.ui import theme
from thundertalk.ui.pages.about_page import AboutPage
from thundertalk.ui.pages.home_page import HomePage
from thundertalk.ui.pages.models_page import ModelsPage
from thundertalk.ui.pages.settings_page import SettingsPage

_SIDEBAR_W = 190

_NAV_ITEMS = ["Home", "Models", "Settings", "About"]


class _NavButton(QPushButton):
    """Sidebar nav button — active state shows left accent bar + bg fill."""

    def __init__(self, index: int, label: str) -> None:
        super().__init__()
        self._index = index
        self._label = label
        self._active = False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self._update()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self._update()

    def _update(self) -> None:
        if self._active:
            self.setStyleSheet(
                f"QPushButton {{ background: {theme.BG_CARD};"
                f" color: {theme.TEXT_PRIMARY}; border: none;"
                " text-align: left; padding-left: 38px;"
                " font-size: 13px; font-weight: 600;"
                f" border-radius: 8px; margin: 2px 14px; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY};"
                " border: none; text-align: left; padding-left: 38px;"
                " font-size: 13px; border-radius: 8px; margin: 2px 14px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
                f" background: rgba(255, 255, 255, 8); }}"
            )
        self.setText(f"   {self._label}")

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon_rect = self.rect().adjusted(24, 0, 0, 0)
        icon_rect.setWidth(22)
        color = QColor(theme.TEXT_PRIMARY) if self._active else QColor(theme.TEXT_SECONDARY)
        p.setPen(QPen(color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        theme.ICON_PAINTERS[self._index](p, icon_rect)
        p.end()


class _LogoBolt(QWidget):
    """Orange rounded-square lightning bolt icon, matching 闪电说 style."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(32, 32)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, 32, 32)
        grad = QLinearGradient(0, 0, 32, 32)
        grad.setColorAt(0, QColor("#fbbf24"))   # Warm amber top
        grad.setColorAt(1, QColor("#f97316"))   # Orange bottom
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.fillPath(path, grad)
        theme.draw_boltPath(p, rect)
        p.end()


class MainWindow(QMainWindow):
    load_model_signal = Signal(str, str, str, str)

    def __init__(self, settings: Settings, history: HistoryStore) -> None:
        super().__init__()
        self._settings = settings
        self.setWindowTitle("ThunderTalk")
        self.setMinimumSize(820, 580)
        self.setStyleSheet(theme.APP_QSS)

        from thundertalk.ui.tray import app_icon
        self.setWindowIcon(app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setFixedWidth(_SIDEBAR_W)
        sidebar.setStyleSheet(
            f"QFrame {{ background: {theme.BG_SIDEBAR};"
            f" border-right: 1px solid {theme.BORDER_SUBTLE}; }}"
        )
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)

        # Logo area
        logo_area = QWidget()
        logo_area.setFixedHeight(64)
        logo_area.setStyleSheet("background: transparent;")
        logo_ly = QHBoxLayout(logo_area)
        logo_ly.setContentsMargins(18, 0, 16, 0)
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
        for i, label in enumerate(_NAV_ITEMS):
            btn = _NavButton(i, label)
            btn.clicked.connect(lambda checked, b=btn: self._on_nav(b))
            sb.addWidget(btn)
            self._nav_buttons.append(btn)

        sb.addStretch()

        # Bottom model status
        self._sidebar_model_label = QLabel("No model loaded")
        self._sidebar_model_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; padding: 14px 18px;"
            " background: transparent;"
        )
        self._sidebar_model_label.setWordWrap(True)
        sb.addWidget(self._sidebar_model_label)

        root.addWidget(sidebar)

        # ── Content ──────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"QStackedWidget {{ background: {theme.BG_BASE}; }}")
        root.addWidget(self._stack, stretch=1)

        self._home_page = HomePage(history)
        self._models_page = ModelsPage()
        self._settings_page = SettingsPage(settings)
        self._about_page = AboutPage()

        self._stack.addWidget(self._home_page)
        self._stack.addWidget(self._models_page)
        self._stack.addWidget(self._settings_page)
        self._stack.addWidget(self._about_page)

        self._models_page.load_model_signal.connect(
            lambda mid, path, fam, be: self.load_model_signal.emit(mid, path, fam, be)
        )

        self._select_nav(0)

    # ── Navigation ───────────────────────────────────────────────

    def _on_nav(self, btn: _NavButton) -> None:
        idx = self._nav_buttons.index(btn)
        self._select_nav(idx)

    def _select_nav(self, idx: int) -> None:
        for i, b in enumerate(self._nav_buttons):
            b.set_active(i == idx)
        self._stack.setCurrentIndex(idx)

    # ── Public API ───────────────────────────────────────────────

    @property
    def models_page(self) -> ModelsPage:
        return self._models_page

    @property
    def home_page(self) -> HomePage:
        return self._home_page

    @property
    def settings_page(self) -> SettingsPage:
        return self._settings_page

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._models_page.set_active_model(model_id)
        if model_id:
            self._sidebar_model_label.setText(f"✓  {model_id}")
            self._sidebar_model_label.setStyleSheet(
                f"color: {theme.SUCCESS}; font-size: 11px; padding: 14px 18px;"
                " background: transparent;"
            )
        else:
            self._sidebar_model_label.setText("No model loaded")
            self._sidebar_model_label.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: 11px; padding: 14px 18px;"
                " background: transparent;"
            )

    def show_load_error(self, msg: str) -> None:
        self._models_page.show_load_error(msg)

    # ── Close to tray ────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to system tray and return to accessory mode."""
        event.ignore()
        self.hide()
        from thundertalk.core.platform_utils import deactivate_app
        deactivate_app()
