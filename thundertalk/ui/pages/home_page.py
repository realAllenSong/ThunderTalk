"""Home page — hero stats dashboard + chronological history.

Inspired by 闪电说's home page: prominent stats that give users a sense
of achievement, plus clean history cards grouped by day.
"""

from __future__ import annotations

import datetime
import math
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.history import HistoryStore


class _StatCard(QFrame):
    """A single stat card with icon, large value, and label."""

    def __init__(self, icon_text: str, value: str, label: str,
                 accent: str = theme.ACCENT_ORANGE) -> None:
        super().__init__()
        self._accent = accent
        self.setObjectName("statCard")
        self.setStyleSheet(
            f"QFrame#statCard {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 14px; }}"
        )
        self.setGraphicsEffect(theme.auto_shadow())
        self.setMinimumHeight(110)

        ly = QVBoxLayout(self)
        ly.setContentsMargins(20, 16, 20, 16)
        ly.setSpacing(6)

        # Icon
        icon = QLabel(icon_text)
        icon.setStyleSheet(
            f"color: {accent}; font-size: 18px; border: none; background: transparent;"
        )
        ly.addWidget(icon)

        ly.addStretch()

        # Value
        self._value = QLabel(value)
        self._value.setFont(QFont("Helvetica Neue", 26, QFont.Weight.Bold))
        self._value.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        ly.addWidget(self._value)

        # Label
        self._label = QLabel(label)
        self._label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
        ly.addWidget(self._label)

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(1, 10, 1, 55)
        grad.setColorAt(0, QColor(self._accent))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 8, 3, 45), 1.5, 1.5)
        p.drawPath(path)
        p.end()


class _HistoryCard(QFrame):
    """Single transcription card — time on top-left, copy button, text below."""

    def __init__(self, entry) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; }}"
            f"QFrame:hover {{ border: 1px solid {theme.BORDER_DEFAULT}; }}"
        )

        ly = QVBoxLayout(self)
        ly.setContentsMargins(20, 14, 20, 14)
        ly.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(0)

        ts = datetime.datetime.fromtimestamp(entry.timestamp)
        time_lbl = QLabel(ts.strftime("%H:%M"))
        time_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;")
        top.addWidget(time_lbl)

        top.addStretch()

        dur = entry.duration_secs
        if dur >= 60:
            dur_text = f"{int(dur // 60)}m {int(dur % 60)}s"
        else:
            dur_text = f"{dur:.1f}s"
        dur_lbl = QLabel(dur_text)
        dur_lbl.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; border: none;"
            f" background: {theme.BG_ELEVATED}; border-radius: 4px; padding: 2px 6px;"
        )
        top.addWidget(dur_lbl)

        from PySide6.QtWidgets import QPushButton
        from PySide6.QtWidgets import QApplication
        copy_btn = QPushButton("Copy")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setFixedHeight(22)
        copy_btn.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_MUTED}; font-size: 10px; border: none;"
            f" background: {theme.BG_ELEVATED}; border-radius: 4px; padding: 2px 10px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
            f" background: {theme.BG_CARD_HOVER}; }}"
        )
        _text = entry.text
        def _copy(checked=False, t=_text, b=copy_btn):
            QApplication.clipboard().setText(t)
            b.setText("Copied!")
            QTimer.singleShot(1500, lambda: b.setText("Copy"))
        copy_btn.clicked.connect(_copy)
        top.addSpacing(8)
        top.addWidget(copy_btn)

        ly.addLayout(top)

        text_lbl = QLabel(entry.text)
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; border: none;")
        ly.addWidget(text_lbl)


class _DayHeader(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFont(theme.font(12, bold=True))
        self.setStyleSheet(f"color: {theme.TEXT_MUTED}; padding-top: 4px;")


class HomePage(QWidget):
    def __init__(self, history: HistoryStore) -> None:
        super().__init__()
        self._history = history

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(0)

        # ── Hero Stats ──
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)

        self._stat_time = _StatCard("🕐", "0m", "Speaking Time", theme.ACCENT_ORANGE)
        self._stat_chars = _StatCard("📝", "0", "Characters", theme.ACCENT_BLUE)
        self._stat_sessions = _StatCard("⚡", "0", "Sessions", theme.ACCENT_ORANGE_WARM)
        self._stat_speed = _StatCard("📊", "0", "Chars / Min", theme.ACCENT_CYAN)

        stats_row.addWidget(self._stat_time)
        stats_row.addWidget(self._stat_chars)
        stats_row.addWidget(self._stat_sessions)
        stats_row.addWidget(self._stat_speed)
        root.addLayout(stats_row)

        root.addSpacing(20)

        # ── History header ──
        hist_header = QHBoxLayout()
        hist_header.setSpacing(8)

        title = QLabel("Recent")
        title.setFont(theme.font(14, bold=True))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        hist_header.addWidget(title)

        hist_header.addStretch()

        self._clear_btn = theme.pill_button("Clear", width=72, height=28)
        self._clear_btn.clicked.connect(self._on_clear)
        hist_header.addWidget(self._clear_btn)
        root.addLayout(hist_header)

        root.addSpacing(12)

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
        total_dur = self._history.total_duration_secs
        total_chars = self._history.total_characters
        sessions = self._history.session_count

        # Format speaking time
        mins = int(total_dur // 60)
        if mins >= 60:
            h, m = divmod(mins, 60)
            time_str = f"{h}h {m}m"
        else:
            time_str = f"{mins}m"

        # Chars per minute
        cpm = int(total_chars / (total_dur / 60)) if total_dur > 30 else 0

        self._stat_time.set_value(time_str)
        self._stat_chars.set_value(f"{total_chars:,}")
        self._stat_sessions.set_value(str(sessions))
        self._stat_speed.set_value(str(cpm))

        # Clear existing cards
        while self._history_layout.count() > 1:
            item = self._history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Group by day
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        current_day = None

        for entry in self._history.entries[:200]:
            ts = datetime.datetime.fromtimestamp(entry.timestamp)
            entry_day = ts.date()

            if entry_day != current_day:
                current_day = entry_day
                if entry_day == today:
                    day_text = "Today"
                elif entry_day == yesterday:
                    day_text = "Yesterday"
                else:
                    day_text = ts.strftime("%b %d, %Y")
                header = _DayHeader(day_text)
                self._history_layout.insertWidget(
                    self._history_layout.count() - 1, header
                )

            card = _HistoryCard(entry)
            self._history_layout.insertWidget(
                self._history_layout.count() - 1, card
            )

        # Empty state
        if sessions == 0:
            empty_w = QWidget()
            ely = QVBoxLayout(empty_w)
            ely.setContentsMargins(0, 60, 0, 60)
            ely.setAlignment(Qt.AlignmentFlag.AlignCenter)

            bolt = _EmptyBolt()
            ely.addWidget(bolt, alignment=Qt.AlignmentFlag.AlignCenter)
            ely.addSpacing(16)

            msg = QLabel("Ready to go")
            msg.setFont(theme.font(16, bold=True))
            msg.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(msg)

            sub = QLabel("Press your hotkey to start recording")
            sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(sub)

            self._history_layout.insertWidget(0, empty_w)

    def _on_clear(self) -> None:
        self._history.clear()
        self.refresh()


class _EmptyBolt(QWidget):
    """Animated lightning bolt for empty state."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(64, 64)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self) -> None:
        self._phase += 0.06
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        alpha = int(180 + 60 * math.sin(self._phase * 2))
        rect = QRectF(0, 0, 64, 64)
        grad = QLinearGradient(0, 0, 64, 64)
        grad.setColorAt(0, QColor(251, 191, 36, alpha))
        grad.setColorAt(1, QColor(249, 115, 22, alpha))
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        p.fillPath(path, grad)

        theme.draw_boltPath(p, rect)
        p.end()
