"""Home page — hotkey info bar, stats, and chronological history.

Layout inspired by 闪电说: compact info bar at top showing current hotkey
and stats, then chronological history cards grouped by day.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
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


class _InfoBar(QFrame):
    """Compact top bar showing hotkey + session stats (like 闪电说)."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; }}"
        )
        self.setGraphicsEffect(theme.auto_shadow())
        self.setFixedHeight(52)

        ly = QHBoxLayout(self)
        ly.setContentsMargins(20, 0, 20, 0)
        ly.setSpacing(0)

        # Hotkey display
        self._hotkey_label = QLabel("⌃R")
        self._hotkey_label.setFont(theme.font(13, bold=True))
        self._hotkey_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; border: none;"
            f" background: {theme.BG_ELEVATED}; border-radius: 6px;"
            " padding: 4px 12px;"
        )
        ly.addWidget(self._hotkey_label)

        ly.addSpacing(16)

        # Separator
        sep = QFrame()
        sep.setFixedSize(1, 24)
        sep.setStyleSheet(f"background: {theme.BORDER_SUBTLE};")
        ly.addWidget(sep)

        ly.addStretch()

        # Stats
        self._stat_time = QLabel("0 min")
        self._stat_time.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;")
        ly.addWidget(self._stat_time)

        ly.addSpacing(20)

        self._stat_chars = QLabel("0 chars")
        self._stat_chars.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;")
        ly.addWidget(self._stat_chars)

        ly.addSpacing(20)

        self._stat_speed = QLabel("0 sessions")
        self._stat_speed.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;")
        ly.addWidget(self._stat_speed)

    def update_stats(self, sessions: int, mins: int, chars: int) -> None:
        time_str = f"{mins} min" if mins < 60 else f"{mins // 60}h {mins % 60}m"
        self._stat_time.setText(f"{time_str}")
        self._stat_chars.setText(f"{chars:,} chars")
        self._stat_speed.setText(f"{sessions} sessions")


class _HistoryCard(QFrame):
    """Single transcription card — time on top-left, text below."""

    def __init__(self, entry) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 12px; }}"
            f"QFrame:hover {{ border: 1px solid {theme.BORDER_DEFAULT}; }}"
        )
        self.setGraphicsEffect(theme.auto_shadow())

        ly = QVBoxLayout(self)
        ly.setContentsMargins(20, 16, 20, 16)
        ly.setSpacing(8)

        # Time
        ts = datetime.datetime.fromtimestamp(entry.timestamp)
        time_lbl = QLabel(ts.strftime("%H:%M"))
        time_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none;")
        ly.addWidget(time_lbl)

        # Text
        text_lbl = QLabel(entry.text)
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; border: none;")
        ly.addWidget(text_lbl)


class _DayHeader(QLabel):
    """Day separator like '昨天' / 'Today' / 'Apr 12, 2026'."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFont(theme.font(12, bold=True))
        self.setStyleSheet(f"color: {theme.TEXT_MUTED}; padding-top: 8px;")


class HomePage(QWidget):
    def __init__(self, history: HistoryStore) -> None:
        super().__init__()
        self._history = history

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(16)

        # ── Info Bar ──
        self._info_bar = _InfoBar()
        root.addWidget(self._info_bar)

        # ── Filter row + Clear ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        all_tab = QLabel("All")
        all_tab.setFont(theme.font(12, bold=True))
        all_tab.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: {theme.BG_ELEVATED};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 6px;"
            " padding: 4px 14px;"
        )
        filter_row.addWidget(all_tab)
        filter_row.addStretch()

        self._clear_btn = theme.pill_button("Clear", width=80, height=30)
        self._clear_btn.clicked.connect(self._on_clear)
        filter_row.addWidget(self._clear_btn)
        root.addLayout(filter_row)

        # ── History list ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(10)
        self._history_layout.addStretch()
        scroll.setWidget(self._history_container)

        self.refresh()

    def refresh(self) -> None:
        dur = self._history.total_duration_secs
        mins = int(dur // 60)
        self._info_bar.update_stats(
            self._history.session_count,
            mins,
            self._history.total_characters,
        )

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
        if self._history.session_count == 0:
            empty_w = QWidget()
            ely = QVBoxLayout(empty_w)
            ely.setContentsMargins(0, 80, 0, 80)
            ely.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon = QLabel("🎙")
            icon.setFont(QFont("Helvetica Neue", 36))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(icon)
            ely.addSpacing(12)

            msg = QLabel("No transcriptions yet")
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
