"""Record or import a short clip of a voice, confirm what was said, save it."""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import audio_io, i18n, voices
from thundertalk.core.audio import AudioRecorder
from thundertalk.core.i18n import t
from thundertalk.core.tts import SR
from thundertalk.core.system_audio import mute_system_audio, stop_recording_and_restore
from thundertalk.ui import theme
from thundertalk.ui.studio.parts import PlayerBar, RoundButton
from thundertalk.ui.studio.workers import RefTranscribeWorker
from thundertalk.ui.widgets import Waveform

MAX_RECORD_SECONDS = 30


def _muted(text: str = "", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {size}px; background: transparent;")
    return lb


class CloneDialog(QDialog):
    audio_ready = Signal(object)

    def __init__(self, parent: Optional[QWidget], asr_engine, library: voices.VoiceLibrary,
                 microphone: str = "auto", mute_speakers: bool = True) -> None:
        super().__init__(parent)
        self._asr = asr_engine
        self._lib = library
        self._mic = None if microphone in ("", "auto") else microphone
        self._check: Optional[voices.ReferenceCheck] = None
        self._recorder: Optional[AudioRecorder] = None
        self._ducking_session = None
        self._starting = False
        self._mute_speakers = mute_speakers
        self.audio_ready.connect(self._start_capture, Qt.QueuedConnection)
        self._rec_t0 = 0.0
        self._ref_worker: Optional[RefTranscribeWorker] = None
        self._text_touched = False
        self.saved: Optional[voices.SavedVoice] = None

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 16)
        ly = QVBoxLayout()
        ly.setContentsMargins(30, 26, 30, 24)
        ly.setSpacing(14)
        outer.addLayout(ly)

        title = QLabel(t("studio.clone.title"))
        title.setFont(theme.font_heading(17))
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        ly.addWidget(title)
        ly.addWidget(_muted(t("studio.clone.intro"), 13))

        # source row: record / import
        src = QHBoxLayout()
        src.setSpacing(12)
        self._rec_btn = RoundButton("record", 44, color=theme.ERROR)
        self._rec_btn.setToolTip(t("studio.clone.record"))
        self._rec_btn.clicked.connect(self._toggle_record)
        src.addWidget(self._rec_btn)
        self._rec_label = QLabel(t("studio.clone.record"))
        self._rec_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600;"
                                      " background: transparent;")
        src.addWidget(self._rec_label)
        self._level = Waveform(bars=28, height=30)
        self._level.setMaximumWidth(150)
        self._level.set_idle(True)
        src.addWidget(self._level)
        src.addStretch()
        self._import_btn = theme.make_button(t("studio.clone.import"), "secondary", 36, font_px=13)
        self._import_btn.clicked.connect(self._import)
        src.addWidget(self._import_btn)
        ly.addLayout(src)

        sample = t("studio.clone.sample_zh") if i18n.LANG == "zh" else t("studio.clone.sample_en")
        self._sample = _muted(t("studio.clone.try_reading").format(text=sample), 12)
        ly.addWidget(self._sample)

        # prepared clip
        self._clip = QWidget()
        cly = QVBoxLayout(self._clip)
        cly.setContentsMargins(0, 0, 0, 0)
        cly.setSpacing(8)
        self._player = PlayerBar(with_save=False)
        cly.addWidget(self._player)
        self._clip_info = QLabel()
        self._clip_info.setWordWrap(True)
        self._clip_info.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
        cly.addWidget(self._clip_info)
        self._clip.setVisible(False)
        ly.addWidget(self._clip)

        # transcript of the clip
        self._said_lbl = QLabel(t("studio.clone.said"))
        self._said_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600;"
                                     " background: transparent;")
        ly.addWidget(self._said_lbl)
        self._said = QPlainTextEdit()
        self._said.setFixedHeight(76)
        self._said.setPlaceholderText(t("studio.clone.said_ph"))
        self._said.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: 8px 10px; font-size: 13px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {theme.INK}; }}")
        self._said.textChanged.connect(self._on_text_edit)
        ly.addWidget(self._said)
        self._said_hint = _muted(t("studio.clone.said_hint"), 11)
        ly.addWidget(self._said_hint)

        self._name = QLineEdit()
        self._name.setPlaceholderText(t("studio.clone.name_ph"))
        self._name.setStyleSheet(theme.INPUT_QSS)
        self._name.textChanged.connect(self._validate)
        ly.addWidget(self._name)

        self._msg = QLabel()
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color: {theme.ERROR}; font-size: 12px; background: transparent;")
        self._msg.setVisible(False)
        ly.addWidget(self._msg)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        cancel = theme.make_button(t("common.cancel"), "secondary", 36, font_px=13)
        cancel.clicked.connect(self.reject)
        cancel.setShortcut("Esc")
        row.addWidget(cancel)
        self._save = theme.make_button(t("studio.clone.save"), "primary", 36, font_px=13)
        self._save.clicked.connect(self._do_save)
        self._save.setEnabled(False)
        row.addWidget(self._save)
        ly.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._on_rec_tick)

    # ── background ────────────────────────────────────────────────────
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(12, 8, -12, -16)
        for i, a in enumerate((14, 8, 4)):
            sh = QPainterPath()
            sh.addRoundedRect(rect.adjusted(-i, 2 + i, i, 3 + i * 2), 10 + i, 10 + i)
            p.fillPath(sh, QColor(0, 0, 0, a))
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        p.fillPath(path, QColor(theme.BG_CARD))
        p.setPen(QPen(theme._BORDER_STRONG_C, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()

    def done(self, r: int) -> None:
        try:
            self._stop_recording(discard=True)
        except Exception as exc:
            print(f"[Audio] Studio recorder stop failed during close: {exc}")
        self._player.shutdown()
        if self._ref_worker is not None:
            self._ref_worker.cancel()
        super().done(r)

    # ── recording ─────────────────────────────────────────────────────
    def _toggle_record(self) -> None:
        if self._starting:
            self._stop_recording(discard=True)
            return
        if self._recorder is not None:
            self._finish_recording()
            return
        self._player.shutdown()
        self._starting = True
        if self._mute_speakers:
            try:
                session = mute_system_audio()
            except Exception as exc:
                self._starting = False
                self._show_error(t("overlay.audio_unavailable"))
                print(f"[Audio] Studio speaker mute startup failed: {exc}")
                return
            self._ducking_session = session
            session.ready.add_done_callback(lambda _future: self.audio_ready.emit(session))
        else:
            self._start_capture(None)

    def _start_capture(self, session) -> None:
        if not self._starting or session is not self._ducking_session:
            return
        try:
            if session is not None:
                session.ready.result()
            self._recorder = AudioRecorder(sample_rate=SR)
            self._recorder.start(self._mic)
        except Exception as exc:                      # no input device, permission, …
            self._stop_recording(discard=True)
            self._show_error(t("studio.clone.err_mic").format(msg=exc))
            return
        if not self._recorder.is_recording:
            self._stop_recording(discard=True)
            self._show_error(t("studio.clone.err_mic").format(msg="timeout"))
            return
        self._starting = False
        if session is not None:
            session.refresh()
        self._msg.setVisible(False)
        self._rec_t0 = time.monotonic()
        self._rec_btn.set_icon("stop")
        self._rec_label.setText("0:00")
        self._level.set_idle(False)
        self._import_btn.setEnabled(False)
        self._timer.start()

    def _on_rec_tick(self) -> None:
        if self._recorder is None:
            return
        el = time.monotonic() - self._rec_t0
        self._rec_label.setText(f"{int(el // 60)}:{int(el % 60):02d}")
        self._level.push(min(1.0, self._recorder.current_rms * 14))
        if el >= MAX_RECORD_SECONDS:
            self._finish_recording()

    def _stop_recording(self, discard: bool = False) -> Optional[np.ndarray]:
        rec, self._recorder = self._recorder, None
        session, self._ducking_session = self._ducking_session, None
        self._starting = False
        self._timer.stop()
        self._rec_btn.set_icon("record")
        self._rec_label.setText(t("studio.clone.record"))
        self._level.set_idle(True)
        self._import_btn.setEnabled(True)
        x = stop_recording_and_restore(rec, session)
        return None if discard else x

    def _finish_recording(self) -> None:
        try:
            x = self._stop_recording()
        except Exception as exc:
            self._show_error(t("studio.clone.err_mic").format(msg=exc))
            return
        if x is None or len(x) < SR:
            self._show_error(t("studio.clone.err_short_rec"))
            return
        if float(np.max(np.abs(x))) < 1e-3:
            self._show_error(t("studio.clone.err_silent"))
            return
        self._set_audio(x)

    # ── importing ─────────────────────────────────────────────────────
    def _import(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(audio_io.AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, t("studio.clone.import"), "",
                                              f"{t('studio.drop.filter')} ({exts})")
        if not path:
            return
        try:
            x = audio_io.decode_audio(path, SR)
        except Exception as exc:
            self._show_error(t("studio.err.decode").format(msg=exc))
            return
        self._set_audio(x)

    # ── prepared clip ─────────────────────────────────────────────────
    def _set_audio(self, x: np.ndarray) -> None:
        self._msg.setVisible(False)
        chk = voices.prepare_reference(x, SR)
        self._check = chk
        self._player.set_audio(chk.audio, SR)
        self._clip.setVisible(True)
        notes = [t(f"studio.clone.warn.{w}") for w in chk.warnings]
        head = t("studio.clone.length").format(sec=f"{chk.duration:.1f}")
        self._clip_info.setText(head + ("   ·   " + "   ·   ".join(notes) if notes else ""))
        color = theme.ERROR if not chk.ok else (theme.WARNING if notes else theme.SUCCESS)
        self._clip_info.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
        self._text_touched = False
        if chk.ok:
            self._auto_transcribe(chk.audio)
        self._validate()
        self.adjustSize()

    def _auto_transcribe(self, audio24: np.ndarray) -> None:
        if self._asr is None or not getattr(self._asr, "is_loaded", False):
            self._said_hint.setText(t("studio.clone.said_hint"))
            return
        x16 = audio_io.resample(audio24, SR, 16000)
        if self._ref_worker is not None:
            self._ref_worker.cancel()
        self._said_hint.setText(t("studio.clone.transcribing"))
        w = RefTranscribeWorker(x16, self._asr)
        self._ref_worker = w
        w.done.connect(lambda text, w=w: self._on_ref_text(text) if w is self._ref_worker else None)
        w.error.connect(lambda _c: self._said_hint.setText(t("studio.clone.said_hint")))
        w.start()

    def _on_ref_text(self, text: str) -> None:
        self._said_hint.setText(t("studio.clone.said_hint"))
        if not self._text_touched and text:
            self._said.blockSignals(True)
            self._said.setPlainText(text)
            self._said.blockSignals(False)
        self._validate()

    def _on_text_edit(self) -> None:
        self._text_touched = True
        self._validate()

    # ── save ──────────────────────────────────────────────────────────
    def _validate(self) -> None:
        ok = bool(self._check and self._check.ok and self._said.toPlainText().strip()
                  and self._name.text().strip())
        self._save.setEnabled(ok)

    def _show_error(self, msg: str) -> None:
        self._msg.setText(msg)
        self._msg.setVisible(True)

    def _do_save(self) -> None:
        if not (self._check and self._check.ok):
            return
        try:
            self.saved = self._lib.add(self._name.text().strip(), self._check.audio,
                                       self._said.toPlainText().strip())
        except OSError as exc:
            self._show_error(t("studio.err.other").format(msg=exc))
            return
        self.accept()
