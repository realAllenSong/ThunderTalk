"""Studio ▸ Compose: songs with vocals or background music with ACE-Step 1.5.

Each Generate runs one job (every take) in a disposable process through
``MusicWorker``; takes appear in the list as soon as each is written."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from dataclasses import replace
from typing import Callable, Optional

from PySide6.QtCore import QLocale, QTimer, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QLineEdit, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from thundertalk.core import audio_io, i18n, music, music_lyrics, music_rules
from thundertalk.core.i18n import t
from thundertalk.core.music_worker import MusicRequest, take_seeds
from thundertalk.ui import theme
from thundertalk.ui.model_facts import facts_text
from thundertalk.ui.studio.music_parts import LengthField, LyricsEdit, OptionalIntField, TakeRow, fmt_mmss, unknown_sections
from thundertalk.ui.studio.music_views import LyricCheckView, SungLyrics
from thundertalk.ui.studio.parts import PlayerBar
from thundertalk.ui.studio.workers import BackendDownloadWorker, LyricCheckWorker, MusicWorker, friendly_error, music_status
from thundertalk.ui.styled_dialog import StyledDialog
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
                 make_worker: Optional[Callable] = None, make_check: Optional[Callable] = None) -> None:
        super().__init__()
        self._settings = settings
        if memory_gb is None:
            from thundertalk.core.music_process import total_memory_mb
            memory_gb = total_memory_mb() / 1024
        self._memory_gb = memory_gb
        self._tier = music.tier(memory_gb) or music.tier(music.MIN_MEMORY_GB)
        self._make_worker = make_worker or MusicWorker
        self._make_check = make_check or LyricCheckWorker
        self._engine = None
        self._check = None
        self._check_queue = []
        self._job_rows = []
        self._shutting_down = False
        self._warnings_shown = set()
        self._rule_timer = QTimer(self)
        self._rule_timer.setSingleShot(True)
        self._rule_timer.setInterval(300)
        self._rule_timer.timeout.connect(self._check_rules)
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
        self._sung = SungLyrics()
        self._sung.changed.connect(self._refresh)
        lly.addWidget(self._sung)
        sly.addWidget(self._lyrics_host)
        warn = QHBoxLayout()
        self._artist_warning = _muted()
        warn.addWidget(self._artist_warning, 1)
        self._remove_artist = theme.make_button("", "secondary", 28, font_px=12)
        self._remove_artist.clicked.connect(self._remove_artists)
        warn.addWidget(self._remove_artist)
        sly.addLayout(warn)

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
            self._on_language)
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
        self._long = QCheckBox()
        self._long.toggled.connect(self._allow_long)
        sly.addWidget(self._long)

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
        self._player = PlayerBar(with_save=False)
        self._player.started.connect(self._sync_play_icons)
        self._player.setVisible(False)
        tly.addWidget(self._player)
        self._playing: Optional[TakeRow] = None
        self._playing_window = None
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
        self._lyrics.pronounced.connect(self._accept_pronunciation)
        self.retranslate()

    def _accept_pronunciation(self, line, word, sung) -> None:
        if self._lang.currentData() in ("zh", "yue"):
            self._sung.accept_word(line, word, sung)
            self._sung.toggle.setChecked(True)

    # ── public ────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return self._job is not None or self._dl is not None or self._check is not None

    def set_engine(self, engine) -> None:
        self._engine = engine

    def set_blocked(self, on: bool) -> None:
        """Another Studio tab is running a model: Generate waits for it."""
        self._blocked = on
        self._refresh()

    def refresh(self) -> None:
        self._refresh()

    def stop_playback(self) -> None:
        self._player.shutdown()
        self._sync_play_icons()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._check_queue.clear()
        self.stop_playback()
        for w in (self._job, self._dl, self._check):
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
        self._sung.retranslate()
        self._long.setText(t("studio.compose.long"))
        self._remove_artist.setText(t("studio.compose.rules.remove"))
        self._check_rules()
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
        self._player.retranslate()
        self._footer.setText(t("studio.compose.footer"))
        for row in self._rows:
            row.retranslate()
            if row.check_result is not None:
                self._checked(row, row.check_result, update_status=False)
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
        self._long.setChecked(False)
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

    def _on_language(self, _i) -> None:
        self._save_pref("compose_language", self._lang.currentData())
        self._sung.set_lyrics(self._lyrics.toPlainText(), self._lang.currentData())
        self._refresh()

    def _allow_long(self, on: bool) -> None:
        hi = self._tier.max_seconds if on else self._tier.default_max_seconds(self._model_id)
        self._length.set_bounds(music.MIN_SECONDS, hi)
        self._refresh()

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
        lyrics = music_lyrics.sung_lyrics(self._lyrics.toPlainText().strip(), self._lang.currentData(), self._sung.overrides)
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
        self._sung.set_lyrics(self._lyrics.toPlainText(), self._lang.currentData())
        self._rule_timer.start()
        self._refresh()

    def _check_rules(self) -> None:
        fields = [self._style.toPlainText()] + ([self._lyrics.toPlainText()] if self._song() else [])
        names = list(dict.fromkeys(span.text for text in fields for span in music_rules.find_artist_mentions(text)))
        self._warnings_shown.update(names)
        self._artist_warning.setText(t("studio.compose.rules.warning").format(names=", ".join(names)))
        self._artist_warning.setVisible(bool(names))
        self._remove_artist.setVisible(bool(names))

    def _remove_artists(self) -> None:
        for field in (self._style, self._lyrics):
            text = field.toPlainText()
            field.setPlainText(music_rules.remove_mentions(text, music_rules.find_artist_mentions(text)))
        self._check_rules()

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
                            + facts_text(m.info.params, size, False, m.info.speed))
        need = music.ram_need_mb(m, self._planner_on())
        lines = [t("studio.compose.memory").format(gb=f"{need / 1024:.1f}")]
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
        self._dl_btn.setEnabled(not self.busy() and not self._blocked)
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
        running = self._job is not None or self._check is not None
        self._long.setVisible(self._model_id == music.XL.info.id and self._tier.max_seconds > music.XL_DEFAULT_MAX_SECONDS)
        self._long.setEnabled(not running)
        self._sung.setEnabled(not running)
        for w in (self._model_pick, self._mode, self._planner, self._length, self._bpm, self._lang,
                  self._takes, self._seed):
            w.setEnabled(not running)
        self._style.setReadOnly(running)
        self._lyrics.setReadOnly(running)

    # ── download ──────────────────────────────────────────────────────
    def _download(self) -> None:
        if self.busy() or self._blocked:
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
        self._check_rules()
        req = MusicRequest(
            project_root=str(music.project_root()), model_folder=m.folder,
            job_dir=str(music.new_job_dir(title or "song")), caption=style,
            lyrics=music_lyrics.sung_lyrics(lyrics, self._lang.currentData(), self._sung.overrides), shown_lyrics=lyrics,
            instrumental=not song, vocal_language=(self._lang.currentData() or "unknown") if song else "unknown",
            duration=seconds, bpm=self._bpm.value(), steps=m.steps,
            seeds=take_seeds(self._seed.value(), int(self._takes.currentData() or 1)),
            planner=planner, dit_backend=self._tier.dit_backend,
            artist_warnings=sorted(self._warnings_shown),
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
        self._start_request(req, need)

    def _start_request(self, req, need) -> None:
        self.stop_playback()
        self._job_rows = []
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
        row.meta = meta
        row.request = replace(self._job._request if hasattr(self._job, "_request") else self._job.request) if self._job else None
        row.check_result = None
        row.check_view = None
        row.set_repaint_enabled(self._repaint_ready(row))
        row.action.connect(lambda a, r=row: self._on_take_action(r, a))
        self._rows.append(row)
        self._job_rows.append(row)
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
        if self._check is not None:
            self._check_queue.clear()
            self._check.cancel()
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
        self._check_queue.extend(r for r in self._job_rows
                                 if not r.meta.get("instrumental") and (r.meta.get("lyrics") or r.meta.get("shown_lyrics")))
        self._next_check()
        self._refresh()
        self.busy_changed.emit(self.busy())

    def _next_check(self) -> None:
        if self._shutting_down or not self._check_queue:
            self._cancel.hide()
            self._go.show()
            self._refresh()
            self.busy_changed.emit(self.busy())
            return
        row = self._check_queue.pop(0)
        w = self._make_check(row.path, self._engine, row.meta)
        self._check = w
        row.check_button.setText(t("studio.compose.check.running"))
        row.check_button.show()
        row.check_button.setEnabled(False)
        w.done.connect(lambda result, r=row: self._checked(r, result))
        w.error.connect(lambda code, r=row: self._check_error(r, code))
        w.cancelled.connect(lambda r=row: self._check_cancelled(r))
        w.finished.connect(self._check_finished)
        self._go.hide()
        self._cancel.show()
        self._cancel.setEnabled(True)
        self._cancel.setText(t("common.cancel"))
        self._status.setText(t("studio.compose.check.running"))
        self._refresh()
        self.busy_changed.emit(True)
        w.start()

    def _check_finished(self) -> None:
        self._check = None
        self._next_check()

    def _check_cancelled(self, row) -> None:
        self._check_error(row, "cancelled")
        self._status.setText(t("studio.cancelled"))

    def _check_error(self, row, code) -> None:
        row.check_button.setText(t("studio.compose.check.download") if code == "music:no_asr"
                                 else t("studio.compose.check.retry"))
        row.check_button.setEnabled(True)
        row.check_button.clicked.disconnect()
        row.check_button.clicked.connect(lambda: self._download_asr(row) if code == "music:no_asr" else self._retry_check(row))
        if code != "cancelled":
            self.toast.emit(friendly_error(code), "warn")

    def _retry_check(self, row) -> None:
        if not self.busy() and not self._blocked:
            self._check_queue.append(row)
            self._next_check()

    def _download_asr(self, row) -> None:
        if self.busy() or self._blocked:
            return
        from thundertalk.core.models import BUILTIN_MODELS
        from thundertalk.ui.pages.models_page import DownloadWorker
        w = DownloadWorker(next(m for m in BUILTIN_MODELS if m.id == "qwen3-asr-06b-int8"))
        self._dl = w
        w.progress.connect(self._on_dl_progress)
        w.error.connect(lambda msg: self.toast.emit(msg, "error"))
        w.done.connect(lambda _: self._check_queue.append(row))
        w.finished.connect(self._asr_download_finished)
        self._refresh()
        self.busy_changed.emit(True)
        w.start()

    def _asr_download_finished(self) -> None:
        self._on_dl_finished()
        self._next_check()

    def _checked(self, row, result, update_status=True) -> None:
        row.check_button.setEnabled(True)
        if result is None:
            row.check_button.hide()
            return
        row.check_result = result
        row.check_button.show()
        row.set_check(*result.summary)
        expanded = row.check_view is not None and not row.check_view.isHidden()
        if row.check_view is not None:
            row.check_view.deleteLater()
        row.check_view = LyricCheckView(result, self._repaint_ready(row))
        row.check_view.action.connect(lambda i, a, r=row: self._check_action(r, i, a))
        row.layout().addWidget(row.check_view)
        row.check_view.setVisible(expanded)
        row.check_button.setText(t("studio.compose.check.details"))
        row.check_button.clicked.disconnect()
        row.check_button.clicked.connect(lambda: row.check_view.setVisible(row.check_view.isHidden()))
        if update_status:
            self._status.setText(t("studio.compose.check.finished"))

    # ── take actions ──────────────────────────────────────────────────
    def _on_take_action(self, row: TakeRow, action: str) -> None:
        if action == "play":
            self._play_take(row)
        elif action == "export":
            self._export_take(row)
        elif action == "repaint":
            self._repaint(row)
        elif action == "delete":
            self._delete_take(row)

    def _repaint_ready(self, row) -> bool:
        wav = Path(row.path)
        filename = row.meta.get("repaint_source_latents_file")
        folder = row.meta.get("model")
        model = next((m for m in music.MUSIC_MODELS if m.folder == folder), None)
        return bool(filename and wav.with_suffix(".json").is_file() and (wav.parent / filename).is_file()
                    and model and not music.missing(model, False))

    def _check_action(self, row, index, action) -> None:
        result = row.check_result
        if action == "edit_sung":
            if self.busy() or self._blocked:
                return
            self._lyrics.setPlainText(row.meta.get("shown_lyrics") or row.meta.get("lyrics") or "")
            self._lang.setCurrentIndex(max(0, self._lang.findData(row.meta.get("vocal_language"))))
            self._sung.edit_line(result.lines[index].text)
        elif action == "new_take":
            self._another_take(row)
        elif action == "repaint_line":
            self._repaint(row, result.line_ranges[index])
        elif action == "play_line":
            window = result.line_ranges[index]
            if window:
                self._play_take(row, window)

    def _another_take(self, row) -> None:
        if self.busy() or self._blocked or row.request is None:
            return
        req = replace(row.request, job_dir=str(music.new_job_dir(row.meta.get("caption") or "song")),
                      seeds=take_seeds(None, 1), task_type="text2music", src_audio=None)
        req.lyrics = music_lyrics.sung_lyrics(req.shown_lyrics or req.lyrics, req.vocal_language, self._sung.overrides)
        m = next(m for m in music.MUSIC_MODELS if m.folder == req.model_folder)
        self._start_request(req, music.ram_need_mb(m, req.planner))

    def repaint_range(self, row, window):
        dialog = StyledDialog(self, title=t("studio.compose.take.repaint"),
                              body=t("studio.compose.check.range_advice"),
                              accept_label=t("studio.compose.take.repaint"), cancel_label=t("common.cancel"))
        panel = dialog.findChild(QWidget, "styledDialogPanel")
        controls = QHBoxLayout()
        fields = []
        for key, value in zip(("start", "end"), window or (0, min(row.seconds, 15))):
            controls.addWidget(_muted(t(f"studio.compose.check.{key}")))
            f = QLineEdit(f"{value:.1f}")
            f.setStyleSheet(theme.INPUT_QSS)
            f.setValidator(QDoubleValidator(0, row.seconds, 1, f))
            fields.append(f)
            controls.addWidget(f)
        panel.layout().insertLayout(2, controls)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                a, b = (float(f.text()) for f in fields)
            except ValueError:
                return None
            if 0 <= a < b <= row.seconds:
                return a, b
        return None

    def _repaint(self, row, window=None) -> None:
        if self.busy() or self._blocked or row.request is None or not self._repaint_ready(row):
            return
        window = self.repaint_range(row, window)
        if window is None:
            return
        req = replace(row.request, task_type="repaint", src_audio=str(Path(row.path).resolve()),
                      repainting_start=window[0], repainting_end=window[1], planner=False,
                      seeds=[row.seed], job_dir=str(music.new_job_dir("repaint")))
        shown = row.meta.get("shown_lyrics") or row.meta.get("lyrics") or ""
        req.lyrics = music_lyrics.sung_lyrics(shown, req.vocal_language, self._sung.overrides)
        m = next(m for m in music.MUSIC_MODELS if m.folder == req.model_folder)
        self._start_request(req, music.ram_need_mb(m, False))

    def _play_take(self, row: TakeRow, window=None) -> None:
        if self._playing is not row or window != self._playing_window:
            try:
                x, sr = audio_io.read_wav(row.path)
            except (OSError, ValueError) as exc:
                self.toast.emit(t("studio.err.other").format(msg=exc), "error")
                return
            if window:
                x = x[int(window[0] * sr):int(window[1] * sr)]
            self._player.set_audio(x.mean(axis=1) if x.ndim > 1 else x, sr)   # Player is mono
            self._player.setVisible(True)
            self._playing = row
            self._playing_window = window
        self._player.toggle()
        self._sync_play_icons()

    def _sync_play_icons(self) -> None:
        on = self._player.player.is_playing
        for r in self._rows:
            r.set_playing(on and r is self._playing)

    def export_path(self, row: TakeRow) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self, t("studio.compose.take.export"), str(Path.home() / Path(row.path).name), "*.wav")
        return path

    def _export_take(self, row: TakeRow) -> None:
        path = self.export_path(row)
        if not path:
            return
        if not path.lower().endswith(".wav"):
            path += ".wav"
        try:
            shutil.copyfile(row.path, path)
            self.toast.emit(t("studio.saved").format(name=Path(path).name), "success")
        except OSError as exc:
            self.toast.emit(t("studio.err.other").format(msg=exc), "error")

    def confirm_delete(self, row: TakeRow) -> bool:
        return StyledDialog.confirm(
            self.window(), title=t("studio.compose.take.delete_title").format(i=row.index),
            body=t("studio.compose.take.delete_body"), accept_label=t("studio.compose.take.delete"),
            cancel_label=t("common.cancel"), destructive=True)

    def _delete_take(self, row: TakeRow) -> None:
        if self.busy() or not self.confirm_delete(row):
            return
        if self._playing is row:
            self._player.shutdown()
            self._player.clear()
            self._player.setVisible(False)
            self._playing = None
        wav = Path(row.path)
        for p in (wav, wav.with_suffix(".json"), wav.with_suffix(".latents.npy")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._takes_card.setVisible(bool(self._rows))
