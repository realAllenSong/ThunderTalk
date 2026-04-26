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

from thundertalk.core.i18n import t
from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.history import HistoryStore


def _draw_stat_icon_clock(p: QPainter, cx: float, cy: float, color: QColor) -> None:
    p.setPen(QPen(color, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
    p.drawLine(int(cx), int(cy), int(cx), int(cy - 5))
    p.drawLine(int(cx), int(cy), int(cx + 4), int(cy + 1))


def _draw_stat_icon_text(p: QPainter, cx: float, cy: float, color: QColor) -> None:
    p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i, w in enumerate((12, 10, 8)):
        y = int(cy - 6 + i * 6)
        p.drawLine(int(cx - 7), y, int(cx - 7 + w), y)


def _draw_stat_icon_bolt(p: QPainter, cx: float, cy: float, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(cx + 1.5, cy - 9)
    path.lineTo(cx - 5, cy + 1)
    path.lineTo(cx - 1, cy + 1)
    path.lineTo(cx - 2, cy + 9)
    path.lineTo(cx + 5, cy - 1)
    path.lineTo(cx + 1, cy - 1)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _draw_stat_icon_bars(p: QPainter, cx: float, cy: float, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    for i, h in enumerate((6, 11, 8, 14)):
        x = cx - 8 + i * 4.5
        p.drawRoundedRect(QRectF(x, cy + 6 - h, 3, h), 1.2, 1.2)


_STAT_ICON_PAINTERS = {
    "clock": _draw_stat_icon_clock,
    "text": _draw_stat_icon_text,
    "bolt": _draw_stat_icon_bolt,
    "bars": _draw_stat_icon_bars,
}


class _StatIcon(QWidget):
    def __init__(self, kind: str, color: str) -> None:
        super().__init__()
        self._kind = kind
        self._color = QColor(color)
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Soft tinted disc behind the glyph for a premium, badge-y feel
        bg = QColor(self._color)
        bg.setAlpha(38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(QRectF(0, 0, 36, 36))
        _STAT_ICON_PAINTERS[self._kind](p, 18, 18, self._color)
        p.end()


class _StatCard(QFrame):
    """A single stat card with icon, large value, and label."""

    def set_label(self, label: str) -> None:
        self._label.setText(label.upper())

    def __init__(self, icon_kind: str, value: str, label: str,
                 accent: str = theme.ACCENT_ORANGE) -> None:
        super().__init__()
        self._accent = accent
        self.setObjectName("statCard")
        self.setStyleSheet(
            f"QFrame#statCard {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 16px; }}"
        )
        self.setFixedHeight(150)

        ly = QVBoxLayout(self)
        ly.setContentsMargins(22, 20, 22, 20)
        ly.setSpacing(4)

        # Icon
        icon = _StatIcon(icon_kind, accent)
        ly.addWidget(icon)

        ly.addStretch()

        # Value
        self._value = QLabel(value)
        self._value.setFont(QFont("Helvetica Neue", 32, QFont.Weight.Bold))
        self._value.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; border: none;"
            " letter-spacing: -0.5px;"
        )
        ly.addWidget(self._value)

        # Label
        self._label = QLabel(label.upper())
        self._label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 10px; border: none;"
            " letter-spacing: 1.2px; font-weight: 600;"
        )
        ly.addWidget(self._label)

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Soft accent glow in the top-right corner — premium achievement feel
        w = self.width()
        glow = QLinearGradient(w, 0, w - 120, 120)
        c0 = QColor(self._accent)
        c0.setAlpha(45)
        c1 = QColor(self._accent)
        c1.setAlpha(0)
        glow.setColorAt(0, c0)
        glow.setColorAt(1, c1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 15, 15)
        p.drawPath(path)
        p.end()


class _HistoryCard(QFrame):
    """Single transcription card — time on top-left, copy button, text below."""

    def __init__(self, entry) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_CARD};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: 12px; }}"
            f"QFrame:hover {{ border: 1px solid {theme.BORDER_STRONG}; }}"
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
            f"color: {theme.TEXT_MUTED}; font-size: 10px;"
            f" background: transparent; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 4px; padding: 2px 6px;"
        )
        top.addWidget(dur_lbl)

        from PySide6.QtWidgets import QPushButton
        from PySide6.QtWidgets import QApplication

        def _make_copy_btn(payload_text: str, label: str = None) -> QPushButton:
            btn = QPushButton(label or t("home.copy"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(22)
            btn.setStyleSheet(
                f"QPushButton {{ color: {theme.TEXT_MUTED}; font-size: 10px;"
                f" background: transparent; border: 1px solid {theme.BORDER_SUBTLE};"
                " border-radius: 4px; padding: 2px 10px; }}"
                f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY};"
                f" border: 1px solid {theme.BORDER_DEFAULT}; }}"
            )
            original_label = btn.text()

            def _copy(_checked=False, _t=payload_text, b=btn, _orig=original_label):
                QApplication.clipboard().setText(_t)
                b.setText(t("home.copied"))
                QTimer.singleShot(1500, lambda: b.setText(_orig))

            btn.clicked.connect(_copy)
            return btn

        copy_btn = _make_copy_btn(entry.text)
        top.addSpacing(8)
        top.addWidget(copy_btn)

        ly.addLayout(top)

        text_lbl = QLabel(entry.text)
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; border: none;")
        ly.addWidget(text_lbl)

        # If a translation was attached (Review-mode entry), render it in
        # a smaller, accent-colored row with its own Copy button.
        if entry.translation:
            tr_row = QWidget()
            tr_row.setStyleSheet("background: transparent;")
            tr_ly = QHBoxLayout(tr_row)
            tr_ly.setContentsMargins(0, 4, 0, 0)
            tr_ly.setSpacing(8)

            arrow = QLabel(f"→ {entry.translation_lang or ''}".strip())
            arrow.setStyleSheet(
                f"color: {theme.ACCENT_ORANGE}; font-size: 10px;"
                " font-weight: 600; border: none; background: transparent;"
            )
            tr_ly.addWidget(arrow, alignment=Qt.AlignmentFlag.AlignTop)

            tr_text = QLabel(entry.translation)
            tr_text.setWordWrap(True)
            tr_text.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; font-size: 12px;"
                " border: none; background: transparent;"
            )
            tr_ly.addWidget(tr_text, stretch=1)

            tr_copy_btn = _make_copy_btn(
                entry.translation, label=t("home.copy_translation")
            )
            tr_ly.addWidget(tr_copy_btn, alignment=Qt.AlignmentFlag.AlignTop)
            ly.addWidget(tr_row)


class _DayHeader(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFont(theme.font(11, bold=True))
        self.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; background: transparent;"
            " padding: 10px 0 4px 2px; letter-spacing: 0.5px;"
        )


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

        self._stat_time = _StatCard("clock", "0m", t("home.speaking_time"), theme.ACCENT_ORANGE)
        self._stat_chars = _StatCard("text", "0", t("home.characters"), theme.ACCENT_BLUE)
        self._stat_sessions = _StatCard("bolt", "0", t("home.sessions"), theme.ACCENT_ORANGE_WARM)

        stats_row.addWidget(self._stat_time)
        stats_row.addWidget(self._stat_chars)
        stats_row.addWidget(self._stat_sessions)
        root.addLayout(stats_row)

        root.addSpacing(20)

        # ── History header ──
        hist_header = QHBoxLayout()
        hist_header.setSpacing(8)

        self._recent_title = QLabel(t("home.recent"))
        self._recent_title.setFont(theme.font(14, bold=True))
        self._recent_title.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        hist_header.addWidget(self._recent_title)

        hist_header.addStretch()

        self._clear_btn = theme.pill_button(t("home.clear"), width=72, height=28)
        self._clear_btn.clicked.connect(self._on_clear)
        hist_header.addWidget(self._clear_btn)
        root.addLayout(hist_header)

        root.addSpacing(12)

        # ── History list ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll, stretch=1)

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

        self._stat_time.set_value(time_str)
        self._stat_chars.set_value(f"{total_chars:,}")
        self._stat_sessions.set_value(str(sessions))

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
                    day_text = t("home.today")
                elif entry_day == yesterday:
                    day_text = t("home.yesterday")
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

            msg = QLabel(t("home.ready"))
            msg.setFont(theme.font(16, bold=True))
            msg.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(msg)

            sub = QLabel(t("home.ready_sub"))
            sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ely.addWidget(sub)

            self._history_layout.insertWidget(0, empty_w)

    def _on_clear(self) -> None:
        # Two-step confirm — Clear sits one click away from the user's
        # entire transcription history and there's no undo. Default
        # button is Cancel so an accidental Enter / Return doesn't
        # silently wipe.
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("home.clear.confirm_title"))
        box.setText(t("home.clear.confirm_title"))
        box.setInformativeText(t("home.clear.confirm_body"))
        clear_btn = box.addButton(
            t("home.clear.confirm_yes"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_btn = box.addButton(
            t("home.clear.confirm_cancel"), QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not clear_btn:
            return
        self._history.clear()
        self.refresh()

    def retranslate(self) -> None:
        self._stat_time.set_label(t("home.speaking_time"))
        self._stat_chars.set_label(t("home.characters"))
        self._stat_sessions.set_label(t("home.sessions"))
        self._recent_title.setText(t("home.recent"))
        self._clear_btn.setText(t("home.clear"))
        self.refresh()


class _EmptyBolt(QWidget):
    """Animated lightning bolt for empty state."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(64, 64)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._timer.isActive():
            self._timer.start(80)

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()

    def _tick(self) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
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
