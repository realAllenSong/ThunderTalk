"""About page — version, tagline, links.

Matches 闪电说 style: centered logo, version badge, tagline,
and footer links.
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import thundertalk
from thundertalk.ui import theme


class _LogoWidget(QWidget):
    """Large rounded-square icon matching 闪电说 about page."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(80, 80)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, 80, 80)
        grad = QLinearGradient(0, 0, 80, 80)
        grad.setColorAt(0, QColor("#fbbf24"))
        grad.setColorAt(1, QColor("#f97316"))
        path = QPainterPath()
        path.addRoundedRect(rect, 20, 20)
        p.fillPath(path, grad)

        # White bolt
        theme.draw_boltPath(p, rect)
        p.end()


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        ly = QVBoxLayout(self)
        ly.setContentsMargins(32, 28, 32, 32)

        # ── Segment tab bar (to match settings page) ──
        from PySide6.QtWidgets import QTabBar
        tab_container = QHBoxLayout()
        tab_container.addStretch()
        tabs = QTabBar()
        tabs.setExpanding(False)
        tabs.setDrawBase(False)
        tabs.setStyleSheet(theme.segment_tab_qss())
        for name in ("Hotkey", "Microphone", "System", "Hotwords", "About"):
            tabs.addTab(name)
        tabs.setCurrentIndex(4)
        # These tabs are display-only since About is a separate page
        tabs.setEnabled(False)
        tabs.setStyleSheet(
            tabs.styleSheet()
            + f"QTabBar::tab:disabled {{ color: {theme.TEXT_SECONDARY}; }}"
            + f"QTabBar::tab:selected {{ color: {theme.TEXT_PRIMARY};"
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE}; }}"
        )
        tab_container.addWidget(tabs)
        tab_container.addStretch()
        # Don't show fake tabs — keep the page clean
        # ly.addLayout(tab_container)

        ly.addStretch()

        # ── Logo ──
        logo = _LogoWidget()
        ly.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)
        ly.addSpacing(16)

        # ── Title ──
        title = QLabel("ThunderTalk")
        title.setFont(theme.font_heading(24))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(title)
        ly.addSpacing(10)

        # ── Version + Check update (side by side like 闪电说) ──
        ver_row = QHBoxLayout()
        ver_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver_row.setSpacing(4)

        version = QLabel(f"v{thundertalk.__version__}")
        version.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;"
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 12px; padding: 5px 16px;"
        )
        ver_row.addWidget(version)

        check_btn = QPushButton("Check for Updates")
        check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        check_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_SECONDARY}; font-size: 12px;"
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 12px; padding: 5px 16px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; }}"
        )
        check_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/realAllenSong/ThunderTalk/releases")
        )
        ver_row.addWidget(check_btn)
        ly.addLayout(ver_row)

        ly.addSpacing(24)

        # ── Tagline ──
        tagline = QLabel("«  Lightning-fast, privacy-first voice-to-text  »")
        tagline.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 14px; font-style: italic;")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(tagline)

        ly.addStretch()

        # ── Footer links (inline like 闪电说) ──
        footer_links = QHBoxLayout()
        footer_links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_links.setSpacing(0)

        def _link(text: str, url: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ color: {theme.TEXT_MUTED}; background: transparent;"
                " border: none; font-size: 12px; padding: 4px 12px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
            )
            btn.clicked.connect(lambda: webbrowser.open(url))
            return btn

        def _divider() -> QLabel:
            d = QLabel("|")
            d.setStyleSheet(f"color: {theme.BORDER_DEFAULT}; font-size: 12px; padding: 0 4px;")
            return d

        footer_links.addWidget(_link("Website", "https://github.com/realAllenSong/ThunderTalk"))
        footer_links.addWidget(_divider())
        footer_links.addWidget(_link("Report Issue", "https://github.com/realAllenSong/ThunderTalk/issues"))
        footer_links.addWidget(_divider())
        footer_links.addWidget(_link("License", "https://github.com/realAllenSong/ThunderTalk/blob/main/LICENSE"))
        ly.addLayout(footer_links)

        ly.addSpacing(8)

        # ── Copyright ──
        copyright = QLabel("© 2026 Allen Song · Open Source · Apache-2.0")
        copyright.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        copyright.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ly.addWidget(copyright)
