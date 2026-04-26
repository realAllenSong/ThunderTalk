"""Main window — sidebar navigation + stacked page content.

Sidebar matches 闪电说 style: warm dark bg, orange bolt logo,
minimal nav items with left accent bar on active.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPen
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
            "  border: none;"
            "  border-right: 1px solid rgba(255, 255, 255, 0.04); }"
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

    def set_active_model(self, model_id: Optional[str]) -> None:
        self._models_page.set_active_model(model_id)

    def show_load_error(self, msg: str) -> None:
        self._models_page.show_load_error(msg)

    # ── Close to tray ────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to system tray and return to accessory mode."""
        event.ignore()
        self.hide()
        from thundertalk.core.platform_utils import deactivate_app
        deactivate_app()
