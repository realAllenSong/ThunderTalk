"""Small self-painted widgets for the Studio: drop zone, waveform scrubber,
transcript rows, voice chips and the audio player bar."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import (
    QAbstractButton,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextDocumentLayout,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
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
    """A dashed target for audio/video files. Click to browse, or drop.
    One file emits ``file_chosen``; several at once emit ``files_chosen``."""

    file_chosen = Signal(str)
    files_chosen = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = False
        self._drag = False
        self._path = ""
        self._compact = False
        self._link: tuple[str, str] = ("", "")          # url, title

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
        self._link = ("", "")
        self._duration = duration
        self._refresh_text()

    def set_link(self, url: str, title: str = "") -> None:
        self._path = ""
        self._link = (url, title)
        self._refresh_text()

    def clear(self) -> None:
        self._path = ""
        self._link = ("", "")
        self._refresh_text()

    def retranslate(self) -> None:
        self._refresh_text()

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedHeight(48 if compact else 120)
        margins = (16, 8, 16, 8) if compact else (24, 18, 24, 18)
        self.layout().setContentsMargins(*margins)
        self._sub.setVisible(not compact)
        self._refresh_text()

    def _refresh_text(self) -> None:
        url, title = self._link
        if url:
            from thundertalk.core.links import site_name
            self._title.setText(title or url)
            self._sub.setText(t("studio.link.sub").format(site=site_name(url)))
            return
        if not self._path:
            self._title.setText(t("studio.drop.another" if self._compact else "studio.drop.hint"))
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
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("studio.drop.dialog"), str(Path.home()),
            f"{t('studio.drop.filter')} ({exts});;All files (*)")
        self._emit(paths)

    def _emit(self, paths: list[str]) -> None:
        if len(paths) == 1:
            self.file_chosen.emit(paths[0])
        elif paths:
            self.files_chosen.emit(list(paths))

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
        self._emit([u.toLocalFile() for u in ev.mimeData().urls()
                    if u.isLocalFile() and os.path.isfile(u.toLocalFile())])

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self._hover or self._drag or self.hasFocus()
        p.setBrush(theme.qcolor(theme.HOVER_FILL if on else theme.BG_CARD))
        empty = not self._path and not self._link[0]
        pen = QPen(theme.qcolor(theme.INK if on else theme.BORDER_STRONG), 1.2,
                   Qt.PenStyle.DashLine if empty else Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.drawRoundedRect(r, theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.end()


# ── queue row ────────────────────────────────────────────────────────────

class _ElidedLabel(QLabel):
    """One line, elided in the middle when narrow (keeps the extension visible)."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)
        self._apply()

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._apply()

    def full(self) -> str:
        return self._full

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._apply()

    def _apply(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                                                      max(40, self.width())))


class QueueRow(QWidget):
    """One queued file or link: name, status, a thin bar while it runs, and
    one action — remove (waiting), cancel (running) or view (done)."""

    action = Signal(str)                  # "remove" | "cancel" | "view"

    def __init__(self, name: str) -> None:
        super().__init__()
        from thundertalk.ui.widgets import IconButton, ThinProgress
        self.setStyleSheet("background: transparent;")
        self.state = "waiting"
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 8, 0, 8)
        col.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        self._name = _ElidedLabel()
        self._name.setFont(theme.font(13, bold=True))
        self._name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        self._name.set_full(name)
        row.addWidget(self._name, 3)
        self._status = _ElidedLabel()
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._status, 2)
        self._view = theme.make_button(t("studio.batch.view"), "ghost", 28, font_px=12)
        self._view.clicked.connect(lambda: self.action.emit("view"))
        self._view.setVisible(False)
        row.addWidget(self._view)
        self._x = IconButton("x", 26)
        self._x.clicked.connect(lambda: self.action.emit("cancel" if self.state == "running" else "remove"))
        row.addWidget(self._x)
        col.addLayout(row)
        self._bar = ThinProgress(2)
        self._bar.setVisible(False)
        col.addWidget(self._bar)
        self.set_state("waiting", t("studio.batch.waiting"))

    def name(self) -> str:
        return self._name.full()

    def set_name(self, name: str) -> None:
        self._name.set_full(name)

    def status_text(self) -> str:
        return self._status.full()

    def set_state(self, state: str, text: str, pct: int = -1) -> None:
        self.state = state
        self._status.set_full(text)
        running = state == "running"
        self._bar.setVisible(running)
        if running:
            if pct >= 0:
                self._bar.set_value(pct)
            else:
                self._bar.set_indeterminate(True)
        self._view.setVisible(state == "done")
        self._x.setToolTip(t("studio.batch.cancel_item") if running else t("studio.batch.remove"))
        self._status.setStyleSheet(
            f"color: {theme.ERROR if state == 'failed' else theme.TEXT_MUTED}; font-size: 12px;"
            " background: transparent;")

    def retranslate(self) -> None:
        self._view.setText(t("studio.batch.view"))


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


class ContentTextBrowser(QTextBrowser):
    """Markdown sized to its content, with scrolling only for long notes."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)

    def _fit_height(self, *_args) -> None:
        height = min(360, max(32, math.ceil(self.document().size().height()) + 2))
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._fit_height()


class TranscriptView(QWidget):
    """One scrolling editor with cached text documents; one chip per speaker.

    Both modes keep a block per turn/segment, so Qt lays out visible text
    lazily instead of thousands of labels. Switching never formats the text
    or constructs widgets. Plain mode retains paragraph breaks for reading.
    """

    speaker_clicked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background: transparent;")
        self._ly = QVBoxLayout(self)
        self._ly.setContentsMargins(0, 0, 0, 0)
        self._ly.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._speakers = QWidget()
        self._speaker_layout = QHBoxLayout(self._speakers)
        self._speaker_layout.setContentsMargins(0, 0, 0, 8)
        self._speaker_layout.setSpacing(8)
        self._ly.addWidget(self._speakers)
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setUndoRedoEnabled(False)
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        editor_font = theme.font(14)
        editor_font.setPixelSize(14)
        self._editor.setFont(editor_font)
        self._editor.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none;")
        self._ly.addWidget(self._editor)
        self._empty_document = QTextDocument(self._editor)
        self._empty_document.setDocumentLayout(QPlainTextDocumentLayout(self._empty_document))
        self._editor.setDocument(self._empty_document)
        self._tr: Optional[Transcript] = None
        self._show_time = True
        self._chips: list[tuple[_SpeakerChip, str]] = []
        self._documents: dict[bool, QTextDocument] = {}
        self._positions = {True: 0, False: 0}
        self._height_limit = 320
        self._speakers.hide()

    def clear(self) -> None:
        self._tr = None
        self._chips.clear()
        while self._speaker_layout.count():
            it = self._speaker_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._speakers.hide()
        self._editor.setDocument(self._empty_document)
        for doc in self._documents.values():
            doc.deleteLater()
        self._documents.clear()
        self._positions = {True: 0, False: 0}
        self._fit_height()

    def set_transcript(self, tr: Transcript) -> None:
        self.clear()
        self._tr = tr
        for i, speaker in enumerate(tr.speakers if tr.has_speakers else []):
            chip = _SpeakerChip(tr.label(speaker), SPEAKER_KINDS[i % len(SPEAKER_KINDS)])
            chip.clicked.connect(lambda s=speaker: self.speaker_clicked.emit(s))
            self._chips.append((chip, speaker))
            self._speaker_layout.addWidget(chip)
        self._speaker_layout.addStretch()
        self._speakers.setVisible(bool(self._chips))
        self._build_documents()

    def set_timestamps(self, on: bool) -> None:
        if self._show_time == on and self._editor.document() is self._documents.get(on):
            return
        self._positions[self._show_time] = self._editor.verticalScrollBar().value()
        self._show_time = on
        if on in self._documents:
            self._editor.setDocument(self._documents[on])
            self._editor.verticalScrollBar().setValue(self._positions[on])
            self._fit_height()

    def refresh_speaker_names(self) -> None:
        if self._tr is None:
            return
        for chip, spk in self._chips:
            chip.setText(self._tr.label(spk))
        self._build_documents()

    def _build_documents(self) -> None:
        old = self._documents
        self._documents = {}
        rows = self._tr.turns() if self._tr.has_speakers else self._tr.segments
        plain, timed = [], []
        for seg in rows:
            text = f"{self._tr.label(seg.speaker)}: {seg.text}" if seg.speaker else seg.text
            plain.append(text)
            timed.append(f"[{fmt_time(seg.start)}] {text}")
        for on, lines in ((False, plain), (True, timed)):
            doc = QTextDocument(self._editor)
            doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
            doc.setDefaultFont(self._editor.font())
            doc.setUndoRedoEnabled(False)
            doc.setPlainText("\n".join(lines))
            self._documents[on] = doc
        self._positions = {True: 0, False: 0}
        self._editor.verticalScrollBar().setValue(0)
        self.set_timestamps(self._show_time)
        for doc in old.values():
            doc.deleteLater()

    def set_height_limit(self, height: int) -> None:
        self._height_limit = height
        self._fit_height()

    def _fit_height(self) -> None:
        height = 2 * self._editor.document().documentMargin() + 2
        line_height = self._editor.fontMetrics().height()
        if height + self._editor.document().blockCount() * line_height >= self._height_limit:
            self._editor.setFixedHeight(self._height_limit)
            return
        block = self._editor.document().begin()
        while block.isValid() and height < self._height_limit:
            height += max(line_height, self._editor.blockBoundingRect(block).height())
            block = block.next()
        self._editor.setFixedHeight(min(self._height_limit, max(32, math.ceil(height))))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._fit_height()


# ── voice chips ──────────────────────────────────────────────────────────

class VoiceChip(QAbstractButton):
    """A selectable voice: name on top, one-line description under it.

    Optional zones on the right: a round ▶ that previews the voice
    (``previewable``) and a ⋯ that opens per-voice actions (``has_menu``).
    In pick mode (``set_pick_mode``) a click ticks the chip instead of
    selecting the voice, for batch actions."""

    preview = Signal()          # the round play button was clicked
    menu_requested = Signal()   # the ⋯ button was clicked
    pick_toggled = Signal(bool)  # pick mode: the chip was ticked / unticked

    _PREVIEW_W = 30             # width of the play-button zone
    _MENU_W = 26                # width of the ⋯ zone
    _PICK_W = 26                # width of the checkbox zone on the left

    def __init__(self, name: str, sub: str = "", *, dashed: bool = False, icon: str = "",
                 previewable: bool = False, has_menu: bool = False) -> None:
        super().__init__()
        self._name, self._sub, self._dashed, self._icon = name, sub, dashed, icon
        self._hover = False
        self._previewable, self._playing = previewable, False
        self._has_menu = has_menu
        self._pick_mode, self._picked = False, False
        self._press_zone = ""
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
        extra = ((22 if self._icon else 0) + (self._PREVIEW_W if self._previewable else 0)
                 + (self._MENU_W if self._has_menu else 0) + (self._PICK_W if self._pick_mode else 0))
        return QSize(int(w) + 36 + extra, 54 if self._sub else 42)

    # -- preview button ---------------------------------------------------
    def set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            self.update()

    def is_playing(self) -> bool:
        return self._playing

    # -- pick mode ----------------------------------------------------------
    def set_pick_mode(self, on: bool) -> None:
        if on != self._pick_mode:
            self._pick_mode, self._picked = on, False
            self.updateGeometry()
            self.update()

    def is_picked(self) -> bool:
        return self._picked

    def set_picked(self, picked: bool) -> None:
        if self._pick_mode and picked != self._picked:
            self._picked = picked
            self.update()
            self.pick_toggled.emit(picked)

    # -- zones --------------------------------------------------------------
    def _preview_rect(self) -> QRectF:
        s = 24.0
        return QRectF(self.width() - self._PREVIEW_W - 4 + (self._PREVIEW_W - s) / 2,
                      (self.height() - s) / 2, s, s)

    def _menu_rect(self) -> QRectF:
        right = self.width() - 4 - (self._PREVIEW_W if self._previewable else 0)
        return QRectF(right - self._MENU_W, (self.height() - 24) / 2, self._MENU_W, 24)

    def _zone(self, pos) -> str:
        x = pos.x()
        if self._previewable and x >= self.width() - self._PREVIEW_W - 6:
            return "preview"
        if self._has_menu and not self._pick_mode and self._menu_rect().adjusted(-2, -8, 2, 8).contains(pos):
            return "menu"
        return ""

    def mousePressEvent(self, ev) -> None:
        zone = self._zone(ev.position()) if ev.button() == Qt.MouseButton.LeftButton else ""
        if zone or self._pick_mode:
            self._press_zone = zone or "pick"
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._press_zone:
            zone, self._press_zone = self._press_zone, ""
            inside = self.rect().contains(ev.position().toPoint())
            if inside and zone == "preview" and self._zone(ev.position()) == "preview":
                self.preview.emit()
            elif inside and zone == "menu" and self._zone(ev.position()) == "menu":
                self.menu_requested.emit()
            elif inside and zone == "pick":
                self.set_picked(not self._picked)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def menu_anchor(self):
        """Global point under the ⋯ button, for placing its menu."""
        r = self._menu_rect()
        return self.mapToGlobal(QPoint(int(r.left()), int(r.bottom()) + 2))

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = self._picked if self._pick_mode else self.isChecked()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(theme.qcolor(theme.BG_CARD if not self._hover else theme.HOVER_FILL))
        if sel:
            p.setPen(QPen(theme.qcolor(theme.INK), 1.6))
        else:
            p.setPen(QPen(theme.qcolor(theme.BORDER_STRONG if self._dashed else theme.BORDER_DEFAULT), 1,
                          Qt.PenStyle.DashLine if self._dashed else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(r, theme.RADIUS_CONTROL + 2, theme.RADIUS_CONTROL + 2)
        x = 16.0
        if self._pick_mode:
            box = QRectF(14, (self.height() - 16) / 2, 16, 16)
            p.setPen(QPen(theme.qcolor(theme.INK if self._picked else theme.BORDER_STRONG), 1.2))
            p.setBrush(theme.qcolor(theme.INK if self._picked else theme.BG_CARD))
            p.drawRoundedRect(box, 4, 4)
            if self._picked:
                paint_icon(p, "check", box.adjusted(2, 2, -2, -2), theme.BG_CARD, 2.2)
            x += self._PICK_W
        if self._icon:
            paint_icon(p, self._icon, QRectF(x - 2, (self.height() - 14) / 2, 14, 14), theme.TEXT_SECONDARY, 2.0)
            x += 20.0
        right = 8 + (self._PREVIEW_W if self._previewable else 0) + (self._MENU_W if self._has_menu else 0)
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
        if self._has_menu and not self._pick_mode:
            mr = self._menu_rect()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(theme.TEXT_SECONDARY))
            for k in (-5.0, 0.0, 5.0):
                p.drawEllipse(QPointF(mr.center().x() + k, mr.center().y()), 1.6, 1.6)
        if self._previewable:
            pr = self._preview_rect()
            p.setPen(QPen(theme.qcolor(theme.INK if self._playing else theme.BORDER_STRONG), 1))
            p.setBrush(theme.qcolor(theme.INK if self._playing else theme.BG_CARD))
            p.drawEllipse(pr)
            paint_icon(p, "stop" if self._playing else "play", pr.adjusted(7, 7, -7, -7),
                       theme.BG_CARD if self._playing else theme.TEXT_SECONDARY, 1.6)
        p.end()
