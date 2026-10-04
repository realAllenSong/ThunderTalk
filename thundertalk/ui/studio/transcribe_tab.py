"""Studio ▸ Transcribe: drop a recording or paste a video link, get text — fast,
or with speakers. Several files/links at once form a queue that is worked
through one item at a time; a transcript of a video can be burned into a copy
of it as subtitles."""

from __future__ import annotations

import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import audio_io, burn, links
from thundertalk.core.i18n import t
from thundertalk.core.models import BUILTIN_MODELS, is_downloaded
from thundertalk.core.transcribe import EXPORT_EXTS, Transcript, fmt_time
from thundertalk.ui import theme
from thundertalk.ui.studio.parts import DropZone, QueueRow, TranscriptView, fmt_seconds, fmt_size
from thundertalk.ui.studio.workers import (
    BatchItem,
    BatchWorker,
    BurnWorker,
    NotesWorker,
    TranscribeWorker,
    friendly_error,
)
from thundertalk.ui.widgets import Rule, SegmentedControl, ThinProgress

MOSS_ID = "moss-transcribe-diarize-mlx"
EXPORTS = [("txt", "studio.export.txt", ".txt"), ("md", "studio.export.md", ".md"),
           ("srt", "studio.export.srt", ".srt"), ("vtt", "studio.export.vtt", ".vtt"),
           ("json", "studio.export.json", ".json")]
FFMPEG_HINT = "brew install ffmpeg"


def _muted(text: str = "", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {size}px; background: transparent;")
    return lb


def partial_text(tr: Transcript) -> str:
    return t("studio.link.partial").format(got=fmt_time(tr.duration), total=fmt_time(tr.expected_duration))


def phase_text(msg: str) -> str:
    """A progress message from the workers as a sentence ("" if it has none)."""
    if msg.startswith("notes:"):
        if msg == "notes:merge":
            return t("studio.notes.merge")
        if msg == "notes:done":
            return t("studio.notes.done")
        _, i, n = msg.split(":")
        return t("studio.notes.part").format(i=i, n=n)
    if msg == "fetch":
        return t("studio.progress.fetch")
    if msg.startswith("download:"):
        _, done, total = msg.split(":")
        if int(total):
            return t("studio.progress.download").format(done=fmt_size(int(done)), total=fmt_size(int(total)))
        return t("studio.progress.download_nosize").format(done=fmt_size(int(done)))
    if "/" in msg:
        i, n = msg.split("/", 1)
        return t("studio.progress.part").format(i=i, n=n)
    if msg in ("decode", "load_moss", "diarize", "probe", "render", "yield"):
        return t(f"studio.progress.{msg}")
    return ""


class TranscribeTab(QWidget):
    navigate = Signal(str)
    toast = Signal(str, str)              # message, kind

    def __init__(self) -> None:
        super().__init__()
        self._engine = None
        self._worker: Optional[TranscribeWorker] = None
        self._dl_worker = None
        self._batch: Optional[BatchWorker] = None
        self._burn_worker: Optional[BurnWorker] = None
        self._notes_worker: Optional[NotesWorker] = None
        self._cleanup_settings = None
        self._transcript: Optional[Transcript] = None
        self._path = ""
        self._url = ""                    # single input that is a web link
        self._result_src = ""             # local file the shown transcript came from
        self._rows: list[QueueRow] = []   # non-empty = queue mode
        self._run_rows: list[QueueRow] = []
        self._batch_ok = 0
        self._out_dir = ""
        self._formats = ["txt", "srt"]
        self._t0 = 0.0
        self._phase = ""
        links.cleanup_stale()

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
        self._drop.files_chosen.connect(self.add_files)
        cly.addWidget(self._drop)

        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        self._link = QLineEdit()
        self._link.setStyleSheet(theme.INPUT_QSS)
        self._link.setPlaceholderText(t("studio.link.placeholder"))
        self._link.setClearButtonEnabled(True)
        self._link.returnPressed.connect(self._on_add_link)
        link_row.addWidget(self._link, 1)
        self._link_btn = theme.make_button(t("studio.link.add"), "secondary", 36)
        self._link_btn.clicked.connect(self._on_add_link)
        link_row.addWidget(self._link_btn)
        cly.addLayout(link_row)

        # queue (shown once there are two or more items)
        self._queue = QWidget()
        self._queue.setStyleSheet("background: transparent;")
        qly = QVBoxLayout(self._queue)
        qly.setContentsMargins(0, 0, 0, 0)
        qly.setSpacing(6)
        qhead = QHBoxLayout()
        self._queue_title = QLabel()
        self._queue_title.setFont(theme.font(13, bold=True))
        self._queue_title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        qhead.addWidget(self._queue_title)
        qhead.addStretch()
        self._clear_btn = theme.make_button(t("studio.batch.clear"), "ghost", 28, font_px=12)
        self._clear_btn.clicked.connect(self.clear_queue)
        qhead.addWidget(self._clear_btn)
        qly.addLayout(qhead)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(0)
        qly.addLayout(self._rows_box)
        qly.addWidget(Rule())
        opts = QHBoxLayout()
        opts.setSpacing(10)
        self._dest_label = _muted(t("studio.batch.save_to"))
        self._dest_label.setWordWrap(False)
        opts.addWidget(self._dest_label)
        self._dest = SegmentedControl(self._dest_options(), "next")
        self._dest.changed.connect(self._on_dest)
        opts.addWidget(self._dest)
        self._dest_desc = _muted()
        opts.addWidget(self._dest_desc, 1)
        self._formats_btn = theme.make_button("", "secondary", 32, font_px=12)
        self._formats_btn.clicked.connect(self._formats_menu)
        opts.addWidget(self._formats_btn)
        qly.addLayout(opts)
        self._queue_notes = QCheckBox(t("studio.notes.queue"))
        self._queue_notes.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; background: transparent;")
        qly.addWidget(self._queue_notes)
        self._queue.setVisible(False)
        cly.addWidget(self._queue)

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
        self._summary_btn = theme.make_button(t("studio.summary"), "secondary", 34, font_px=12)
        self._summary_btn.clicked.connect(self._summarize)
        head.addWidget(self._summary_btn)
        self._burn_btn = theme.make_button(t("studio.burn"), "secondary", 34, font_px=12)
        self._burn_btn.clicked.connect(self._burn_menu)
        self._burn_btn.setVisible(False)
        head.addWidget(self._burn_btn)
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
        self._notes_hint = _muted()
        root.addWidget(self._notes_hint)
        self._notes_settings = theme.make_button(t("studio.notes.settings"), "ghost", 32, font_px=12)
        self._notes_settings.clicked.connect(lambda: self.navigate.emit("settings.cleanup"))
        root.addWidget(self._notes_settings)
        self._notes_card = theme.make_card()
        nly = QVBoxLayout(self._notes_card)
        nly.setContentsMargins(24, 20, 24, 20)
        nhead = QHBoxLayout()
        self._notes_title = QLabel(t("studio.summary"))
        self._notes_title.setFont(theme.font(14, bold=True))
        self._notes_title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        nhead.addWidget(self._notes_title)
        nhead.addStretch()
        self._notes_copy = theme.make_button(t("studio.notes.copy"), "secondary", 34, font_px=12)
        self._notes_copy.clicked.connect(self._copy_notes)
        nhead.addWidget(self._notes_copy)
        self._notes_save = theme.make_button(t("studio.notes.save"), "secondary", 34, font_px=12)
        self._notes_save.clicked.connect(self._save_notes)
        nhead.addWidget(self._notes_save)
        nly.addLayout(nhead)
        self._notes_view = QTextBrowser()
        self._notes_view.setOpenLinks(False)
        self._notes_view.setOpenExternalLinks(False)
        self._notes_view.setStyleSheet(
            f"QTextBrowser {{ color: {theme.TEXT_PRIMARY}; background: transparent; border: none; font-size: 14px; }}")
        self._notes_view.setMinimumHeight(300)
        nly.addWidget(self._notes_view)
        self._notes_card.hide()
        root.addWidget(self._notes_card)
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

    def set_cleanup_settings(self, settings) -> None:
        self._cleanup_settings = settings
        settings.providers_changed.connect(lambda _p: self._refresh())
        settings.provider_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        settings.model_combo.currentTextChanged.connect(lambda _s: self._refresh())
        self._refresh()

    def _notes_provider(self):
        settings = self._cleanup_settings
        provider = settings.chosen_provider() if settings else None
        return provider, settings.chosen_model(provider) if provider else ""

    def refresh(self) -> None:
        self._refresh()

    def busy(self) -> bool:
        return any(w is not None for w in (self._worker, self._dl_worker, self._batch,
                                          self._burn_worker, self._notes_worker))

    def shutdown(self) -> None:
        """App is quitting: cancel and wait for any running job."""
        for w in (self._worker, self._dl_worker, self._batch, self._burn_worker, self._notes_worker):
            if w is not None:
                w.cancel() if hasattr(w, "cancel") else None
                w.wait()                 # HTTP completion may need its bounded timeout to return

    def retranslate(self) -> None:
        self._mode.set_options(self._mode_options())
        self._time_toggle.set_options(self._time_options())
        self._dest.set_options(self._dest_options())
        self._drop.retranslate()
        self._link.setPlaceholderText(t("studio.link.placeholder"))
        self._link_btn.setText(t("studio.link.add"))
        self._clear_btn.setText(t("studio.batch.clear"))
        self._dest_label.setText(t("studio.batch.save_to"))
        for row in self._rows:
            row.retranslate()
        self._cancel.setText(t("studio.batch.cancel_all") if self._batch is not None else t("common.cancel"))
        self._summary_btn.setText(t("studio.summary"))
        self._queue_notes.setText(t("studio.notes.queue"))
        self._notes_settings.setText(t("studio.notes.settings"))
        self._notes_title.setText(t("studio.summary"))
        self._notes_copy.setText(t("studio.notes.copy"))
        self._notes_save.setText(t("studio.notes.save"))
        self._burn_btn.setText(t("studio.burn"))
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

    @staticmethod
    def _dest_options() -> list[tuple[str, str]]:
        return [("next", t("studio.batch.next_to")), ("folder", t("studio.batch.folder"))]

    def _speakers_mode(self) -> bool:
        return self._mode.current() == "speakers"

    def _moss_ready(self) -> bool:
        return is_downloaded(MOSS_ID)

    def _engine_ready(self) -> bool:
        return bool(self._engine is not None and getattr(self._engine, "is_loaded", False))

    def queue_mode(self) -> bool:
        return bool(self._rows)

    def _runnable_rows(self) -> list[QueueRow]:
        return [r for r in self._rows if r.state != "done"]

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

        model_ok = self._moss_ready() if sp else self._engine_ready()
        queue = self.queue_mode()
        if queue:
            n = len(self._runnable_rows())
            self._go.setText(t("studio.batch.go").format(n=n))
            ready = n > 0 and model_ok
        else:
            self._go.setText(t("studio.transcribe.go"))
            ready = bool(self._path or self._url) and model_ok
        self._go.setEnabled(ready and not busy)
        self._summary_btn.setEnabled(not busy and self._transcript is not None)
        self._burn_btn.setEnabled(not busy)
        self._queue_notes.setEnabled(not busy)
        provider, model = self._notes_provider()
        self._queue_notes.setToolTip(t("studio.notes.queue_hint"))
        relevant = self._transcript is not None or queue
        self._notes_hint.setVisible(relevant)
        self._notes_settings.setVisible(relevant)
        self._notes_hint.setText(
            t("studio.notes.no_provider") if provider is None else
            t("studio.notes.local" if provider.local else "studio.notes.cloud").format(
                provider=provider.display_name, model=model))
        self._summary_btn.setToolTip(self._notes_hint.text())
        self._mode.setEnabled(not busy)
        can_add = not busy or self._batch is not None
        self._drop.setEnabled(can_add)
        self._link.setEnabled(can_add)
        self._link_btn.setEnabled(can_add)

        self._queue.setVisible(queue)
        self._queue_title.setText(t("studio.batch.title").format(n=len(self._rows)))
        self._clear_btn.setEnabled(not busy)
        self._dest.setEnabled(self._batch is None)
        self._formats_btn.setEnabled(self._batch is None)
        self._formats_btn.setText(t("studio.batch.formats").format(
            list=" · ".join(f.upper() for f in self._formats)))
        if self._dest.current() == "folder" and self._out_dir:
            self._dest_desc.setText(self._out_dir.replace(str(Path.home()), "~"))
        else:
            self._dest_desc.setText(t("studio.batch.next_to_hint")
                                    if any(r.item.is_url for r in self._rows) else "")

    # ── input ─────────────────────────────────────────────────────────
    def _on_file(self, path: str) -> None:
        if self.queue_mode() or self._batch is not None:
            self._enqueue([BatchItem(path)])
            return
        if self.busy():
            return
        try:
            dur = audio_io.audio_duration(path)
        except Exception:
            dur = 0.0
        self._path, self._url = path, ""
        self._drop.set_file(path, dur)
        self._status.setText("")
        self._refresh()

    def load_file(self, path: str) -> None:
        """Programmatic entry (drag onto the window, tests)."""
        self._on_file(path)

    def add_files(self, paths: list[str]) -> None:
        """Several files at once start (or extend) the queue."""
        if self.busy() and self._batch is None:
            return
        if len(paths) == 1 and not self.queue_mode():
            self._on_file(paths[0])
            return
        if not self.queue_mode():
            self._clear_single()
        self._enqueue([BatchItem(p) for p in paths])

    def add_links(self, text: str) -> bool:
        """Links in ``text``: the single input if nothing is chosen yet,
        otherwise they join (or start) the queue. False if there are none."""
        urls = links.find_urls(text)
        if not urls:
            return False
        if self.busy() and self._batch is None:
            return True
        if not self.queue_mode() and len(urls) == 1 and not (self._path or self._url):
            self._url, self._path = urls[0], ""
            self._drop.set_link(urls[0])
            self._status.setText("")
            self._refresh()
            return True
        items = [BatchItem(u, True) for u in urls]
        if not self.queue_mode():
            current = [BatchItem(self._url, True)] if self._url else [BatchItem(self._path)] if self._path else []
            self._clear_single()
            items = current + items
        self._enqueue(items)
        return True

    def _on_add_link(self) -> None:
        text = self._link.text().strip()
        if not text:
            return
        if self.add_links(text):
            self._link.clear()
        else:
            self.toast.emit(t("studio.link.not_link"), "warn")

    def _clear_single(self) -> None:
        self._path, self._url = "", ""
        self._drop.clear()

    # ── queue ─────────────────────────────────────────────────────────
    def _enqueue(self, items: list[BatchItem]) -> None:
        for item in items:
            row = QueueRow(item.source)
            row.item, row.transcript = item, None
            row.notes_error = ""
            row.action.connect(lambda a, r=row: self._on_row_action(r, a))
            if self._rows:
                row.layout().insertWidget(0, Rule())
            self._rows.append(row)
            self._rows_box.addWidget(row)
            if self._batch is not None:
                self._run_rows.append(row)
                self._batch.add(item)
        self._refresh()

    def clear_queue(self) -> None:
        if self.busy():
            return
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()
        self._run_rows.clear()
        self._refresh()

    def _remove_row(self, row: QueueRow) -> None:
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        if len(self._rows) == 1:                      # back to the single-item flow
            last = self._rows[0]
            self.clear_queue()
            if last.item.is_url:
                self.add_links(last.item.source)
            else:
                self._on_file(last.item.source)
        self._refresh()

    def _on_row_action(self, row: QueueRow, action: str) -> None:
        if action == "view" and row.transcript is not None and self._notes_worker is None:
            self._show_result(row.transcript, "" if row.item.is_url else row.item.source)
        elif self._batch is not None and row in self._run_rows:
            self._batch.cancel_item(self._run_rows.index(row))
            if row.state == "running":
                row.set_state("running", t("studio.cancelling"))
        elif action == "remove" and self._batch is None:
            self._remove_row(row)

    def _on_dest(self, key: str) -> None:
        if key == "folder":
            d = QFileDialog.getExistingDirectory(self, t("studio.batch.save_to"), self._out_dir or str(Path.home()))
            if d:
                self._out_dir = d
            elif not self._out_dir:
                self._dest.set_current("next")
        self._refresh()

    def _formats_menu(self) -> None:
        menu = QMenu(self)
        for fmt, key, _ext in EXPORTS:
            a = menu.addAction(t(key))
            a.setCheckable(True)
            a.setChecked(fmt in self._formats)
            a.toggled.connect(lambda on, f=fmt: self._toggle_format(f, on))
        menu.exec(self._formats_btn.mapToGlobal(self._formats_btn.rect().bottomLeft()))

    def _toggle_format(self, fmt: str, on: bool) -> None:
        if on and fmt not in self._formats:
            self._formats.append(fmt)
        elif not on and fmt in self._formats and len(self._formats) > 1:
            self._formats.remove(fmt)
        order = list(EXPORT_EXTS)
        self._formats.sort(key=order.index)
        self._refresh()

    # ── running ───────────────────────────────────────────────────────
    def _begin(self, cancel_text: str) -> None:
        self._t0 = time.monotonic()
        self._go.setVisible(False)
        self._cancel.setVisible(True)
        self._cancel.setEnabled(True)
        self._cancel.setText(cancel_text)
        self._bar.setVisible(True)
        self._bar.set_indeterminate(True)
        self._tick.start()
        self._on_tick()
        self._refresh()

    def _end(self) -> None:
        self._tick.stop()
        self._bar.setVisible(False)
        self._bar.set_indeterminate(False)
        self._cancel.setVisible(False)
        self._go.setVisible(True)
        self._refresh()

    def _start(self) -> None:
        if self.busy():
            return
        if self.queue_mode():
            self._start_batch()
            return
        if not (self._path or self._url):
            return
        sp = self._speakers_mode()
        self._worker = TranscribeWorker(self._path, self._engine, sp, url=self._url)
        self._worker.progress.connect(self._on_progress)
        self._worker.titled.connect(self._on_titled)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self._result_src = self._path
        self._eta_base, self._eta_done, self._eta_total = None, 0, 0
        self._phase = t("studio.progress.fetch") if self._url else t("studio.progress.decode")
        self._result.setVisible(False)
        self._notes_card.setVisible(False)
        self._begin(t("common.cancel"))
        self._worker.start()

    def _on_titled(self, title: str) -> None:
        if self._url:
            self._drop.set_link(self._url, title)

    def _on_progress(self, pct: int, msg: str) -> None:
        if msg.startswith("notes:"):
            self._phase = phase_text(msg)
            self._bar.set_value(pct)
            self._on_tick()
            return
        if msg == "fetch" or msg.startswith("download:"):
            self._phase = phase_text(msg)
            if pct >= 0:
                self._bar.set_value(pct)
            else:
                self._bar.set_indeterminate(True)
            return
        if "/" in msg:
            i, n = msg.split("/", 1)
            self._phase = phase_text(msg)
            self._note_part(int(i), int(n))
        elif msg in ("decode", "load_moss", "diarize", "yield"):
            self._phase = phase_text(msg)
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
        eta = self.eta_seconds() if self._worker is not None else None
        if eta is not None:
            text += "   " + t("studio.progress.eta").format(t=fmt_time(eta + 0.5))
        self._status.setText(text)

    def _on_cancel(self) -> None:
        w = next((x for x in (self._worker, self._batch, self._burn_worker, self._dl_worker,
                             self._notes_worker) if x is not None), None)
        if w is not None:
            w.cancel()
            self._cancel.setEnabled(False)
            self._cancel.setText(t("studio.cancelling"))
            self._phase = t("studio.cancelling_hint")

    def _on_done(self, tr: Transcript) -> None:
        self._show_result(tr, self._result_src)
        self._status.setText("")
        if tr.is_partial:
            self.toast.emit(partial_text(tr), "warn")

    def _show_result(self, tr: Transcript, src: str = "") -> None:
        self._transcript = tr
        self._result_src = src
        self._view.set_transcript(tr)
        self._view.set_timestamps(self._time_toggle.current() == "time")
        bits = [tr.title] if tr.title else []
        bits.append(t("studio.stats").format(dur=fmt_time(tr.duration), took=fmt_seconds(tr.seconds_taken),
                                             x=f"{tr.realtime_factor:.0f}" if tr.realtime_factor >= 10
                                             else f"{tr.realtime_factor:.1f}"))
        if tr.has_speakers:
            bits.append(t("studio.speaker_count").format(n=len(tr.speakers)))
        bits.append(tr.engine)
        self._stats.setText("  ·  ".join(bits))
        self._burn_btn.setVisible(bool(src) and burn.is_video(src) and os.path.isfile(src))
        self._result.setVisible(True)
        self._show_notes()
        self._refresh()

    def _on_error(self, code: str) -> None:
        self._status.setText("")
        self.toast.emit(friendly_error(code), "error")

    def _on_cancelled(self) -> None:
        self._status.setText(t("studio.cancelled"))

    def _on_finished(self) -> None:
        self._worker = None
        self._end()

    # ── running a queue ───────────────────────────────────────────────
    def _start_batch(self) -> None:
        provider, model = self._notes_provider()
        if self._queue_notes.isChecked() and provider is None:
            self.toast.emit(t("studio.notes.no_provider"), "warn")
            self._refresh()
            return
        self._run_rows = self._runnable_rows()
        if not self._run_rows:
            return
        for r in self._run_rows:
            r.notes_error = ""
            r.set_state("waiting", t("studio.batch.waiting"))
        out_dir = self._out_dir if self._dest.current() == "folder" else ""
        formats = list(self._formats)
        if self._queue_notes.isChecked() and "md" not in formats:
            formats.append("md")
        w = BatchWorker([r.item for r in self._run_rows], self._engine, self._speakers_mode(),
                        formats, out_dir, notes_provider=provider if self._queue_notes.isChecked() else None,
                        notes_model=model)
        self._batch, self._batch_ok = w, 0
        w.item_started.connect(self._on_item_started)
        w.item_progress.connect(self._on_item_progress)
        w.item_titled.connect(lambda i, s: self._run_rows[i].set_name(s))
        w.item_done.connect(self._on_item_done)
        w.item_notes_failed.connect(lambda i, c: setattr(self._run_rows[i], "notes_error", friendly_error(c)))
        w.item_failed.connect(lambda i, c: self._run_rows[i].set_state(
            "failed", t("studio.batch.failed").format(msg=friendly_error(c))))
        w.item_cancelled.connect(lambda i: self._run_rows[i].set_state("cancelled", t("studio.cancelled")))
        w.finished.connect(self._on_batch_finished)
        self._phase = ""
        self._begin(t("studio.batch.cancel_all"))
        self._bar.set_value(0)
        w.start()

    def _on_item_started(self, i: int) -> None:
        self._run_rows[i].set_state("running", t("studio.batch.running"))
        self._phase = f"{i + 1} / {len(self._run_rows)}"
        self._bar.set_value(100 * i / max(1, len(self._run_rows)))

    def _on_item_progress(self, i: int, pct: int, msg: str) -> None:
        row = self._run_rows[i]
        if row.status_text() == t("studio.cancelling"):
            return
        indeterminate = msg in ("fetch", "decode", "load_moss", "diarize")
        row.set_state("running", phase_text(msg) or t("studio.batch.running"), -1 if indeterminate else pct)

    def _on_item_done(self, i: int, tr: Transcript, paths: list) -> None:
        row = self._run_rows[i]
        row.transcript = tr
        self._batch_ok += 1
        text = t("studio.batch.saved").format(files=", ".join(Path(p).name for p in paths))
        if row.notes_error:
            text += "  ·  " + row.notes_error
        if tr.is_partial:
            text = partial_text(tr) + "  ·  " + text
        row.set_state("done", text)
        row.setToolTip("\n".join(paths))

    def _on_batch_finished(self) -> None:
        n = len(self._run_rows)
        self._batch = None
        self._end()
        self._status.setText(t("studio.batch.finished").format(ok=self._batch_ok, n=n))
        self.toast.emit(t("studio.batch.finished").format(ok=self._batch_ok, n=n),
                        "success" if self._batch_ok == n else "warn")

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
        self._phase = t("studio.moss.downloading")
        self._begin(t("common.cancel"))
        w.start()

    def _on_dl_progress(self, pct: int, msg: str) -> None:
        self._phase = f"{t('studio.moss.downloading')}  {msg}"
        if pct >= 0:
            self._bar.set_value(pct)
        else:
            self._bar.set_indeterminate(True)

    def _on_dl_finished(self) -> None:
        self._dl_worker = None
        self._status.setText("")
        self._end()

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

    def _export_stem(self) -> str:
        if self._transcript is not None and self._transcript.title:
            return links.safe_name(self._transcript.title)
        src = self._result_src or self._path
        return Path(src).stem if src else "transcript"

    def _export(self, fmt: str, ext: str) -> None:
        if self._transcript is None:
            return
        folder = Path(self._result_src).parent if self._result_src else Path.home()
        path, _ = QFileDialog.getSaveFileName(self, t("studio.export"), str(folder / f"{self._export_stem()}{ext}"),
                                              f"*{ext}")
        if not path:
            return
        try:
            Path(path).write_text(self._transcript.export(fmt), encoding="utf-8")
            self.toast.emit(t("studio.saved").format(name=Path(path).name), "success")
        except OSError as exc:
            self.toast.emit(t("studio.err.other").format(msg=str(exc)), "error")

    def _rename_speaker(self, speaker: str) -> None:
        if self.busy() or self._transcript is None:
            return
        cur = self._transcript.label(speaker)
        name, ok = QInputDialog.getText(self, t("studio.rename_speaker"),
                                        t("studio.rename_speaker.prompt").format(name=cur), text=cur)
        if ok:
            self._transcript.rename_speaker(speaker, name)
            self._view.refresh_speaker_names()
            self._transcript.notes = ""     # regenerate with the current speaker names
            self._show_notes()

    def _summarize(self) -> None:
        if self.busy() or self._transcript is None:
            return
        provider, model = self._notes_provider()
        self._refresh()
        if provider is None:
            self.toast.emit(t("studio.notes.no_provider"), "warn")
            return
        w = NotesWorker(deepcopy(self._transcript), provider, model)
        self._notes_worker = w
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_notes_done)
        w.error.connect(self._on_error)
        w.cancelled.connect(self._on_cancelled)
        w.finished.connect(self._on_notes_finished)
        self._phase = t("studio.notes.part").format(i=1, n=1)
        self._begin(t("common.cancel"))
        w.start()

    def _on_notes_done(self, notes: str) -> None:
        if not self._notes_worker.cancel_event.is_set():
            self._transcript.notes = notes
            self._show_notes()
            self._status.setText(t("studio.notes.done"))

    def _on_notes_finished(self) -> None:
        self._notes_worker.deleteLater()
        self._notes_worker = None
        self._end()

    def _show_notes(self) -> None:
        notes = self._transcript.notes if self._transcript else ""
        self._notes_view.setMarkdown(notes)
        self._notes_card.setVisible(bool(notes))

    def _copy_notes(self) -> None:
        if self._transcript and self._transcript.notes:
            QApplication.clipboard().setText(self._transcript.notes)
            self.toast.emit(t("studio.copied"), "copy")

    def _save_notes(self) -> None:
        if not self._transcript or not self._transcript.notes:
            return
        folder = Path(self._result_src).parent if self._result_src else Path.home()
        path, _ = QFileDialog.getSaveFileName(self, t("studio.notes.save"),
                                              str(folder / f"{self._export_stem()}-notes.md"), "*.md")
        if not path:
            return
        if not path.lower().endswith(".md"):
            path += ".md"
        try:
            Path(path).write_text(self._transcript.notes + "\n", encoding="utf-8")
            self.toast.emit(t("studio.saved").format(name=Path(path).name), "success")
        except OSError as exc:
            self.toast.emit(t("studio.err.other").format(msg=str(exc)), "error")

    # ── burn subtitles into the video ─────────────────────────────────
    def _burn_menu(self) -> None:
        if self._transcript is None or not self._result_src:
            return
        menu = QMenu(self)
        menu.addAction(t("studio.burn.hard"), lambda: self.burn_subtitles(soft=False))
        menu.addAction(t("studio.burn.hard_soft"), lambda: self.burn_subtitles(soft=True))
        menu.exec(self._burn_btn.mapToGlobal(self._burn_btn.rect().bottomLeft()))

    def burn_subtitles(self, soft: bool = False, out: str = "") -> None:
        if self.busy() or self._transcript is None or not self._result_src:
            return
        if not audio_io.find_ffmpeg():
            from thundertalk.ui.styled_dialog import StyledDialog
            if StyledDialog.confirm(self, title=t("studio.burn.need_ffmpeg"), body=t("studio.burn.need_ffmpeg_body"),
                                    accept_label=t("studio.burn.copy_cmd"), cancel_label=t("studio.burn.close")):
                QApplication.clipboard().setText(FFMPEG_HINT)
                self.toast.emit(t("studio.copied"), "copy")
            return
        if not out:
            out, _ = QFileDialog.getSaveFileName(self, t("studio.burn.dialog"), burn.default_output(self._result_src),
                                                 "*.mp4 *.mov *.m4v")
            if not out:
                return
        if Path(out).suffix.lower() not in (".mp4", ".mov", ".m4v"):
            out += ".mp4"
        w = BurnWorker(self._result_src, self._transcript, out, soft)
        self._burn_worker = w
        w.progress.connect(self._on_burn_progress)
        w.done.connect(self._on_burn_done)
        w.error.connect(self._on_error)
        w.cancelled.connect(self._on_cancelled)
        w.finished.connect(self._on_burn_finished)
        self._phase = t("studio.progress.probe")
        self._begin(t("common.cancel"))
        w.start()

    def _on_burn_progress(self, pct: int, msg: str) -> None:
        if msg == "encode":
            self._phase = t("studio.progress.encode").format(pct=max(0, pct))
            self._bar.set_value(max(0, pct))
        elif msg in ("probe", "render"):
            self._phase = phase_text(msg)
            self._bar.set_indeterminate(True)

    def _on_burn_done(self, r) -> None:
        self._status.setText("")
        self.toast.emit(t("studio.burn.saved").format(name=Path(r.path).name, size=fmt_size(r.size)), "success")

    def _on_burn_finished(self) -> None:
        self._burn_worker = None
        self._end()
