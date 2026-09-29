"""Studio ▸ Transcribe: drop a recording, get text — fast, or with speakers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import audio_io
from thundertalk.core.i18n import t
from thundertalk.core.models import BUILTIN_MODELS, is_downloaded
from thundertalk.core.transcribe import Transcript, fmt_time
from thundertalk.ui import theme
from thundertalk.ui.studio.parts import DropZone, TranscriptView, fmt_seconds
from thundertalk.ui.studio.workers import TranscribeWorker, friendly_error
from thundertalk.ui.widgets import Rule, SegmentedControl, ThinProgress

MOSS_ID = "moss-transcribe-diarize-mlx"
EXPORTS = [("txt", "studio.export.txt", ".txt"), ("md", "studio.export.md", ".md"),
           ("srt", "studio.export.srt", ".srt"), ("vtt", "studio.export.vtt", ".vtt"),
           ("json", "studio.export.json", ".json")]


def _muted(text: str = "", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {size}px; background: transparent;")
    return lb


class TranscribeTab(QWidget):
    navigate = Signal(str)
    toast = Signal(str, str)              # message, kind

    def __init__(self) -> None:
        super().__init__()
        self._engine = None
        self._worker: Optional[TranscribeWorker] = None
        self._dl_worker = None
        self._transcript: Optional[Transcript] = None
        self._path = ""
        self._t0 = 0.0
        self._phase = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # ── input card ────────────────────────────────────────────────
        card = theme.make_card()
        cly = QVBoxLayout(card)
        cly.setContentsMargins(24, 22, 24, 22)
        cly.setSpacing(14)

        self._drop = DropZone()
        self._drop.file_chosen.connect(self._on_file)
        cly.addWidget(self._drop)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        self._mode = SegmentedControl(self._mode_options(), "fast")
        self._mode.changed.connect(lambda _k: self._refresh())
        mode_row.addWidget(self._mode)
        self._mode_desc = _muted()
        mode_row.addWidget(self._mode_desc, 1)
        cly.addLayout(mode_row)

        # notice: nothing to transcribe with / speaker model not installed
        self._notice = QWidget()
        nly = QHBoxLayout(self._notice)
        nly.setContentsMargins(0, 0, 0, 0)
        nly.setSpacing(12)
        self._notice_text = _muted(size=12)
        nly.addWidget(self._notice_text, 1)
        self._notice_btn = theme.make_button("", "secondary", 32, font_px=12)
        self._notice_btn.clicked.connect(self._on_notice_action)
        nly.addWidget(self._notice_btn)
        cly.addWidget(self._notice)

        act = QHBoxLayout()
        act.setSpacing(10)
        self._go = theme.accent_button(t("studio.transcribe.go"), 40)
        self._go.clicked.connect(self._start)
        act.addWidget(self._go)
        self._cancel = theme.make_button(t("common.cancel"), "secondary", 40)
        self._cancel.clicked.connect(self._on_cancel)
        self._cancel.setVisible(False)
        act.addWidget(self._cancel)
        self._status = _muted()
        act.addWidget(self._status, 1)
        cly.addLayout(act)
        self._bar = ThinProgress(4)
        self._bar.setVisible(False)
        cly.addWidget(self._bar)
        root.addWidget(card)

        # ── result card ───────────────────────────────────────────────
        self._result = theme.make_card()
        rly = QVBoxLayout(self._result)
        rly.setContentsMargins(24, 20, 24, 12)
        rly.setSpacing(10)
        self._stats = _muted(size=12)
        rly.addWidget(self._stats)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._time_toggle = SegmentedControl(self._time_options(), "time")
        self._time_toggle.changed.connect(lambda k: self._view.set_timestamps(k == "time"))
        head.addWidget(self._time_toggle)
        head.addStretch()
        self._copy_btn = theme.make_button(t("studio.copy"), "secondary", 34, font_px=12)
        self._copy_btn.clicked.connect(self._copy)
        head.addWidget(self._copy_btn)
        self._export_btn = theme.make_button(t("studio.export"), "secondary", 34, font_px=12)
        self._export_btn.clicked.connect(self._export_menu)
        head.addWidget(self._export_btn)
        rly.addLayout(head)
        rly.addWidget(Rule())
        self._view = TranscriptView()
        self._view.speaker_clicked.connect(self._rename_speaker)
        rly.addWidget(self._view)
        self._result.setVisible(False)
        root.addWidget(self._result)
        root.addStretch()

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)
        self._refresh()

    # ── public ────────────────────────────────────────────────────────
    def set_engine(self, engine) -> None:
        self._engine = engine
        self._refresh()

    def refresh(self) -> None:
        self._refresh()

    def busy(self) -> bool:
        return self._worker is not None or self._dl_worker is not None

    def shutdown(self) -> None:
        """App is quitting: cancel and wait for any running job."""
        for w in (self._worker, self._dl_worker):
            if w is not None:
                w.cancel() if hasattr(w, "cancel") else None
                w.wait(8000)

    def retranslate(self) -> None:
        self._mode.set_options(self._mode_options())
        self._time_toggle.set_options(self._time_options())
        self._drop.retranslate()
        self._go.setText(t("studio.transcribe.go"))
        self._cancel.setText(t("common.cancel"))
        self._copy_btn.setText(t("studio.copy"))
        self._export_btn.setText(t("studio.export"))
        self._refresh()

    # ── state ─────────────────────────────────────────────────────────
    @staticmethod
    def _mode_options() -> list[tuple[str, str]]:
        return [("fast", t("studio.mode.fast")), ("speakers", t("studio.mode.speakers"))]

    @staticmethod
    def _time_options() -> list[tuple[str, str]]:
        return [("time", t("studio.timestamps")), ("plain", t("studio.text_only"))]

    def _speakers_mode(self) -> bool:
        return self._mode.current() == "speakers"

    def _moss_ready(self) -> bool:
        return is_downloaded(MOSS_ID)

    def _engine_ready(self) -> bool:
        return bool(self._engine is not None and getattr(self._engine, "is_loaded", False))

    def _refresh(self) -> None:
        busy = self.busy()
        sp = self._speakers_mode()
        if sp:
            self._mode_desc.setText(t("studio.mode.speakers.desc"))
        else:
            model = getattr(self._engine, "current_model", "") or "—"
            self._mode_desc.setText(t("studio.mode.fast.desc").format(model=model))

        notice, action = "", ""
        if sp and not self._moss_ready():
            info = next((m for m in BUILTIN_MODELS if m.id == MOSS_ID), None)
            size = f"{info.size_mb / 1000:.1f} GB" if info else ""
            notice = t("studio.moss.missing").format(size=size)
            action = t("studio.download")
        elif not sp and not self._engine_ready():
            notice, action = t("studio.err.no_model"), t("studio.choose_model")
        self._notice_text.setText(notice)
        self._notice_btn.setText(action)
        self._notice.setVisible(bool(notice) and self._dl_worker is None)
        if self._dl_worker is not None:
            self._notice.setVisible(False)

        ready = bool(self._path) and (self._moss_ready() if sp else self._engine_ready())
        self._go.setEnabled(ready and not busy)
        self._mode.setEnabled(not busy)
        self._drop.setEnabled(not busy)

    # ── input ─────────────────────────────────────────────────────────
    def _on_file(self, path: str) -> None:
        if self.busy():
            return
        try:
            dur = audio_io.audio_duration(path)
        except Exception:
            dur = 0.0
        self._path = path
        self._drop.set_file(path, dur)
        self._status.setText("")
        self._refresh()

    def load_file(self, path: str) -> None:
        """Programmatic entry (drag onto the window, tests)."""
        self._on_file(path)

    # ── running ───────────────────────────────────────────────────────
    def _start(self) -> None:
        if self.busy() or not self._path:
            return
        sp = self._speakers_mode()
        self._worker = TranscribeWorker(self._path, self._engine, sp)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self._t0 = time.monotonic()
        self._eta_base, self._eta_done, self._eta_total = None, 0, 0
        self._phase = t("studio.progress.decode")
        self._go.setVisible(False)
        self._cancel.setVisible(True)
        self._cancel.setEnabled(True)
        self._cancel.setText(t("common.cancel"))
        self._bar.setVisible(True)
        self._bar.set_indeterminate(True)
        self._result.setVisible(False)
        self._tick.start()
        self._on_tick()
        self._refresh()
        self._worker.start()

    def _on_progress(self, pct: int, msg: str) -> None:
        if "/" in msg:
            i, n = msg.split("/", 1)
            self._phase = t("studio.progress.part").format(i=i, n=n)
            self._note_part(int(i), int(n))
        elif msg in ("decode", "load_moss", "diarize"):
            self._phase = t(f"studio.progress.{msg}")
        if msg == "diarize" or msg == "load_moss" or msg == "decode":
            self._bar.set_indeterminate(True)
        elif pct >= 0:
            self._bar.set_value(pct)

    def _note_part(self, i: int, n: int, now: Optional[float] = None) -> None:
        """Part ``i`` of ``n`` (1-based) just started, so ``i - 1`` are done."""
        now = time.monotonic() if now is None else now
        if i == 2:
            self._eta_base = now                  # the first part includes warm-up
        self._eta_last = now
        self._eta_done, self._eta_total = i - 1, n

    def eta_seconds(self, now: Optional[float] = None) -> Optional[float]:
        """Remaining time, once two parts have finished; None before that."""
        base = getattr(self, "_eta_base", None)
        done, total = getattr(self, "_eta_done", 0), getattr(self, "_eta_total", 0)
        if base is None or done < 2 or total <= done:
            return None
        now = time.monotonic() if now is None else now
        per_part = (self._eta_last - base) / (done - 1)
        return max(0.0, per_part * (total - done) - (now - self._eta_last))

    def _on_tick(self) -> None:
        elapsed = time.monotonic() - self._t0
        text = f"{self._phase}   {fmt_time(elapsed)}"
        eta = self.eta_seconds()
        if eta is not None:
            text += "   " + t("studio.progress.eta").format(t=fmt_time(eta + 0.5))
        self._status.setText(text)

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._cancel.setEnabled(False)
            self._cancel.setText(t("studio.cancelling"))
            self._phase = t("studio.cancelling_hint")

    def _on_done(self, tr: Transcript) -> None:
        self._transcript = tr
        self._view.set_transcript(tr)
        self._view.set_timestamps(self._time_toggle.current() == "time")
        bits = [t("studio.stats").format(dur=fmt_time(tr.duration), took=fmt_seconds(tr.seconds_taken),
                                         x=f"{tr.realtime_factor:.0f}" if tr.realtime_factor >= 10
                                         else f"{tr.realtime_factor:.1f}")]
        if tr.has_speakers:
            bits.append(t("studio.speaker_count").format(n=len(tr.speakers)))
        bits.append(tr.engine)
        self._stats.setText("  ·  ".join(bits))
        self._result.setVisible(True)
        self._status.setText("")

    def _on_error(self, code: str) -> None:
        self._status.setText("")
        self.toast.emit(friendly_error(code), "error")

    def _on_cancelled(self) -> None:
        self._status.setText(t("studio.cancelled"))

    def _on_finished(self) -> None:
        self._tick.stop()
        self._worker = None
        self._bar.setVisible(False)
        self._bar.set_indeterminate(False)
        self._cancel.setVisible(False)
        self._go.setVisible(True)
        self._refresh()

    # ── notice action (download speaker model / choose a model) ───────
    def _on_notice_action(self) -> None:
        if self._speakers_mode() and not self._moss_ready():
            self._download_moss()
        else:
            self.navigate.emit("models")

    def _download_moss(self) -> None:
        from thundertalk.ui.pages.models_page import DownloadWorker
        info = next((m for m in BUILTIN_MODELS if m.id == MOSS_ID), None)
        if info is None or self._dl_worker is not None:
            return
        w = DownloadWorker(info)
        self._dl_worker = w
        w.progress.connect(self._on_dl_progress)
        w.done.connect(lambda _id: self.toast.emit(t("studio.moss.ready"), "success"))
        w.error.connect(lambda m: self.toast.emit(t("studio.err.other").format(msg=m), "error"))
        w.finished.connect(self._on_dl_finished)
        self._cancel.setVisible(True)
        self._cancel.setEnabled(True)
        self._cancel.clicked.disconnect()
        self._cancel.clicked.connect(w.cancel)
        self._go.setVisible(False)
        self._bar.setVisible(True)
        self._bar.set_indeterminate(True)
        self._phase = t("studio.moss.downloading")
        self._t0 = time.monotonic()
        self._tick.start()
        self._refresh()
        w.start()

    def _on_dl_progress(self, pct: int, msg: str) -> None:
        self._phase = f"{t('studio.moss.downloading')}  {msg}"
        if pct >= 0:
            self._bar.set_value(pct)
        else:
            self._bar.set_indeterminate(True)

    def _on_dl_finished(self) -> None:
        self._tick.stop()
        self._dl_worker = None
        self._status.setText("")
        self._bar.setVisible(False)
        self._cancel.setVisible(False)
        self._cancel.clicked.disconnect()
        self._cancel.clicked.connect(self._on_cancel)
        self._go.setVisible(True)
        self._refresh()

    # ── result actions ────────────────────────────────────────────────
    def _copy(self) -> None:
        if self._transcript is None:
            return
        QApplication.clipboard().setText(
            self._transcript.to_text(timestamps=self._time_toggle.current() == "time"))
        self.toast.emit(t("studio.copied"), "copy")

    def _export_menu(self) -> None:
        if self._transcript is None:
            return
        menu = QMenu(self)
        for fmt, key, ext in EXPORTS:
            menu.addAction(t(key), lambda f=fmt, e=ext: self._export(f, e))
        menu.exec(self._export_btn.mapToGlobal(self._export_btn.rect().bottomLeft()))

    def _export(self, fmt: str, ext: str) -> None:
        if self._transcript is None:
            return
        stem = Path(self._path).stem if self._path else "transcript"
        path, _ = QFileDialog.getSaveFileName(self, t("studio.export"), str(Path.home() / f"{stem}{ext}"),
                                              f"*{ext}")
        if not path:
            return
        try:
            Path(path).write_text(self._transcript.export(fmt), encoding="utf-8")
            self.toast.emit(t("studio.saved").format(name=Path(path).name), "success")
        except OSError as exc:
            self.toast.emit(t("studio.err.other").format(msg=str(exc)), "error")

    def _rename_speaker(self, speaker: str) -> None:
        if self._transcript is None:
            return
        cur = self._transcript.label(speaker)
        name, ok = QInputDialog.getText(self, t("studio.rename_speaker"),
                                        t("studio.rename_speaker.prompt").format(name=cur), text=cur)
        if ok:
            self._transcript.rename_speaker(speaker, name)
            self._view.refresh_speaker_names()
