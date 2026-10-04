"""Floating dictation indicator — the only UI most people see while using
the app.

A flat ink bar at the top of the screen. Recording: an orange dot, "Listening",
a live level meter, a seconds counter and the hotkey; with live preview on,
the words recognized so far appear under that row (last few lines, newest at
the bottom). Transcribing: plain text with cycling dots. Then a one-line
result or error. Proofreading adds a quiet working meter and a short inline
word/character diff over the full text, in readable pages; replacement happens independently.
"""

from __future__ import annotations

import math
import time
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget

from thundertalk.core.i18n import t
from thundertalk.core.proofread_diff import display_text, proofread_pages
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon
from thundertalk.ui.keys import display_combo

# The widget is a little larger than the bar so a faint edge shadow fits.
_MX, _MY = 12, 10
_PW, _PH = 420, 52
_W, _H = _PW + 2 * _MX, _PH + 2 * _MY + 4
_BARS = 34

# Live preview text under the recording row.
_PV_LINES = 3                 # at most this many lines; older text scrolls off the top
_PV_PAD_X, _PV_PAD_B = 24, 14
_PV_LINE_H = 20
_PV_FONT_PT = 13
_DIFF_LINES = _PV_LINES      # proofread pages use the same width and height as the live text

_INK = QColor(theme.INK)
_PAPER = QColor("#FBFBFA")
_DIM = QColor(251, 251, 250, 150)
_PV_INK = QColor(251, 251, 250, 225)
_RULE = QColor(251, 251, 250, 34)


def wrap_tail(text: str, font, width: int, max_lines: int = _PV_LINES) -> list[str]:
    """Word-wrap *text* to *width* px and keep the last *max_lines* lines,
    starting the first kept line with "…" when earlier text was dropped.
    Wraps anywhere when there are no spaces (Chinese)."""
    text = " ".join(text.split())
    if not text:
        return []
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    lines: list[str] = []
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(text[line.textStart(): line.textStart() + line.textLength()].strip())
    layout.endLayout()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        fm = QFontMetrics(font)
        first = "…" + lines[0]
        if fm.horizontalAdvance(first) > width:
            first = fm.elidedText(first, Qt.TextElideMode.ElideLeft, width)
        lines[0] = first
    return lines


class VoiceOverlay(QWidget):

    _IDLE, _RECORDING, _TRANSCRIBING, _RESULT, _ERROR, _CLEANUP, _DIFF = range(7)

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
        self._preview = ""
        self._pv_lines: list[str] = []
        self._diff = []
        self._diff_pages = []
        self._diff_page = 0
        self._diff_started = 0.0
        self._diff_progress = 0.0
        self._hovered = False
        self._pause_started = None
        self._paused_seconds = 0.0

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
        self._reset_diff()
        self._state = self._RECORDING
        self._text = t("overlay.listening").rstrip("…").rstrip(".")
        self._smooth = 0.0
        self._audio_rms = 0.0
        self._levels = deque([0.0] * _BARS, maxlen=_BARS)
        self._rec_t0 = time.monotonic()
        self._set_preview_lines([])
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

    def set_preview_text(self, text: str) -> None:
        """Live transcript so far. Only shown while recording or
        transcribing; empty text collapses the bar back to one row."""
        if self._state not in (self._RECORDING, self._TRANSCRIBING):
            return
        self._preview = text
        self._set_preview_lines(
            wrap_tail(text, theme.font(_PV_FONT_PT), _PW - 2 * _PV_PAD_X))
        self.update()

    @property
    def preview_lines(self) -> list[str]:
        return list(self._pv_lines)

    def show_transcribing(self) -> None:
        self._hide_timer.stop()
        self._reset_diff()
        self._state = self._TRANSCRIBING
        self._text = t("overlay.transcribing").rstrip("…").rstrip(".")
        self._tick_n = 0
        self._present()
        self._anim.start(320)
        self.update()

    def show_waiting(self) -> None:
        """Transcribing, but behind a Studio job; the whole clip is kept."""
        if self._state != self._TRANSCRIBING:
            return
        self._text = t("overlay.waiting_studio").rstrip("…")
        self.update()

    def show_cleanup(self, original: str = "") -> None:
        self.show_transcribing()
        self._state = self._CLEANUP
        self._text = t("cleanup.progress").rstrip("…")
        self._set_preview_lines(wrap_tail(original, theme.font(_PV_FONT_PT), _PW - 2 * _PV_PAD_X))
        self._anim.start(50)
        self.update()

    def show_cleanup_diff(self, original: str, corrected: str) -> None:
        self._hide_timer.stop()
        self._reset_diff()
        if original == corrected:
            self.show_result(t("cleanup.no_changes"))
            self._hide_timer.start(900)
            return
        self._diff_pages = proofread_pages(original, corrected, self._diff_fits)
        self._diff = self._diff_pages[0].spans
        self._state = self._DIFF
        self._text = t("cleanup.corrected")
        self._diff_started = time.monotonic()
        self._diff_progress = 0.0
        lines = max(self._diff_layout(display_text(page.spans)).lineCount()
                    for page in self._diff_pages)
        self._set_preview_lines([""] * max(1, min(_DIFF_LINES, lines)))
        self._present()
        if self._hovered or self.underMouse():
            self._pause_started = self._diff_started
            self._anim.stop()
        else:
            self._anim.start(30)

    def advance_cleanup_animation(self, elapsed: float) -> None:
        """Advance by wall seconds since presentation, excluding hover time."""
        if self._state != self._DIFF:
            return
        if self._pause_started is not None:
            elapsed = min(elapsed, self._pause_started - self._diff_started)
        elapsed = max(0.0, elapsed - self._paused_seconds)
        start = 0.0
        for index, page in enumerate(self._diff_pages):
            if elapsed < start + page.duration:
                self._diff_page = index
                self._diff = page.spans
                self._diff_progress = min(1.0, (elapsed - start) / 0.18)
                break
            start += page.duration
        else:
            self.hide_overlay()
            return
        self.update()

    def complete_transcribing(self) -> None:
        if self._state == self._TRANSCRIBING:
            self._hide_timer.start(200)
        else:
            self.hide_overlay()

    def show_result(self, text: str) -> None:
        self._reset_diff()
        self._set_preview_lines([])
        self._state = self._RESULT
        self._text = text[:80] + ("…" if len(text) > 80 else "")
        self._anim.stop()
        self._present()
        self._hide_timer.start(1500)

    def show_error(self, msg: str) -> None:
        self._reset_diff()
        self._set_preview_lines([])
        self._state = self._ERROR
        self._text = msg[:70]
        self._anim.stop()
        self._present()
        self._hide_timer.start(2600)

    def hide_overlay(self) -> None:
        self._hide_timer.stop()
        self._anim.stop()
        self._state = self._IDLE
        self._reset_diff()
        self._hovered = False
        self._set_preview_lines([])
        self.hide()

    # ── internals ───────────────────────────────────────────────────────

    def _reset_diff(self) -> None:
        self._diff = []
        self._diff_pages = []
        self._diff_page = 0
        self._pause_started = None
        self._paused_seconds = 0.0

    def enterEvent(self, event) -> None:
        self._hovered = True
        if self._state == self._DIFF and self._pause_started is None:
            now = time.monotonic()
            self.advance_cleanup_animation(now - self._diff_started)
            if self._state == self._DIFF:
                self._pause_started = now
                self._anim.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self._state == self._DIFF and self._pause_started is not None:
            self._paused_seconds += time.monotonic() - self._pause_started
            self._pause_started = None
            self._anim.start(30)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._state == self._DIFF and event.button() == Qt.MouseButton.LeftButton:
            self.hide_overlay()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _diff_layout(self, text: str, ranges=()) -> QTextLayout:
        layout = QTextLayout(text, theme.font(_PV_FONT_PT))
        layout.setFormats(ranges)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(_PW - 2 * _PV_PAD_X)
        layout.endLayout()
        return layout

    def _diff_fits(self, text: str) -> bool:
        return self._diff_layout(text).lineCount() <= _DIFF_LINES

    def _set_preview_lines(self, lines: list[str]) -> None:
        if not lines:
            self._preview = ""
        if lines == self._pv_lines:
            return
        self._pv_lines = lines
        extra = len(lines) * _PV_LINE_H + _PV_PAD_B if lines else 0
        # Grows downward; the top edge (set in _present) stays put.
        self.setFixedSize(_W, _H + extra)

    def _present(self) -> None:
        if not self.isVisible():
            s = self.screen()
            if s:
                g = s.availableGeometry()
                self.move(QPoint(g.x() + (g.width() - _W) // 2, g.y() + 52))
            self.show()
        self.update()

    def _tick(self) -> None:
        if self._state == self._DIFF:
            self.advance_cleanup_animation(time.monotonic() - self._diff_started)
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
        pv_h = len(self._pv_lines) * _PV_LINE_H + _PV_PAD_B if self._pv_lines else 0
        bar = QRectF(_MX, _MY, _PW, _PH + pv_h)

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
        elif self._state in (self._TRANSCRIBING, self._CLEANUP):
            self._paint_transcribing(p)
        else:
            self._paint_message(p)
        if self._state == self._DIFF:
            self._paint_diff(p)
        elif self._pv_lines:
            self._paint_preview(p)
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
        if self._state == self._CLEANUP:
            p.setPen(QPen(_DIM, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            cx = _PW - 38
            for i in range(5):
                amp = 2 + 3 * (1 + math.sin(self._tick_n * 0.22 + i * 0.7)) / 2
                p.drawLine(QPointF(cx + i * 4, h / 2 - amp), QPointF(cx + i * 4, h / 2 + amp))
        dots = "." * ((self._tick_n // 6 if self._state == self._CLEANUP else self._tick_n) % 4)
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
        available = w - 72
        if self._state == self._DIFF:
            page = self._diff_pages[self._diff_page]
            label = f"{self._diff_page + 1}/{len(self._diff_pages)}"
            if page.omitted:
                label = t("cleanup.more_changes").format(n=page.omitted) + " · " + label
            p.setFont(theme.font(11))
            p.setPen(_DIM)
            width = p.fontMetrics().horizontalAdvance(label) + 4
            p.drawText(QRectF(w - width - 24, 0, width, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextSingleLine,
                       label)
            available -= width + 16
            p.setFont(f)
            p.setPen(_PAPER)
        p.drawText(QRectF(50, 0, available, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self._text, Qt.TextElideMode.ElideRight, available))

    def _paint_preview(self, p: QPainter) -> None:
        p.setPen(QPen(_RULE, 1))
        p.drawLine(QPointF(_PV_PAD_X, _PH - 0.5), QPointF(_PW - _PV_PAD_X, _PH - 0.5))
        p.setFont(theme.font(_PV_FONT_PT))
        p.setPen(_PV_INK)
        y = _PH + 2
        for line in self._pv_lines:
            p.drawText(QRectF(_PV_PAD_X, y, _PW - 2 * _PV_PAD_X, _PV_LINE_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line)
            y += _PV_LINE_H

    def _paint_diff(self, p: QPainter) -> None:
        p.setPen(QPen(_RULE, 1))
        p.drawLine(QPointF(_PV_PAD_X, _PH), QPointF(_PW - _PV_PAD_X, _PH))
        text, ranges = "", []
        for span in self._diff:
            parts = [(span.original, "equal")] if span.kind == "equal" else [
                (span.original, "old"), (" → " if span.original and span.corrected else "", "arrow"),
                (span.corrected, "new")]
            for value, kind in parts:
                start = len(text.encode("utf-16-le")) // 2
                text += value
                fmt = QTextCharFormat()
                color = QColor(_PV_INK if kind == "equal" else _DIM)
                if kind == "old":
                    fmt.setFontStrikeOut(True)
                elif kind == "new":
                    color = QColor("#FFAD8F")
                color.setAlpha(int(color.alpha() * (0.85 + 0.15 * self._diff_progress)))
                fmt.setForeground(color)
                fr = QTextLayout.FormatRange()
                fr.start, fr.length, fr.format = start, len(value.encode("utf-16-le")) // 2, fmt
                ranges.append(fr)
        layout = self._diff_layout(text, ranges)
        p.save()
        p.setClipRect(QRectF(_PV_PAD_X, _PH + 4, _PW - 2 * _PV_PAD_X, _DIFF_LINES * _PV_LINE_H))
        for i in range(layout.lineCount()):
            line = layout.lineAt(i)
            line.draw(p, QPointF(_PV_PAD_X, _PH + 4 + i * _PV_LINE_H))
        p.restore()
