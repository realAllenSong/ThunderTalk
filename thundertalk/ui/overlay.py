"""Floating dictation indicator — the only UI most people see while using
the app.

A flat ink bar at the top of the screen. Recording: an orange dot, "Listening",
a live level meter, a seconds counter and the hotkey. Transcribing: plain text
with cycling dots. Then a one-line result or error. It appears and disappears
without animation; the only movement is the meter, which is real input level.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from thundertalk.core.i18n import t
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.keys import display_combo

# The widget is a little larger than the bar so a faint edge shadow fits.
_MX, _MY = 12, 10
_PW, _PH = 420, 52
_W, _H = _PW + 2 * _MX, _PH + 2 * _MY + 4
_BARS = 34

_INK = QColor(theme.INK)
_PAPER = QColor("#FBFBFA")
_DIM = QColor(251, 251, 250, 150)


class VoiceOverlay(QWidget):

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
        self.setFixedSize(_W, _H)

        self._state = self._IDLE
        self._text = ""
        self._audio_rms = 0.0
        self._smooth = 0.0
        self._levels: deque[float] = deque([0.0] * _BARS, maxlen=_BARS)
        self._rec_t0 = 0.0
        self._tick_n = 0
        self._hotkey = ""

        # Slow tick: advances the seconds counter and the "…" dots. (The level
        # meter repaints itself whenever a new sample arrives.)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

        # One cancellable timer — a stale singleShot used to hide the *next*
        # recording's overlay if the user re-triggered within the delay.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_overlay)

    # ── public API ──────────────────────────────────────────────────────

    def set_hotkey(self, combo: str) -> None:
        self._hotkey = combo
        self.update()

    def show_recording(self) -> None:
        self._hide_timer.stop()
        self._state = self._RECORDING
        self._text = t("overlay.listening").rstrip("…").rstrip(".")
        self._smooth = 0.0
        self._audio_rms = 0.0
        self._levels = deque([0.0] * _BARS, maxlen=_BARS)
        self._rec_t0 = time.monotonic()
        self._present()
        self._anim.start(250)

    def set_audio_level(self, rms: float) -> None:
        self._audio_rms = rms
        if self._state == self._RECORDING:
            target = min(1.0, rms * 10.0)
            k = 0.55 if target > self._smooth else 0.25
            self._smooth += (target - self._smooth) * k
            self._levels.append(self._smooth)
            self.update()

    def show_transcribing(self) -> None:
        self._hide_timer.stop()
        self._state = self._TRANSCRIBING
        self._text = t("overlay.transcribing").rstrip("…").rstrip(".")
        self._tick_n = 0
        self._present()
        self._anim.start(320)
        self.update()

    def complete_transcribing(self) -> None:
        if self._state == self._TRANSCRIBING:
            self._hide_timer.start(200)
        else:
            self.hide_overlay()

    def show_result(self, text: str) -> None:
        self._state = self._RESULT
        self._text = text[:80] + ("…" if len(text) > 80 else "")
        self._anim.stop()
        self._present()
        self._hide_timer.start(1500)

    def show_error(self, msg: str) -> None:
        self._state = self._ERROR
        self._text = msg[:70]
        self._anim.stop()
        self._present()
        self._hide_timer.start(2600)

    def hide_overlay(self) -> None:
        self._hide_timer.stop()
        self._anim.stop()
        self._state = self._IDLE
        self.hide()

    # ── internals ───────────────────────────────────────────────────────

    def _present(self) -> None:
        if not self.isVisible():
            s = self.screen()
            if s:
                g = s.availableGeometry()
                self.move(QPoint(g.x() + (g.width() - _W) // 2, g.y() + 52))
            self.show()
        self.update()

    def _tick(self) -> None:
        self._tick_n += 1
        if self._state == self._RECORDING and self._audio_rms < 0.002:
            self._smooth *= 0.9
        self.update()

    # ── paint ───────────────────────────────────────────────────────────

    def paintEvent(self, ev) -> None:
        if self._state == self._IDLE:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(_MX, _MY, _PW, _PH)

        # Two very faint layers stand in for a shadow (no blur, no glow).
        for i, a in enumerate((16, 8)):
            sh = QPainterPath()
            sh.addRoundedRect(bar.adjusted(-i, 1 + i, i, 2 + i * 2), 13 + i, 13 + i)
            p.fillPath(sh, QColor(0, 0, 0, a))
        path = QPainterPath()
        path.addRoundedRect(bar, 12, 12)
        p.fillPath(path, QColor("#7F1D1D") if self._state == self._ERROR else _INK)

        p.save()
        p.translate(bar.topLeft())
        if self._state == self._RECORDING:
            self._paint_recording(p)
        elif self._state == self._TRANSCRIBING:
            self._paint_transcribing(p)
        else:
            self._paint_message(p)
        p.restore()
        p.end()

    def _paint_recording(self, p: QPainter) -> None:
        w, h = _PW, _PH
        cy = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FF6B35"))
        p.drawEllipse(QPointF(24, cy), 4.5, 4.5)

        p.setFont(theme.font(13, bold=True))
        p.setPen(_PAPER)
        p.drawText(QRectF(40, 0, 96, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._text)

        wx, ww = 132.0, 130.0
        step = ww / _BARS
        bw = max(1.8, step * 0.5)
        p.setPen(Qt.PenStyle.NoPen)
        for i, lv in enumerate(self._levels):
            amp = max(0.0, min(1.0, lv))
            bh = max(2.0, amp * (h - 20))
            c = QColor(_PAPER)
            c.setAlpha(70 if amp < 0.03 else int(120 + 135 * amp))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(wx + i * step, cy - bh / 2, bw, bh), bw / 2, bw / 2)

        secs = int(time.monotonic() - self._rec_t0)
        p.setFont(theme.font_mono(12, bold=True))
        p.setPen(_DIM)
        p.drawText(QRectF(wx + ww + 14, 0, 44, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{secs // 60}:{secs % 60:02d}")

        if self._hotkey:
            label = display_combo(self._hotkey)
            f = theme.font_mono(11, bold=True)
            p.setFont(f)
            cw = QFontMetrics(f).horizontalAdvance(label) + 18
            chip = QRectF(w - cw - 16, cy - 11, cw, 22)
            p.setBrush(QColor(251, 251, 250, 30))
            p.setPen(QPen(QColor(251, 251, 250, 70), 1))
            p.drawRoundedRect(chip, 4, 4)
            p.setPen(_PAPER)
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_transcribing(self, p: QPainter) -> None:
        h = _PH
        p.setFont(theme.font(14, bold=True))
        p.setPen(_PAPER)
        fm = p.fontMetrics()
        x = 24
        p.drawText(QRectF(x, 0, fm.horizontalAdvance(self._text) + 4, h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
        dots = "." * (self._tick_n % 4)
        p.drawText(QRectF(x + fm.horizontalAdvance(self._text), 0, 40, h),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, dots)

    def _paint_message(self, p: QPainter) -> None:
        w, h = _PW, _PH
        icon = "alert" if self._state == self._ERROR else "check"
        color = "#F4A9A4" if self._state == self._ERROR else "#9AD7B0"
        paint_icon(p, icon, QRectF(20, h / 2 - 9, 18, 18), color, 2.4)
        f = theme.font(13, bold=self._state == self._ERROR)
        p.setFont(f)
        p.setPen(_PAPER)
        fm = QFontMetrics(f)
        p.drawText(QRectF(50, 0, w - 50 - 20, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self._text, Qt.TextElideMode.ElideRight, int(w - 72)))
