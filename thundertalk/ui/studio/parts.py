"""Small self-painted widgets for the Studio: drop zone, waveform scrubber,
transcript rows, voice chips and the audio player bar."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import audio_io, i18n
from thundertalk.core.i18n import t
from thundertalk.core.playback import Player
from thundertalk.core.transcribe import Transcript, fmt_time
from thundertalk.ui import theme
from thundertalk.ui.icons import paint_icon

SPEAKER_KINDS = ["blue", "orange", "green", "purple", "amber", "red"]


def fmt_size(nbytes: int) -> str:
    if nbytes >= 1 << 30:
        return f"{nbytes / (1 << 30):.1f} GB"
    if nbytes >= 1 << 20:
        return f"{nbytes / (1 << 20):.1f} MB"
    return f"{max(1, nbytes // 1024)} KB"


def fmt_seconds(secs: float) -> str:
    """"1.4 s" / "42 s" / "3 min 5 s" — or "1.4 秒" / "3 分 5 秒" in Chinese."""
    zh = i18n.LANG == "zh"
    sec, mn = ("秒", "分") if zh else ("s", "min")
    gap = " "
    if secs < 10:
        return f"{secs:.1f}{gap}{sec}"
    if secs < 90:
        return f"{secs:.0f}{gap}{sec}"
    return f"{int(secs // 60)}{gap}{mn}{gap}{int(secs % 60)}{gap}{sec}"


# ── drop zone ────────────────────────────────────────────────────────────

class DropZone(QFrame):
    """A dashed target for one audio/video file. Click to browse, or drop."""

    file_chosen = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = False
        self._drag = False
        self._path = ""

        ly = QVBoxLayout(self)
        ly.setContentsMargins(24, 18, 24, 18)
        ly.setSpacing(4)
        ly.addStretch()
        self._title = QLabel()
        self._title.setFont(theme.font_serif(15))
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        self._title.setWordWrap(True)
        self._sub = QLabel()
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        self._sub.setWordWrap(True)
        ly.addWidget(self._title)
        ly.addWidget(self._sub)
        ly.addStretch()
        for w in (self._title, self._sub):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.retranslate()

    # -- api ------------------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    def set_file(self, path: str, duration: float = 0.0) -> None:
        self._path = path
        self._duration = duration
        self._refresh_text()

    def clear(self) -> None:
        self._path = ""
        self._refresh_text()

    def retranslate(self) -> None:
        self._refresh_text()

    def _refresh_text(self) -> None:
        if not self._path:
            self._title.setText(t("studio.drop.hint"))
            self._sub.setText(t("studio.drop.sub"))
            return
        p = Path(self._path)
        try:
            size = fmt_size(p.stat().st_size)
        except OSError:
            size = ""
        dur = getattr(self, "_duration", 0.0)
        bits = [fmt_time(dur)] if dur > 0 else []
        bits += [b for b in (size, p.suffix.lstrip(".").upper()) if b]
        self._title.setText(p.name)
        self._sub.setText("  ·  ".join(bits) + "   —   " + t("studio.drop.change"))

    # -- events ---------------------------------------------------------
    def _browse(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(audio_io.AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, t("studio.drop.dialog"), str(Path.home()),
            f"{t('studio.drop.filter')} ({exts});;All files (*)")
        if path:
            self.file_chosen.emit(path)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self._browse()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._browse()
        else:
            super().keyPressEvent(ev)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and any(u.isLocalFile() for u in ev.mimeData().urls()):
            ev.acceptProposedAction()
            self._drag = True
            self.update()

    def dragLeaveEvent(self, ev) -> None:
        self._drag = False
        self.update()

    def dropEvent(self, ev) -> None:
        self._drag = False
        self.update()
        for u in ev.mimeData().urls():
            if u.isLocalFile() and os.path.isfile(u.toLocalFile()):
                self.file_chosen.emit(u.toLocalFile())
                break

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self._hover or self._drag or self.hasFocus()
        p.setBrush(theme.qcolor(theme.HOVER_FILL if on else theme.BG_CARD))
        pen = QPen(theme.qcolor(theme.INK if on else theme.BORDER_STRONG), 1.2,
                   Qt.PenStyle.DashLine if not self._path else Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.drawRoundedRect(r, theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.end()


# ── waveform scrubber ────────────────────────────────────────────────────

class WaveScrub(QWidget):
    """Static waveform overview with a playhead; click or drag to seek."""

    seek = Signal(float)

    def __init__(self, height: int = 48) -> None:
        super().__init__()
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._peaks = np.zeros(0, dtype=np.float32)
        self._frac = 0.0
        self._drag = False

    def set_audio(self, x: Optional[np.ndarray]) -> None:
        if x is None or len(x) == 0:
            self._peaks = np.zeros(0, dtype=np.float32)
        else:
            n = 720
            step = max(1, len(x) // n)
            m = len(x) // step
            pk = np.abs(x[: m * step]).reshape(m, step).max(axis=1)
            top = float(np.percentile(pk, 99)) or 1.0
            self._peaks = np.clip(pk / top, 0.0, 1.0).astype(np.float32)
        self._frac = 0.0
        self.update()

    def set_fraction(self, f: float) -> None:
        f = min(max(f, 0.0), 1.0)
        if abs(f - self._frac) > 1e-4:
            self._frac = f
            self.update()

    def _emit(self, ev) -> None:
        f = min(max(ev.position().x() / max(1, self.width()), 0.0), 1.0)
        self._frac = f
        self.update()
        self.seek.emit(f)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._emit(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag:
            self._emit(ev)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = False

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._peaks)
        if n == 0:
            p.setPen(QPen(theme._BORDER_SUBTLE_C, 1))
            p.drawLine(0, h // 2, w, h // 2)
            p.end()
            return
        pitch = 4.0
        bars = max(1, int(w / pitch))
        played = theme.qcolor(theme.INK)
        rest = QColor(31, 30, 27, 70)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(bars):
            a, b = int(i * n / bars), max(int(i * n / bars) + 1, int((i + 1) * n / bars))
            v = float(self._peaks[a:b].max())
            bh = max(2.0, v * (h - 6))
            x = i * pitch
            p.setBrush(played if (i + 0.5) / bars <= self._frac else rest)
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, 2.4, bh), 1.2, 1.2)
        if self._frac > 0:
            p.setPen(QPen(theme.qcolor(theme.INK), 1.2))
            xx = self._frac * w
            p.drawLine(int(xx), 2, int(xx), h - 2)
        p.end()


# ── round play button ────────────────────────────────────────────────────

class RoundButton(QPushButton):
    """Solid ink circle with a white vector icon (play / pause / stop / record)."""

    def __init__(self, icon: str, size: int = 40, color: str = theme.INK) -> None:
        super().__init__()
        self._icon, self._color = icon, color
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def set_icon(self, icon: str, color: Optional[str] = None) -> None:
        self._icon = icon
        if color:
            self._color = color
        self.update()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        if self._hover and self.isEnabled():
            c = c.lighter(118)
        if not self.isEnabled():
            c = theme.qcolor(theme.BORDER_STRONG)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        inset = self.width() * 0.29
        paint_icon(p, self._icon, QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), "#FFFFFF", 2.1)
        p.end()


# ── player bar ───────────────────────────────────────────────────────────

class PlayerBar(QWidget):
    """Play/pause, a seekable waveform, elapsed/total time and a Save menu."""

    save_requested = Signal(str)          # "wav" | "m4a"
    started = Signal()                    # playback (re)started

    def __init__(self, with_save: bool = True) -> None:
        super().__init__()
        self._player = Player()
        self._sr = 24000
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self._btn = RoundButton("play", 40)
        self._btn.clicked.connect(self.toggle)
        row.addWidget(self._btn)
        col = QVBoxLayout()
        col.setSpacing(2)
        self._wave = WaveScrub(46)
        self._wave.seek.connect(self._on_seek)
        col.addWidget(self._wave)
        row.addLayout(col, 1)
        self._time = QLabel("0:00 / 0:00")
        self._time.setFont(theme.font_mono(12))
        self._time.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent;")
        self._time.setMinimumWidth(86)
        row.addWidget(self._time)
        self._save = theme.make_button(t("studio.save"), "secondary", 34, font_px=12)
        self._save.setVisible(with_save)
        self._save.clicked.connect(self._save_menu)
        row.addWidget(self._save)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        # Keyboard: Space plays/pauses, ← / → jump 5 s. Clicking the waveform
        # gives the bar focus; keys pressed on the play button bubble up here.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._wave.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._wave.seek.connect(lambda _f: self.setFocus(Qt.FocusReason.MouseFocusReason))
        self.setToolTip(t("studio.player.keys"))

    SEEK_STEP_S = 5.0

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self.toggle()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self._player.duration > 0:
            step = self.SEEK_STEP_S if key == Qt.Key.Key_Right else -self.SEEK_STEP_S
            target = min(max(self._player.position + step, 0.0), self._player.duration)
            self._on_seek(target / self._player.duration)
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    # -- api ------------------------------------------------------------
    @property
    def player(self) -> Player:
        return self._player

    def set_audio(self, x: np.ndarray, sr: int) -> None:
        self._sr = sr
        self._player.load(x, sr)
        self._wave.set_audio(x)
        self._btn.set_icon("play")
        self._update_time()

    def clear(self) -> None:
        self._timer.stop()
        self._player.clear()
        self._wave.set_audio(None)
        self._btn.set_icon("play")
        self._time.setText("0:00 / 0:00")

    def shutdown(self) -> None:
        self._timer.stop()
        self._player.stop()
        self._btn.set_icon("play")

    def retranslate(self) -> None:
        self._save.setText(t("studio.save"))

    def pause(self) -> None:
        if self._player.is_playing:
            self.toggle()

    def toggle(self) -> None:
        if self._player.is_playing:
            self._player.pause()
            self._btn.set_icon("play")
            self._timer.stop()
        elif self._player.play():
            self.started.emit()
            self._btn.set_icon("pause")
            self._timer.start()
        else:
            self._time.setText(t("studio.play_failed"))

    # -- internals ------------------------------------------------------
    def _on_seek(self, f: float) -> None:
        was = self._player.is_playing
        self._player.seek(f)
        if was:
            self._player.play(start=f)
        self._update_time()

    def _tick(self) -> None:
        playing = self._player.poll()
        self._wave.set_fraction(self._player.fraction)
        self._update_time()
        if not playing:
            self._btn.set_icon("play")
            self._timer.stop()

    def _update_time(self) -> None:
        self._time.setText(f"{fmt_time(self._player.position)} / {fmt_time(self._player.duration)}")
        self._wave.set_fraction(self._player.fraction)

    def _save_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("WAV — " + t("studio.save.wav"), lambda: self.save_requested.emit("wav"))
        menu.addAction("M4A — " + t("studio.save.m4a"), lambda: self.save_requested.emit("m4a"))
        menu.exec(self._save.mapToGlobal(self._save.rect().bottomLeft()))


# ── transcript view ──────────────────────────────────────────────────────

class _SpeakerChip(theme.Chip):
    clicked = Signal()

    def __init__(self, text: str, kind: str) -> None:
        fg, bg, bd = theme.PASTELS[kind]
        super().__init__(text, fg, bg, bd, size_px=11)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t("studio.rename_speaker"))

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class TranscriptView(QWidget):
    """One row per segment (or per speaker turn): timestamp, speaker chip, text."""

    speaker_clicked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        self._ly = QVBoxLayout(self)
        self._ly.setContentsMargins(0, 0, 0, 0)
        self._ly.setSpacing(0)
        self._tr: Optional[Transcript] = None
        self._show_time = True
        self._times: list[QLabel] = []
        self._chips: list[tuple[_SpeakerChip, str]] = []

    def clear(self) -> None:
        self._tr = None
        self._times.clear()
        self._chips.clear()
        while self._ly.count():
            it = self._ly.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def set_transcript(self, tr: Transcript) -> None:
        self.clear()
        self._tr = tr
        rows = tr.turns() if tr.has_speakers else tr.segments
        order = tr.speakers
        for i, seg in enumerate(rows):
            self._ly.addWidget(self._row(seg, order, first=(i == 0)))
        self._equalise_chips()

    def set_timestamps(self, on: bool) -> None:
        self._show_time = on
        for lb in self._times:
            lb.setVisible(on)

    def refresh_speaker_names(self) -> None:
        if self._tr is None:
            return
        for chip, spk in self._chips:
            chip.setText(self._tr.label(spk))
        self._equalise_chips()

    def _equalise_chips(self) -> None:
        """One shared chip width, so every row's text starts at the same x."""
        if not self._chips:
            return
        w = min(150, max(c.sizeHint().width() for c, _ in self._chips))
        for chip, _ in self._chips:
            chip.setFixedWidth(w)

    def _row(self, seg, order: list[str], first: bool) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 10, 0, 10)
        row.setSpacing(14)
        tm = QLabel(fmt_time(seg.start))
        tm.setFont(theme.font_mono(11))
        tm.setFixedWidth(46)
        tm.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        tm.setStyleSheet(f"color: {theme.TEXT_MUTED}; background: transparent; padding-top: 3px;")
        tm.setVisible(self._show_time)
        self._times.append(tm)
        row.addWidget(tm)
        if seg.speaker and self._tr is not None:
            kind = SPEAKER_KINDS[order.index(seg.speaker) % len(SPEAKER_KINDS)]
            chip = _SpeakerChip(self._tr.label(seg.speaker), kind)
            chip.clicked.connect(lambda s=seg.speaker: self.speaker_clicked.emit(s))
            self._chips.append((chip, seg.speaker))
            row.addWidget(chip, alignment=Qt.AlignmentFlag.AlignTop)
        text = QLabel(seg.text)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; background: transparent;")
        text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        row.addWidget(text, 1)
        if not first:
            holder = QWidget()
            holder.setStyleSheet("background: transparent;")
            v = QVBoxLayout(holder)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            from thundertalk.ui.widgets import Rule
            v.addWidget(Rule())
            v.addWidget(w)
            return holder
        return w


# ── voice chips ──────────────────────────────────────────────────────────

class VoiceChip(QAbstractButton):
    """A selectable voice: name on top, one-line description under it."""

    preview = Signal()          # the round play button on the right was clicked

    _PREVIEW_W = 30             # width of the play-button zone

    def __init__(self, name: str, sub: str = "", *, dashed: bool = False, icon: str = "",
                 previewable: bool = False) -> None:
        super().__init__()
        self._name, self._sub, self._dashed, self._icon = name, sub, dashed, icon
        self._hover = False
        self._previewable, self._playing, self._press_preview = previewable, False, False
        if previewable:
            self.setToolTip(t("studio.voices.preview"))
        self.setCheckable(not dashed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(name)

    def set_texts(self, name: str, sub: str) -> None:
        self._name, self._sub = name, sub
        self.setAccessibleName(name)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        f1, f2 = theme.font(13, bold=True), theme.font(11)
        w = max(QFontMetrics(f1).horizontalAdvance(self._name),
                QFontMetrics(f2).horizontalAdvance(self._sub) if self._sub else 0)
        return QSize(int(w) + 36 + (22 if self._icon else 0) + (self._PREVIEW_W if self._previewable else 0),
                     54 if self._sub else 42)

    # -- preview button ---------------------------------------------------
    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.update()

    def is_playing(self) -> bool:
        return self._playing

    def _preview_rect(self) -> QRectF:
        s = 24.0
        return QRectF(self.width() - self._PREVIEW_W - 4 + (self._PREVIEW_W - s) / 2,
                      (self.height() - s) / 2, s, s)

    def _in_preview(self, pos) -> bool:
        return self._previewable and pos.x() >= self.width() - self._PREVIEW_W - 6

    def mousePressEvent(self, ev) -> None:
        self._press_preview = ev.button() == Qt.MouseButton.LeftButton and self._in_preview(ev.position())
        if self._press_preview:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._press_preview:
            self._press_preview = False
            if self._in_preview(ev.position()) and self.rect().contains(ev.position().toPoint()):
                self.preview.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = self.isChecked()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(theme.qcolor(theme.BG_CARD if not self._hover else theme.HOVER_FILL))
        if sel:
            p.setPen(QPen(theme.qcolor(theme.INK), 1.6))
        else:
            p.setPen(QPen(theme.qcolor(theme.BORDER_STRONG if self._dashed else theme.BORDER_DEFAULT), 1,
                          Qt.PenStyle.DashLine if self._dashed else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(r, theme.RADIUS_CONTROL + 2, theme.RADIUS_CONTROL + 2)
        x = 16.0
        if self._icon:
            paint_icon(p, self._icon, QRectF(14, (self.height() - 14) / 2, 14, 14), theme.TEXT_SECONDARY, 2.0)
            x = 36.0
        right = 8 + (self._PREVIEW_W if self._previewable else 0)
        p.setPen(theme.qcolor(theme.TEXT_PRIMARY))
        p.setFont(theme.font(13, bold=True))
        if self._sub:
            p.drawText(QRectF(x, 9, self.width() - x - right, 18), Qt.AlignmentFlag.AlignVCenter, self._name)
            p.setPen(theme.qcolor(theme.TEXT_MUTED))
            p.setFont(theme.font(11))
            p.drawText(QRectF(x, 28, self.width() - x - right, 16), Qt.AlignmentFlag.AlignVCenter, self._sub)
        else:
            p.drawText(QRectF(x, 0, self.width() - x - right, self.height()), Qt.AlignmentFlag.AlignVCenter,
                       self._name)
        if self._previewable:
            pr = self._preview_rect()
            p.setPen(QPen(theme.qcolor(theme.INK if self._playing else theme.BORDER_STRONG), 1))
            p.setBrush(theme.qcolor(theme.INK if self._playing else theme.BG_CARD))
            p.drawEllipse(pr)
            paint_icon(p, "stop" if self._playing else "play", pr.adjusted(7, 7, -7, -7),
                       theme.BG_CARD if self._playing else theme.TEXT_SECONDARY, 1.6)
        p.end()
