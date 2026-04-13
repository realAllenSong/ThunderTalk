"""About page — version, links, update check."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import thundertalk
from thundertalk.ui import theme


class _LogoWidget(QWidget):
    """Painted gradient logo icon."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(88, 88)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, 88, 88)
        grad = QLinearGradient(0, 0, 88, 88)
        grad.setColorAt(0, QColor("#f97316"))
        grad.setColorAt(1, QColor("#ea580c"))
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        p.fillPath(path, grad)

        p.setPen(QColor("#ffffff"))
        f = QFont("Helvetica Neue", 44)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(rect.adjusted(0, -2, 0, 0), Qt.AlignmentFlag.AlignCenter, "⚡")
        p.end()


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        ly = QVBoxLayout(self)
        ly.setContentsMargins(28, 48, 28, 28)
        ly.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        logo = _LogoWidget()
        ly.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)
        ly.addSpacing(16)

        title = QLabel("ThunderTalk")
        title.setFont(theme.font_heading(24))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(title)
        ly.addSpacing(4)

        version = QLabel(f"v{thundertalk.__version__}")
        version.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
            f" background: {theme.BG_CARD}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 12px; padding: 4px 16px;"
        )
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setFixedWidth(90)
        ly.addWidget(version, alignment=Qt.AlignmentFlag.AlignCenter)
        ly.addSpacing(8)

        tagline = QLabel("Lightning-fast, privacy-first voice-to-text")
        tagline.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(tagline)
        ly.addSpacing(32)

        def _link_btn(text: str, url: str, icon: str = "") -> QPushButton:
            btn = QPushButton(f"{icon}  {text}" if icon else text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(260)
            btn.setFixedHeight(40)
            btn.setStyleSheet(
                f"QPushButton {{ background: {theme.BG_CARD}; color: {theme.TEXT_SECONDARY};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px;"
                " font-size: 13px; padding: 0 16px; }}"
                f"QPushButton:hover {{ background: {theme.BG_CARD_HOVER};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; color: {theme.TEXT_PRIMARY}; }}"
            )
            btn.clicked.connect(lambda: webbrowser.open(url))
            return btn

        ly.addWidget(
            _link_btn("GitHub Repository", "https://github.com/songallen/ThunderTalk", "→"),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        ly.addSpacing(6)
        ly.addWidget(
            _link_btn("Report an Issue", "https://github.com/songallen/ThunderTalk/issues", "→"),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        ly.addSpacing(6)
        ly.addWidget(
            _link_btn("License (Apache-2.0)", "https://github.com/songallen/ThunderTalk/blob/main/LICENSE", "→"),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        ly.addStretch()

        footer = QLabel("Made with care for the open-source community")
        footer.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(footer)
