"""Floating voice input capsule — polished pill overlay for recording state."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, QTimer, Qt, QPointF
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from thundertalk.core.i18n import t
from thundertalk.ui import theme

_W, _H = 340, 56


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
        self._phase = 0.0
        self._audio_rms: float = 0.0
        self._smooth_rms: float = 0.0
        self._progress: float = 0.0
        self._t0: float = 0.0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

    # ── public API ──────────────────────────────────────────────────────

    def show_recording(self) -> None:
        self._state = self._RECORDING
        self._text = t("overlay.listening")
        self._phase = 0.0
        self._center()
        self.show()
        self._anim.start(16)

    def set_audio_level(self, rms: float) -> None:
        self._audio_rms = rms

    def show_transcribing(self) -> None:
        self._state = self._TRANSCRIBING
        self._text = t("overlay.transcribing")
        self._progress = 0.0
        self._t0 = time.monotonic()
        if not self._anim.isActive():
            self._anim.start(16)
        self.update()

    def complete_transcribing(self) -> None:
        if self._state == self._TRANSCRIBING:
            self._progress = 1.0
            self.update()
            QTimer.singleShot(200, self.hide_overlay)
        else:
            self.hide_overlay()

    def show_result(self, text: str) -> None:
        self._state = self._RESULT
        self._text = text[:55] + ("…" if len(text) > 55 else "")
        self._anim.stop()
        self.update()
        QTimer.singleShot(1500, self.hide_overlay)

    def show_error(self, msg: str) -> None:
        self._state = self._ERROR
        self._text = msg[:50]
        self._anim.stop()
        self.update()
        QTimer.singleShot(2500, self.hide_overlay)

    def hide_overlay(self) -> None:
        self._anim.stop()
        self._state = self._IDLE
        self.hide()

    # ── internals ───────────────────────────────────────────────────────

    def _center(self) -> None:
        s = self.screen()
        if s:
            g = s.availableGeometry()
            self.move(g.x() + (g.width() - _W) // 2, g.y() + 80)

    def _tick(self) -> None:
        self._phase += 0.08
        t = min(1.0, self._audio_rms * 10.0)
        k = 0.22 if t > self._smooth_rms else 0.06
        self._smooth_rms += (t - self._smooth_rms) * k
        if self._state == self._TRANSCRIBING:
            dt = time.monotonic() - self._t0
            self._progress = 1.0 - 1.0 / (1.0 + dt * 0.7)
        self.update()

    # ── paint ───────────────────────────────────────────────────────────

    def paintEvent(self, ev) -> None:
        if self._state == self._IDLE:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = _W, _H
        r = h / 2

        pill = QPainterPath()
        pill.addRoundedRect(QRectF(0, 0, w, h), r, r)

        bg = QColor(40, 15, 15, 240) if self._state == self._ERROR else QColor(18, 18, 18, 240)
        p.fillPath(pill, bg)
        p.setPen(QPen(QColor(255, 255, 255, 15), 1))
        p.drawPath(pill)

        if self._state == self._RECORDING:
            self._paint_recording(p, w, h)
        elif self._state == self._TRANSCRIBING:
            self._paint_transcribing(p, w, h)
        else:
            self._paint_text(p, w, h)
        p.end()

    # ── recording ───────────────────────────────────────────────────────

    def _paint_recording(self, p: QPainter, w: int, h: int) -> None:
        # Label
        f = QFont("Helvetica Neue", 13)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(24, int(h / 2 + 5), self._text)

        # Waveform bars with smooth interpolation
        lv = self._smooth_rms
        bx, bw, nb = 150, 168, 28
        sp = bw / nb
        for i in range(nb):
            x = bx + i * sp
            wave = 0.5 + 0.5 * math.sin(self._phase * 2.0 + i * 0.32)
            amp = 0.04 + 0.96 * lv * wave
            bh = max(2, int(20 * amp))
            y = int(h / 2 - bh / 2)
            c = QColor(theme.ACCENT_ORANGE)
            c.setAlpha(int(150 + 100 * amp))
            p.setPen(QPen(c, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x), y, int(x), y + bh)

    # ── transcribing ────────────────────────────────────────────────────

    def _paint_transcribing(self, p: QPainter, w: int, h: int) -> None:
        # Soft, wide breathing orange glow behind the text — subtle warmth,
        # no hard edges, reads as "thinking"
        breath = 0.5 + 0.5 * math.sin(self._phase * 1.4)
        glow = QRadialGradient(QPointF(w / 2, h / 2), w * 0.55)
        glow.setColorAt(0.0, QColor(249, 115, 22, int(55 + 45 * breath)))
        glow.setColorAt(0.5, QColor(234, 88, 12, int(18 + 14 * breath)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        # Centered text — base color is muted, a travelling bright gradient
        # sweeps across the text creating an Apple-style shimmer.
        f = QFont("Helvetica Neue", 14)
        f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self._text)
        tx = int((w - tw) / 2)
        ty = int(h / 2 + fm.ascent() / 2 - 2)

        cyc = (self._phase * 0.04) % 1.0
        sweep = (tw + 120) * cyc - 60
        g = QLinearGradient(tx + sweep - 60, 0, tx + sweep + 60, 0)
        g.setColorAt(0.0, QColor(180, 180, 185))
        g.setColorAt(0.45, QColor(180, 180, 185))
        g.setColorAt(0.5, QColor(255, 255, 255))
        g.setColorAt(0.55, QColor(180, 180, 185))
        g.setColorAt(1.0, QColor(180, 180, 185))
        pen = QPen(QBrush(g), 1)
        p.setPen(pen)
        p.drawText(tx, ty, self._text)

    # ── text (result / error) ───────────────────────────────────────────

    def _paint_text(self, p: QPainter, w: int, h: int) -> None:
        f = QFont("Helvetica Neue", 13)
        p.setFont(f)
        c = QColor(theme.ERROR) if self._state == self._ERROR else QColor(theme.TEXT_PRIMARY)
        p.setPen(c)
        p.drawText(QRectF(24, 0, w - 48, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
