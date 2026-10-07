"""Studio ▸ Compose: songs with vocals or background music with ACE-Step 1.5.

Each Generate runs one job (every take) in a disposable process through
``MusicWorker``; takes appear in the list as soon as each is written."""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QLocale, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from thundertalk.core import i18n, music
from thundertalk.core.i18n import t
from thundertalk.core.music_worker import MusicRequest, take_seeds
from thundertalk.ui import theme
from thundertalk.ui.model_facts import facts_text
from thundertalk.ui.studio.music_parts import LengthField, LyricsEdit, OptionalIntField, TakeRow, fmt_mmss, unknown_sections
from thundertalk.ui.studio.workers import BackendDownloadWorker, MusicWorker, friendly_error, music_status
from thundertalk.ui.widgets import Rule, SegmentedControl, ThinProgress

MODES = ("song", "bgm")
MAX_SEED = 2**32 - 1


def _muted(text: str = "", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {size}px; background: transparent;")
    return lb


def _caption(text: str = "") -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;")
    return lb


def _gb(mb: int) -> str:
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def language_name(code: str) -> str:
    name = QLocale(code).nativeLanguageName() if code != "yue" else "粵語"
    return f"{name} ({code})" if name else code


class ComposeTab(QWidget):
    navigate = Signal(str)
    toast = Signal(str, str)
    busy_changed = Signal(bool)

    def __init__(self, settings=None, memory_gb: Optional[float] = None,
                 make_worker: Optional[Callable] = None) -> None:
        super().__init__()
        self._settings = settings
        if memory_gb is None:
            from thundertalk.core.music_process import total_memory_mb
            memory_gb = total_memory_mb() / 1024
        self._memory_gb = memory_gb
        self._tier = music.tier(memory_gb) or music.tier(music.MIN_MEMORY_GB)
        self._make_worker = make_worker or MusicWorker
        self._job: Optional[MusicWorker] = None
        self._dl: Optional[BackendDownloadWorker] = None
        self._blocked = False                       # Speak or Transcribe is running
        self._rows: list[TakeRow] = []
        self._n_takes = 0
        self._take_i = 0
        self._line = ""
        self._error = ""
        self._t0 = 0.0
        saved = self._pref("compose_model", "")
        self._model_id = saved if saved in self._tier.models else self._tier.recommended

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # ── engine ────────────────────────────────────────────────────
        ecard = theme.make_card()
        ely = QVBoxLayout(ecard)
        ely.setContentsMargins(24, 20, 24, 20)
        ely.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        self._cap_model = _caption()
        head.addWidget(self._cap_model)
        self._model_pick = SegmentedControl([(m, music.model(m).info.name) for m in self._tier.models], self._model_id)
        self._model_pick.changed.connect(self._on_model)
        head.addWidget(self._model_pick)
        head.addStretch()
        ely.addLayout(head)
        self._facts = _muted()
        ely.addWidget(self._facts)
        self._needs = _muted()
        ely.addWidget(self._needs)
        self._planner = QCheckBox()
        self._planner.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; background: transparent;")
        self._planner.setChecked(self._initial_planner())
        self._planner.toggled.connect(self._on_planner)
        self._planner.setVisible(self._tier.planner)
        ely.addWidget(self._planner)
        drow = QHBoxLayout()
        drow.setSpacing(10)
        self._dl_btn = theme.accent_button("", 38)
        self._dl_btn.clicked.connect(self._download)
        drow.addWidget(self._dl_btn)
        self._dl_cancel = theme.make_button(t("common.cancel"), "secondary", 38)
        self._dl_cancel.clicked.connect(self._cancel_download)
        drow.addWidget(self._dl_cancel)
        self._dl_status = _muted()
        drow.addWidget(self._dl_status, 1)
        ely.addLayout(drow)
        self._dl_bar = ThinProgress(4)
        self._dl_bar.setVisible(False)
        ely.addWidget(self._dl_bar)
        root.addWidget(ecard)

        # ── song ──────────────────────────────────────────────────────
        scard = theme.make_card()
        sly = QVBoxLayout(scard)
        sly.setContentsMargins(24, 20, 24, 20)
        sly.setSpacing(10)
        mode = self._pref("compose_mode", "song")
        self._mode = SegmentedControl([(k, t(f"studio.compose.mode.{k}")) for k in MODES],
                                      mode if mode in MODES else "song")
        self._mode.changed.connect(self._on_mode)
        mrow = QHBoxLayout()
        mrow.addWidget(self._mode)
        mrow.addStretch()
        sly.addLayout(mrow)
        self._cap_style = _caption()
        sly.addWidget(self._cap_style)
        self._style = QPlainTextEdit()
        self._style.setTabChangesFocus(True)
        self._style.setFixedHeight(72)
        self._style.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: 8px 12px; font-size: 14px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {theme.INK}; }}")
        self._style.textChanged.connect(self._on_text)
        sly.addWidget(self._style)
        self._style_count = _muted()
        sly.addWidget(self._style_count)

        self._lyrics_host = QWidget()
        lly = QVBoxLayout(self._lyrics_host)
        lly.setContentsMargins(0, 6, 0, 0)
        lly.setSpacing(8)
        lhead = QHBoxLayout()
        self._cap_lyrics = _caption()
        lhead.addWidget(self._cap_lyrics)
        lhead.addStretch()
        self._lyrics = LyricsEdit()
        self._lyrics.textChanged.connect(self._on_text)
        self._insert = theme.make_button("", "secondary", 28, font_px=12)
        self._insert.clicked.connect(
            lambda: self._lyrics.section_menu(self).exec(self._insert.mapToGlobal(self._insert.rect().bottomLeft())))
        lhead.addWidget(self._insert)
        lly.addLayout(lhead)
        lly.addWidget(self._lyrics)
        self._lyrics_count = _muted()
        lly.addWidget(self._lyrics_count)
        sly.addWidget(self._lyrics_host)

        sly.addWidget(Rule())
        opts = QHBoxLayout()
        opts.setSpacing(10)
        self._len_lbl = _muted()
        opts.addWidget(self._len_lbl)
        hi = self._tier.default_max_seconds(self._model_id)
        self._length = LengthField(int(self._pref("compose_seconds", 120) or 120), music.MIN_SECONDS, hi)
        self._length.changed.connect(self._on_length)
        opts.addWidget(self._length)
        self._bpm_lbl = _muted()
        opts.addWidget(self._bpm_lbl)
        self._bpm = OptionalIntField(30, 300, "")
        opts.addWidget(self._bpm)
        self._lang_lbl = _muted()
        opts.addWidget(self._lang_lbl)
        self._lang = QComboBox()
        theme.style_combo(self._lang)
        for code in music.ACESTEP_LANGUAGES:
            self._lang.addItem(language_name(code), code)
        lang = self._pref("compose_language", "") or ("zh" if i18n.LANG == "zh" else "en")
        self._lang.setCurrentIndex(max(0, self._lang.findData(lang)))
        self._lang.currentIndexChanged.connect(
            lambda _i: self._save_pref("compose_language", self._lang.currentData()))
        opts.addWidget(self._lang)
        self._takes_lbl = _muted()
        opts.addWidget(self._takes_lbl)
        self._takes = QComboBox()
        theme.style_combo(self._takes)
        for n in range(1, music.MAX_TAKES + 1):
            self._takes.addItem(str(n), n)
        n = int(self._pref("compose_takes", 0) or 0) or self._tier.takes
        self._takes.setCurrentIndex(max(0, min(music.MAX_TAKES, n) - 1))
        self._takes.currentIndexChanged.connect(self._on_takes)
        opts.addWidget(self._takes)
        self._seed_lbl = _muted()
        opts.addWidget(self._seed_lbl)
        self._seed = OptionalIntField(0, MAX_SEED, "", width=110)
        opts.addWidget(self._seed)
        opts.addStretch()
        sly.addLayout(opts)
        self._estimate = _muted()
        sly.addWidget(self._estimate)

        act = QHBoxLayout()
        act.setSpacing(10)
        self._go = theme.accent_button("", 40)
        self._go.clicked.connect(self._generate)
        act.addWidget(self._go)
        self._cancel = theme.make_button(t("common.cancel"), "secondary", 40)
        self._cancel.clicked.connect(self._on_cancel)
        self._cancel.setVisible(False)
        act.addWidget(self._cancel)
        self._status = _muted()
        act.addWidget(self._status, 1)
        sly.addLayout(act)
        self._bar = ThinProgress(4)
        self._bar.setVisible(False)
        sly.addWidget(self._bar)
        root.addWidget(scard)

        # ── takes ─────────────────────────────────────────────────────
        self._takes_card = theme.make_card()
        tly = QVBoxLayout(self._takes_card)
        tly.setContentsMargins(24, 16, 24, 16)
        tly.setSpacing(4)
        self._cap_takes = _caption()
        tly.addWidget(self._cap_takes)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
        tly.addLayout(self._rows_layout)
        self._takes_card.setVisible(False)
        root.addWidget(self._takes_card)

        self._footer = _muted(size=11)
        root.addWidget(self._footer)
        root.addStretch()

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._show_status)
        self.retranslate()

    # ── public ────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return self._job is not None or self._dl is not None

    def set_blocked(self, on: bool) -> None:
        """Another Studio tab is running a model: Generate waits for it."""
        self._blocked = on
        self._refresh()

    def refresh(self) -> None:
        self._refresh()

    def stop_playback(self) -> None:
        pass

    def shutdown(self) -> None:
        for w in (self._job, self._dl):
            if w is not None:
                w.cancel()
                w.wait(12000)

    def retranslate(self) -> None:
        self._cap_model.setText(t("studio.compose.model"))
        self._mode.set_options([(k, t(f"studio.compose.mode.{k}")) for k in MODES])
        self._cap_style.setText(t("studio.compose.style"))
        self._style.setPlaceholderText(t("studio.compose.style_ph"))
        self._cap_lyrics.setText(t("studio.compose.lyrics"))
        self._lyrics.setPlaceholderText(t("studio.compose.lyrics_ph"))
        self._insert.setText(t("studio.compose.insert"))
        self._len_lbl.setText(t("studio.compose.length"))
        self._bpm_lbl.setText(t("studio.compose.bpm"))
        self._bpm.setPlaceholderText(t("studio.compose.auto"))
        self._lang_lbl.setText(t("studio.compose.language"))
        self._takes_lbl.setText(t("studio.compose.takes"))
        self._seed_lbl.setText(t("studio.compose.seed"))
        self._seed.setPlaceholderText(t("studio.compose.random"))
        self._go.setText(t("studio.compose.go"))
        self._cancel.setText(t("common.cancel"))
        self._dl_cancel.setText(t("common.cancel"))
        self._cap_takes.setText(t("studio.compose.takes_title"))
        self._footer.setText(t("studio.compose.footer"))
        for row in self._rows:
            row.retranslate()
        self._on_mode(self._mode.current(), save=False)

    # ── remembered choices ────────────────────────────────────────────
    def _pref(self, key: str, default):
        get = getattr(self._settings, "get", None)
        try:
            value = get(key) if callable(get) else None
        except Exception:
            value = None
        return default if value is None else value

    def _save_pref(self, key: str, value) -> None:
        setter = getattr(self._settings, "set", None)
        if callable(setter):
            try:
                setter(key, value)
            except OSError:
                pass

    def _initial_planner(self) -> bool:
        if not self._tier.planner:
            return False
        saved = self._pref("compose_planner", None)
        if saved is not None:
            return bool(saved)
        import shutil
        from pathlib import Path
        m = music.model(self._model_id)
        pending = sum(d.size_mb for d in music.missing(m, True))
        free_mb = shutil.disk_usage(Path.home()).free // 1_000_000
        return music.planner_pre_ticked(self._memory_gb, free_mb, pending)

    # ── form ──────────────────────────────────────────────────────────
    def _planner_on(self) -> bool:
        return self._tier.planner and self._planner.isChecked()

    def _on_model(self, mid: str) -> None:
        self._model_id = mid
        self._save_pref("compose_model", mid)
        self._length.set_bounds(music.MIN_SECONDS, self._tier.default_max_seconds(mid))
        self._refresh()

    def _on_planner(self, on: bool) -> None:
        self._save_pref("compose_planner", on)
        self._refresh()

    def _on_mode(self, mode: str, save: bool = True) -> None:
        if save:
            self._save_pref("compose_mode", mode)
        song = mode == "song"
        self._lyrics_host.setVisible(song)
        for w in (self._lang_lbl, self._lang):
            w.setVisible(song)
        self._on_text()

    def _on_length(self, seconds: int) -> None:
        self._save_pref("compose_seconds", seconds)
        self._refresh()

    def _on_takes(self, _i: int) -> None:
        self._save_pref("compose_takes", int(self._takes.currentData() or 1))
        self._refresh()

    def _song(self) -> bool:
        return self._mode.current() == "song"

    def _texts_ok(self) -> bool:
        style = self._style.toPlainText().strip()
        lyrics = self._lyrics.toPlainText().strip()
        if len(style) > music.STYLE_MAX_CHARS or (self._song() and len(lyrics) > music.LYRICS_MAX_CHARS):
            return False
        return bool(lyrics) if self._song() else bool(style)

    def _on_text(self) -> None:
        def count(label: QLabel, n: int, cap: int, extra: str = "") -> None:
            color = theme.WARNING if n > cap or extra else theme.TEXT_MUTED
            label.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
            label.setText(f"{n:,} / {cap:,}" + (f"   {extra}" if extra else ""))
        count(self._style_count, len(self._style.toPlainText()), music.STYLE_MAX_CHARS)
        odd = unknown_sections(self._lyrics.toPlainText())
        hint = t("studio.compose.odd_tags").format(tags=", ".join(f"[{s}]" for s in dict.fromkeys(odd))) if odd else ""
        count(self._lyrics_count, len(self._lyrics.toPlainText()), music.LYRICS_MAX_CHARS, hint)
        self._refresh()

    # ── readiness ─────────────────────────────────────────────────────
    def _pending(self) -> list:
        return music.missing(music.model(self._model_id), self._planner_on())

    def _component_needed(self) -> bool:
        from thundertalk.core.runtime import needed
        return needed("music")

    def _ready(self) -> bool:
        return not self._pending() and not self._component_needed()

    def _refresh(self) -> None:
        m = music.model(self._model_id)
        zh = i18n.LANG == "zh"
        size = sum(d.size_mb for d in m.info.downloads)
        self._facts.setText((m.info.blurb_zh if zh else m.info.blurb_en) + "  "
                            + facts_text(m.info.params, size, False))
        need = music.ram_need_mb(m, self._planner_on())
        lines = [t("studio.compose.memory").format(gb=f"{need / 1000:.0f}")]
        lines += [t("studio.compose.needs").format(name=music.model(d).info.name, gb=music.model(d).min_memory_gb)
                  for d in self._tier.disabled]
        if self._tier.experimental:
            lines.append(t("studio.compose.experimental"))
        self._needs.setText("\n".join(lines))
        self._planner.setText(t("studio.compose.planner").format(size=_gb(music.PLANNER.size_mb)))

        pending = self._pending()
        from thundertalk.core.runtime import size_mb
        pending_mb = sum(d.size_mb for d in pending) + (size_mb("music") if self._component_needed() else 0)
        downloading = self._dl is not None
        ready = not pending_mb
        self._dl_btn.setText(t("studio.engine.download_named").format(name=m.info.name, size=_gb(pending_mb)))
        self._dl_btn.setVisible(not ready and not downloading)
        self._dl_btn.setEnabled(self._job is None)
        self._dl_cancel.setVisible(downloading)
        self._dl_bar.setVisible(downloading)
        if not downloading:
            self._dl_status.setText(t("studio.compose.installed") if ready else "")

        est = music.estimate_take_s(m, self._length.value(), self._planner_on())
        n = int(self._takes.currentData() or 1)
        self._estimate.setText(t("studio.compose.estimate").format(
            each=fmt_mmss(round(est)), total=fmt_mmss(round(est * n))))
        busy = self.busy()
        self._go.setEnabled(ready and not busy and not self._blocked and self._texts_ok())
        self._go.setToolTip(t("studio.compose.blocked") if self._blocked else "")
        running = self._job is not None
        for w in (self._model_pick, self._mode, self._planner, self._length, self._bpm, self._lang,
                  self._takes, self._seed):
            w.setEnabled(not running)
        self._style.setReadOnly(running)
        self._lyrics.setReadOnly(running)

    # ── download ──────────────────────────────────────────────────────
    def _download(self) -> None:
        if self._dl is not None:
            return
        m = music.model(self._model_id)
        w = BackendDownloadWorker(music.with_planner(m) if self._planner_on() else m.info)
        self._dl = w
        w.progress.connect(self._on_dl_progress)
        w.done.connect(lambda _r: self.toast.emit(t("studio.engine.ready"), "success"))
        w.error.connect(lambda c: self.toast.emit(friendly_error(c), "error"))
        w.finished.connect(self._on_dl_finished)
        self._dl_bar.set_indeterminate(True)
        self._dl_status.setText(t("studio.engine.connecting"))
        self._refresh()
        self.busy_changed.emit(True)
        w.start()

    def _on_dl_progress(self, pct: int, msg: str) -> None:
        self._dl_status.setText(msg)
        if pct >= 0:
            self._dl_bar.set_value(pct)
        else:
            self._dl_bar.set_indeterminate(True)

    def _cancel_download(self) -> None:
        if self._dl is not None:
            self._dl.cancel()
            self._dl_cancel.setEnabled(False)
            self._dl_status.setText(t("studio.cancelling"))

    def _on_dl_finished(self) -> None:
        self._dl = None
        self._dl_status.setText("")
        self._dl_cancel.setEnabled(True)
        self._refresh()
        self.busy_changed.emit(self.busy())

    # ── generate ──────────────────────────────────────────────────────
    def build_request(self) -> tuple[MusicRequest, int]:
        """The job for the current form, and the memory it needs (MB)."""
        m = music.model(self._model_id)
        planner = self._planner_on()
        song = self._song()
        style = self._style.toPlainText().strip()
        lyrics = self._lyrics.toPlainText().strip() if song else ""
        seconds = self._length.value()
        title = style or next((ln for ln in lyrics.splitlines() if ln.strip() and not ln.strip().startswith("[")), "")
        req = MusicRequest(
            project_root=str(music.project_root()), model_folder=m.folder,
            job_dir=str(music.new_job_dir(title or "song")), caption=style, lyrics=lyrics,
            instrumental=not song, vocal_language=(self._lang.currentData() or "unknown") if song else "unknown",
            duration=seconds, bpm=self._bpm.value(), steps=m.steps,
            seeds=take_seeds(self._seed.value(), int(self._takes.currentData() or 1)),
            planner=planner, dit_backend=self._tier.dit_backend,
            estimate_s=music.estimate_take_s(m, seconds, planner))
        return req, music.ram_need_mb(m, planner)

    def _generate(self) -> None:
        if self.busy() or self._blocked or not self._ready() or not self._texts_ok():
            return
        try:
            req, need = self.build_request()
        except OSError as exc:
            self.toast.emit(t("studio.err.other").format(msg=exc), "error")
            return
        w = self._make_worker(req, need)
        self._job = w
        self._n_takes, self._take_i, self._error = len(req.seeds), 0, ""
        self._line = t("studio.music.progress.load")
        w.event.connect(self._on_event)
        w.take.connect(self._on_take)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.cancelled.connect(self._on_cancelled)
        w.finished.connect(self._on_finished)
        self._t0 = time.monotonic()
        self._go.setVisible(False)
        self._cancel.setVisible(True)
        self._cancel.setEnabled(True)
        self._cancel.setText(t("common.cancel"))
        self._bar.setVisible(True)
        self._bar.set_indeterminate(True)
        self._tick.start()
        self._show_status()
        self._refresh()
        self.busy_changed.emit(True)
        w.start()

    def _on_event(self, event: tuple) -> None:
        if event[0] == "phase" and event[1] in ("plan", "compose") and isinstance(event[2], int):
            self._take_i = event[2]
        if event[0] == "step":
            done, total = event[1], max(1, event[2])
            self._bar.set_value(100.0 * (self._take_i + done / total) / max(1, self._n_takes))
        line = music_status(event)
        if line is not None:
            self._line = line
        self._show_status()

    def _show_status(self) -> None:
        if self._job is None:
            return
        el = time.monotonic() - self._t0
        take = t("studio.music.progress.take").format(i=min(self._take_i + 1, self._n_takes), n=self._n_takes)
        lead = f"{take} · " if self._n_takes > 1 else ""
        self._status.setText(f"{lead}{self._line}   {fmt_mmss(int(el))}")

    def _on_take(self, index: int, path: str, meta) -> None:
        meta = meta or {}
        params = meta.get("params") or {}
        seconds = float(meta.get("duration") or params.get("duration") or self._length.value())
        row = TakeRow(len(self._rows) + 1, int(meta.get("seed", 0)), seconds, path)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        self._takes_card.setVisible(True)
        self._take_i = index + 1

    def _on_done(self, takes) -> None:
        self._status.setText(t("studio.compose.done").format(n=len(takes), took=fmt_mmss(int(time.monotonic() - self._t0))))

    def _on_error(self, code: str) -> None:
        self._error = friendly_error(code)
        self._status.setText(self._error)
        self.toast.emit(self._error, "error")

    def _on_cancelled(self) -> None:
        self._status.setText(t("studio.cancelled"))

    def _on_cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self._cancel.setEnabled(False)
            self._cancel.setText(t("studio.cancelling"))

    def _on_finished(self) -> None:
        self._tick.stop()
        self._job = None
        self._bar.setVisible(False)
        self._bar.set_indeterminate(False)
        self._cancel.setVisible(False)
        self._go.setVisible(True)
        self._refresh()
        self.busy_changed.emit(self.busy())
