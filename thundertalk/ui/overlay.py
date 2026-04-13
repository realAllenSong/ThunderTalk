"""Floating voice input capsule — polished pill overlay for recording state."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from thundertalk.ui import theme


class VoiceOverlay(QWidget):
    """Frameless, translucent, always-on-top capsule shown during recording."""

    _IDLE, _RECORDING, _TRANSCRIBING, _RESULT, _ERROR = range(5)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFixedSize(340, 60)

        self._state = self._IDLE
        self._text = ""
        self._phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

    def show_recording(self) -> None:
        self._state = self._RECORDING
        self._text = "Listening…"
        self._phase = 0.0
        self._center_on_screen()
        self.show()
        self._anim_timer.start(30)

    def show_transcribing(self) -> None:
        self._state = self._TRANSCRIBING
        self._text = "Transcribing…"
        self.update()

    def show_result(self, text: str) -> None:
        self._state = self._RESULT
        self._text = text[:55] + ("…" if len(text) > 55 else "")
        self._anim_timer.stop()
        self.update()
        QTimer.singleShot(1500, self.hide_overlay)

    def show_error(self, msg: str) -> None:
        self._state = self._ERROR
        self._text = msg[:50]
        self._anim_timer.stop()
        self.update()
        QTimer.singleShot(2500, self.hide_overlay)

    def hide_overlay(self) -> None:
        self._anim_timer.stop()
        self._state = self._IDLE
        self.hide()

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + 80,
            )

    def _tick(self) -> None:
        self._phase += 0.10
        self.update()

    def paintEvent(self, event) -> None:
        if self._state == self._IDLE:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        if self._state == self._ERROR:
            bg = QColor(40, 15, 15, 240)
        else:
            bg = QColor(18, 18, 18, 240)
        p.fillPath(pill, bg)

        p.setPen(QPen(QColor(255, 255, 255, 15), 1))
        p.drawPath(pill)

        if self._state == self._RECORDING:
            self._draw_recording(p, w, h)
        elif self._state == self._TRANSCRIBING:
            self._draw_transcribing(p, w, h)
        else:
            self._draw_text(p, w, h)

        p.end()

    def _draw_recording(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT_BLUE))
        pulse = 4.5 + 1.5 * math.sin(self._phase * 2)
        p.drawEllipse(QRectF(20 - pulse / 2, h / 2 - pulse / 2, 9 + pulse, 9 + pulse))

        f = QFont("Helvetica Neue", 13)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(40, int(h / 2 + 5), self._text)

        bar_x, bar_w = 160, 150
        n_bars = 24
        for i in range(n_bars):
            x = bar_x + i * (bar_w / n_bars)
            amp = 0.3 + 0.7 * abs(math.sin(self._phase * 1.2 + i * 0.45))
            bh = int(22 * amp)
            y1 = int(h / 2 - bh / 2)

            c = QColor(theme.ACCENT_BLUE)
            c.setAlpha(int(120 + 100 * amp))
            p.setPen(QPen(c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x), y1, int(x), y1 + bh)

    def _draw_transcribing(self, p: QPainter, w: int, h: int) -> None:
        f = QFont("Helvetica Neue", 13)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(theme.ACCENT_BLUE))
        p.drawText(24, int(h / 2 + 5), self._text)

        dots = 3
        for i in range(dots):
            offset = math.sin(self._phase * 2 + i * 0.8) * 4
            cx = 170 + i * 14
            cy = int(h / 2 + offset)
            alpha = int(120 + 120 * (0.5 + 0.5 * math.sin(self._phase * 2 + i * 0.8)))
            c = QColor(theme.ACCENT_BLUE)
            c.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))

    def _draw_text(self, p: QPainter, w: int, h: int) -> None:
        f = QFont("Helvetica Neue", 12)
        p.setFont(f)
        color = QColor(theme.ERROR) if self._state == self._ERROR else QColor(theme.TEXT_PRIMARY)
        p.setPen(color)
        p.drawText(24, int(h / 2 + 5), self._text)
