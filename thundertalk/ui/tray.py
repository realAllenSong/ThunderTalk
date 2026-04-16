"""System tray icon with quick-access menu."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from thundertalk.core.i18n import bus as i18n_bus, t


def app_icon() -> QIcon:
    """Load the app icon from assets, or generate a monochrome fallback."""
    from thundertalk import asset_path
    icon_path = asset_path("icon.png")
    if os.path.isfile(icon_path):
        return QIcon(icon_path)
    
    # Generate a sleek macOS native tray icon (monochrome)
    from PySide6.QtCore import QRectF
    px = QPixmap(44, 44)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Instead of importing theme here directly which may have circular deps if not careful,
    # just draw the same bolt shape scaled for 44x44
    from PySide6.QtGui import QPainterPath
    from PySide6.QtCore import Qt
    path = QPainterPath()
    cx, cy = 22, 22
    w, h = 18, 26
    path.moveTo(cx + w*0.15, cy - h*0.45)
    path.lineTo(cx - w*0.35, cy + h*0.05)
    path.lineTo(cx + w*0.15, cy + h*0.05)
    path.lineTo(cx - w*0.15, cy + h*0.45)
    path.lineTo(cx + w*0.35, cy - h*0.15)
    path.lineTo(cx - w*0.15, cy - h*0.15)
    path.closeSubpath()

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(230, 230, 230))  # Standard macOS light grey for dark menubar
    p.drawPath(path)
    p.end()
    
    icon = QIcon(px)
    icon.setIsMask(True) # crucial for macOS native coloring (template icon)
    return icon


class TrayIcon(QSystemTrayIcon):
    def __init__(self, parent=None) -> None:
        super().__init__(app_icon(), parent)

        self._menu = QMenu()
        self._menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #ccc; border: 1px solid #333;"
            " border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; }"
            "QMenu::item:selected { background: #333; }"
        )

        self.open_action = QAction(t("tray.open"))
        self._menu.addAction(self.open_action)

        self.quit_action = QAction(t("tray.quit"))
        self._menu.addAction(self.quit_action)

        self.setContextMenu(self._menu)
        self.setToolTip("ThunderTalk")

        i18n_bus.language_changed.connect(self._retranslate)

    def _retranslate(self) -> None:
        self.open_action.setText(t("tray.open"))
        self.quit_action.setText(t("tray.quit"))

    def set_model_status(self, model_name: Optional[str]) -> None:
        pass
