"""Frameless, dark-themed modal dialog matching the rest of the app.

System QMessageBox uses macOS native chrome — light frame, native
buttons, font that clashes with our pure-black + accent palette.
This dialog draws its own rounded panel (BG_CARD with a faint
border-rim) and uses our pill / accent button helpers.

Usage:
    if StyledDialog.confirm(
        parent,
        title="Clear all history?",
        body="This permanently deletes every transcription...",
        accept_label="Clear",
        cancel_label="Cancel",
        destructive=True,
    ):
        ...
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from thundertalk.ui import theme


class StyledDialog(QDialog):
    """Frameless modal dialog with custom-painted background.

    Drawn at runtime: rounded rect filled with BG_CARD, 1 px
    BORDER_DEFAULT outline, soft drop shadow on the parent window
    by virtue of macOS's native shadow under translucent windows.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        *,
        title: str,
        body: str,
        accept_label: str,
        cancel_label: str,
        destructive: bool = False,
    ) -> None:
        super().__init__(parent)
        # Frameless + translucent so the rounded background is the
        # only thing visible; macOS still draws its drop shadow.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Inner panel — the actual painted card
        panel = QWidget()
        panel.setObjectName("styledDialogPanel")
        panel.setStyleSheet(
            "#styledDialogPanel { background: transparent; }"
        )
        outer.addWidget(panel)

        ly = QVBoxLayout(panel)
        ly.setContentsMargins(28, 24, 28, 22)
        ly.setSpacing(14)

        # Title — TEXT_PRIMARY, bold, larger.
        title_lbl = QLabel(title)
        title_lbl.setFont(theme.font_heading(15))
        title_lbl.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent;"
            " border: none;"
        )
        title_lbl.setWordWrap(True)
        ly.addWidget(title_lbl)

        # Body — TEXT_SECONDARY, regular, wraps.
        body_lbl = QLabel(body)
        body_lbl.setFont(theme.font(12))
        body_lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; background: transparent;"
            " border: none; line-height: 1.4;"
        )
        body_lbl.setWordWrap(True)
        ly.addWidget(body_lbl)

        ly.addSpacing(4)

        # Buttons row — right-aligned, cancel on the left of accept,
        # native macOS dialog convention.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton(cancel_label)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setFixedHeight(34)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" color: {theme.TEXT_SECONDARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT};"
            " border-radius: 17px; padding: 0 20px; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_STRONG}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        accent = theme.ERROR if destructive else theme.ACCENT_BLUE
        accent_hover = theme.ERROR if destructive else theme.ACCENT_BLUE_HOVER
        accept_btn = QPushButton(accept_label)
        accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        accept_btn.setFixedHeight(34)
        accept_btn.setStyleSheet(
            f"QPushButton {{ background: {accent};"
            " color: #ffffff; border: none; border-radius: 17px;"
            " padding: 0 24px; font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {accent_hover}; }}"
        )
        accept_btn.setDefault(True)
        accept_btn.setAutoDefault(True)
        accept_btn.clicked.connect(self.accept)
        btn_row.addWidget(accept_btn)

        ly.addLayout(btn_row)

        # Make Cancel the implicit "Escape" target — pressing Esc
        # dismisses the dialog as a Cancel.
        cancel_btn.setShortcut("Esc")

    # Custom paint — rounded BG_CARD panel with a translucent rim.
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(
            0.5, 0.5, -0.5, -0.5
        )
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        # Background — solid pure black (matches the rest of the
        # app's BG_BASE / BG_CARD).
        p.fillPath(path, QColor("#0a0a0a"))
        # Rim — faint white at very low alpha for the glass-edge
        # look the rest of the app uses.
        p.setPen(QPen(QColor(255, 255, 255, 32), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()

    # ── Convenience constructor ────────────────────────────────

    @classmethod
    def confirm(
        cls,
        parent: Optional[QWidget],
        *,
        title: str,
        body: str,
        accept_label: str,
        cancel_label: str,
        destructive: bool = False,
    ) -> bool:
        """Modal yes/no. Returns True iff the user clicked accept."""
        dlg = cls(
            parent,
            title=title,
            body=body,
            accept_label=accept_label,
            cancel_label=cancel_label,
            destructive=destructive,
        )
        # Center over parent; QDialog's default position is screen-
        # centered which is fine when parent is None.
        if parent is not None:
            geo = parent.frameGeometry()
            dlg.adjustSize()
            cx = geo.center().x() - dlg.width() // 2
            cy = geo.center().y() - dlg.height() // 2
            dlg.move(QPointF(cx, cy).toPoint())
        return dlg.exec() == QDialog.DialogCode.Accepted
