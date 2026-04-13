"""System tray icon with quick-access menu."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def app_icon() -> QIcon:
    """Load the app icon from assets, or generate a fallback."""
    icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icon.png")
    icon_path = os.path.normpath(icon_path)
    if os.path.isfile(icon_path):
        return QIcon(icon_path)
    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(59, 130, 246))
    p.setPen(QColor(59, 130, 246))
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Helvetica Neue", 28, QFont.Weight.Bold))
    p.drawText(px.rect(), 0x0084, "⚡")
    p.end()
    return QIcon(px)


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

        self._status_action = QAction("No model loaded")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        self.open_action = QAction("Open Settings")
        self._menu.addAction(self.open_action)

        self.quit_action = QAction("Quit")
        self._menu.addAction(self.quit_action)

        self.setContextMenu(self._menu)
        self.setToolTip("ThunderTalk")

    def set_model_status(self, model_name: Optional[str]) -> None:
        if model_name:
            self._status_action.setText(f"Model: {model_name}")
        else:
            self._status_action.setText("No model loaded")
