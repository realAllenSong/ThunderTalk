"""Menu-bar / system-tray icon: status line, quick actions."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from thundertalk.core import state as st
from thundertalk.core.i18n import bus as i18n_bus, t


def app_icon() -> QIcon:
    """Load the app icon from assets, or generate a monochrome fallback."""
    from thundertalk import asset_path
    icon_path = asset_path("icon.png")
    if os.path.isfile(icon_path):
        return QIcon(icon_path)
    return tray_icon()


def tray_icon(color: Optional[QColor] = None) -> QIcon:
    """Menu-bar glyph: a bolt flanked by sound arcs.

    ``color=None`` yields a macOS *template* image (system tints it for light /
    dark menu bars). A colour yields a full-colour icon, used while recording.
    """
    px = QPixmap(44, 44)
    px.setDevicePixelRatio(2.0)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = color or QColor(0, 0, 0)

    bolt = QPainterPath()
    pts = [(12.6, 2.4), (5.6, 12.4), (10.8, 12.4), (9.4, 19.6), (16.4, 9.4), (11.2, 9.4)]
    bolt.moveTo(*pts[0])
    for x, y in pts[1:]:
        bolt.lineTo(x, y)
    bolt.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(bolt)

    pen = QPen(c, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for r in (7.6, 10.6):
        rect = QRectF(11 - r, 11 - r, 2 * r, 2 * r)
        p.drawArc(rect, 150 * 16, 60 * 16)      # left arc
        p.drawArc(rect, -30 * 16, 60 * 16)      # right arc
    p.end()

    icon = QIcon(px)
    if color is None:
        icon.setIsMask(True)
    return icon


class TrayIcon(QSystemTrayIcon):
    toggle_requested = Signal()

    def __init__(self, state: Optional[st.AppState] = None, parent=None) -> None:
        super().__init__(tray_icon(), parent)
        self._state = state

        self._menu = QMenu()
        self._status_action = QAction("")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)
        self._menu.addSeparator()

        self.toggle_action = QAction(t("tray.toggle"))
        self.toggle_action.triggered.connect(self.toggle_requested)
        self._menu.addAction(self.toggle_action)

        self.open_action = QAction(t("tray.open_app"))
        self._menu.addAction(self.open_action)
        self._menu.addSeparator()

        self.quit_action = QAction(t("tray.quit"))
        self._menu.addAction(self.quit_action)

        self.setContextMenu(self._menu)
        self.setToolTip("ThunderTalk")

        i18n_bus.language_changed.connect(self._retranslate)
        if state is not None:
            state.model_changed.connect(self._refresh)
            state.recording_changed.connect(lambda _s: self._refresh())
        self._refresh()

    def _retranslate(self) -> None:
        self.toggle_action.setText(t("tray.toggle"))
        self.open_action.setText(t("tray.open_app"))
        self.quit_action.setText(t("tray.quit"))
        self._refresh()

    def _refresh(self) -> None:
        s = self._state
        if s is None:
            self._status_action.setText("ThunderTalk")
            return
        recording = s.recording == st.REC_RECORDING
        if recording:
            text = t("tray.status.recording")
        elif s.model_status == st.MODEL_READY:
            text = t("tray.status.ready").format(name=s.model_name)
        elif s.model_status == st.MODEL_LOADING:
            text = t("tray.status.loading")
        else:
            text = t("tray.status.setup")
        self._status_action.setText(text)
        self.toggle_action.setEnabled(s.model_status == st.MODEL_READY)
        self.setIcon(tray_icon(QColor(249, 115, 22)) if recording else tray_icon())
        self.setToolTip(f"ThunderTalk — {text}")

    def set_model_status(self, model_name: Optional[str]) -> None:
        """Kept for callers that predate AppState; state drives the menu now."""
        self._refresh()
