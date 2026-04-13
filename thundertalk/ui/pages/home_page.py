"""Home page — transcription history and usage stats."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.history import HistoryStore


class _StatCard(QFrame):
    """Painted stat card with a colored top accent line."""

    def __init__(self, value: str, label: str, accent: str) -> None:
        super().__init__()
        self._accent = accent
        self.setFixedHeight(90)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 14px; }}"
        )
        ly = QVBoxLayout(self)
        ly.setContentsMargins(18, 20, 18, 14)
        ly.setSpacing(2)

        self._val = QLabel(value)
        self._val.setFont(theme.font_heading(22))
        self._val.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        ly.addWidget(self._val)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none;")
        ly.addWidget(lbl)

    def set_value(self, v: str) -> None:
        self._val.setText(v)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(14, 0, 60, 0)
        grad.setColorAt(0, QColor(self._accent))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        path = QPainterPath()
        path.addRoundedRect(QRectF(10, 1, 50, 3), 1.5, 1.5)
        p.drawPath(path)
        p.end()


class HomePage(QWidget):
    def __init__(self, history: HistoryStore) -> None:
        super().__init__()
        self._history = history

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 20)
        root.setSpacing(20)

        # ── Header ──
        header_row = QHBoxLayout()
        heading = QLabel("Home")
        heading.setFont(theme.font_heading(20))
        heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        header_row.addWidget(heading)
        header_row.addStretch()
        root.addLayout(header_row)

        # ── Stats ──
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self._stat_sessions = _StatCard("0", "Sessions", theme.ACCENT_BLUE)
        self._stat_duration = _StatCard("0 min", "Total Time", theme.ACCENT_ORANGE)
        self._stat_chars = _StatCard("0", "Characters", theme.ACCENT_PURPLE)
        stats_row.addWidget(self._stat_sessions)
        stats_row.addWidget(self._stat_duration)
        stats_row.addWidget(self._stat_chars)
        root.addLayout(stats_row)

        # ── History header ──
        hist_header = QHBoxLayout()
        hist_label = QLabel("Recent Transcriptions")
        hist_label.setFont(theme.font(14, bold=True))
        hist_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        hist_header.addWidget(hist_label)
        hist_header.addStretch()

        self._clear_btn = theme.pill_button("Clear All", width=80, height=30)
        self._clear_btn.clicked.connect(self._on_clear)
        hist_header.addWidget(self._clear_btn)
        root.addLayout(hist_header)

        # ── History list ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(8)
        self._history_layout.addStretch()
        scroll.setWidget(self._history_container)

        self.refresh()

    def refresh(self) -> None:
        dur = self._history.total_duration_secs
        mins = int(dur // 60)
        self._stat_sessions.set_value(str(self._history.session_count))
        self._stat_duration.set_value(
            f"{mins} min" if mins < 60 else f"{mins // 60}h {mins % 60}m"
        )
        self._stat_chars.set_value(str(self._history.total_characters))

        while self._history_layout.count() > 1:
            item = self._history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in self._history.entries[:200]:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background: {theme.BG_CARD};"
                f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; }}"
                f"QFrame:hover {{ background: {theme.BG_CARD_HOVER};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; }}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(5)

            top_row = QHBoxLayout()
            ts = datetime.datetime.fromtimestamp(entry.timestamp)
            time_label = QLabel(ts.strftime("%b %d, %Y   %H:%M"))
            time_label.setStyleSheet(f"color: {theme.ACCENT_ORANGE}; font-size: 11px; border: none;")
            top_row.addWidget(time_label)
            top_row.addStretch()
            ms_label = QLabel(f"{entry.inference_ms}ms")
            ms_label.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: 10px;"
                f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE};"
                " border-radius: 8px; padding: 2px 8px;"
            )
            top_row.addWidget(ms_label)
            cl.addLayout(top_row)

            text_label = QLabel(entry.text)
            text_label.setWordWrap(True)
            text_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; border: none;")
            cl.addWidget(text_label)

            meta = QLabel(f"{entry.model}  ·  {entry.duration_secs:.1f}s audio")
            meta.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
            cl.addWidget(meta)

            self._history_layout.insertWidget(self._history_layout.count() - 1, card)

        if self._history.session_count == 0:
            empty_w = QWidget()
            ely = QVBoxLayout(empty_w)
            ely.setContentsMargins(0, 40, 0, 40)
            ely.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon = QLabel("🎙")
            icon.setFont(QFont("Helvetica Neue", 32))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet("color: #666;")
            ely.addWidget(icon)

            msg = QLabel("No transcriptions yet")
            msg.setFont(theme.font(14, bold=True))
            msg.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(msg)

            sub = QLabel("Press your hotkey to start recording")
            sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(sub)

            self._history_layout.insertWidget(0, empty_w)

    def _on_clear(self) -> None:
        self._history.clear()
        self.refresh()
