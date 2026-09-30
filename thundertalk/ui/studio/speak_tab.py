"""Studio ▸ Speak: pick a voice (built-in or your own), type text, get speech."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import audio_io, i18n, speech, tts
from thundertalk.core.i18n import t
from thundertalk.core.playback import Player
from thundertalk.core.tts_backends.previews import load_preview, preview_path
from thundertalk.core.voices import SavedVoice, VoiceLibrary
from thundertalk.ui import theme
from thundertalk.ui.studio.clone_dialog import CloneDialog
from thundertalk.ui.studio.parts import PlayerBar, VoiceChip, fmt_seconds
from thundertalk.ui.studio.workers import BackendDownloadWorker, SynthWorker, friendly_error
from thundertalk.ui.widgets import FlowLayout, Rule, SegmentedControl, ThinProgress

SPEEDS = [("0.75", 0.75), ("0.9", 0.9), ("1.0", 1.0), ("1.15", 1.15), ("1.3", 1.3), ("1.5", 1.5)]
IDLE_UNLOAD_MS = 5 * 60 * 1000
MY_PREFIX = "my:"


def _voice_tag(v, zh: bool) -> str:
    """Short second line for a voice chip: "中文 · 女声" / "Chinese · female"."""
    lang = {"chinese": ("中文", "Chinese"), "english": ("英文", "English")}.get(v.language, ("多语言", "Multilingual"))
    sex = {"f": ("女声", "female"), "m": ("男声", "male")}.get(v.gender, ("", ""))
    return f"{lang[0]} · {sex[0]}" if zh else f"{lang[1]} · {sex[1]}"


def default_voice() -> str:
    """The first voice to offer: Kokoro when it is already on disk (small and
    instant), otherwise VoxCPM2's warm voice in the UI language."""
    zh = i18n.LANG == "zh"
    if speech.backend("kokoro").is_ready() and not speech.backend("voxcpm2").is_ready():
        return "kokoro:3" if zh else "kokoro:0"
    return "voxcpm2:warm-female-zh" if zh else "voxcpm2:female-en"


def _muted(text: str = "", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {size}px; background: transparent;")
    return lb


def _caption(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;")
    return lb


class SpeakTab(QWidget):
    navigate = Signal(str)
    toast = Signal(str, str)

    def __init__(self, settings=None) -> None:
        super().__init__()
        self._settings = settings
        self._asr = None
        self._lib = VoiceLibrary()
        self._voice_id = self._pref("studio_voice", "") or default_voice()
        self._engine_id = speech.BACKEND_ORDER[0]
        self._chips: dict[str, VoiceChip] = {}
        self._synth: Optional[SynthWorker] = None
        self._dl: Optional[BackendDownloadWorker] = None
        self._result: Optional[tts.SynthResult] = None
        self._t0 = 0.0
        self._phase = ""
        self._preview = Player()                    # voice previews, separate from the result player
        self._preview_vid: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # ── voice engine (download) ───────────────────────────────────
        self._engine_card = theme.make_card()
        ely = QVBoxLayout(self._engine_card)
        ely.setContentsMargins(24, 20, 24, 20)
        ely.setSpacing(10)
        self._engine_title = QLabel()
        self._engine_title.setFont(theme.font_serif(16))
        self._engine_title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        ely.addWidget(self._engine_title)
        self._engine_body = _muted(size=13)
        ely.addWidget(self._engine_body)
        erow = QHBoxLayout()
        erow.setSpacing(10)
        self._dl_btn = theme.accent_button("", 38)
        self._dl_btn.clicked.connect(self._download)
        erow.addWidget(self._dl_btn)
        self._dl_cancel = theme.make_button(t("common.cancel"), "secondary", 38)
        self._dl_cancel.clicked.connect(self._cancel_download)
        self._dl_cancel.setVisible(False)
        erow.addWidget(self._dl_cancel)
        self._dl_status = _muted()
        erow.addWidget(self._dl_status, 1)
        ely.addLayout(erow)
        self._dl_bar = ThinProgress(4)
        self._dl_bar.setVisible(False)
        ely.addWidget(self._dl_bar)
        root.addWidget(self._engine_card)

        # ── voices ────────────────────────────────────────────────────
        vcard = theme.make_card()
        vly = QVBoxLayout(vcard)
        vly.setContentsMargins(24, 20, 24, 20)
        vly.setSpacing(12)
        erow2 = QHBoxLayout()
        erow2.setSpacing(12)
        self._cap_engine = _caption(t("studio.engine.pick"))
        erow2.addWidget(self._cap_engine)
        self._engine_pick = SegmentedControl([(b.info.id, b.info.name) for b in speech.backends()],
                                             self._initial_engine())
        self._engine_pick.changed.connect(self._on_engine)
        erow2.addWidget(self._engine_pick)
        erow2.addStretch()
        vly.addLayout(erow2)
        self._engine_tag = _muted(size=12)
        vly.addWidget(self._engine_tag)
        vly.addWidget(Rule())
        self._cap_builtin = _caption(t("studio.voices.builtin"))
        vly.addWidget(self._cap_builtin)
        self._builtin_host = QWidget()
        self._builtin_flow = FlowLayout(self._builtin_host, 10, 10)
        vly.addWidget(self._builtin_host)
        self._mine_rule = Rule()
        vly.addWidget(self._mine_rule)
        head = QHBoxLayout()
        self._cap_mine = _caption(t("studio.voices.mine"))
        head.addWidget(self._cap_mine)
        head.addStretch()
        self._manage_btn = theme.make_button(t("studio.voices.manage"), "secondary", 28, font_px=12)
        self._manage_btn.clicked.connect(self._manage_menu)
        head.addWidget(self._manage_btn)
        vly.addLayout(head)
        self._mine_host = QWidget()
        self._mine_flow = FlowLayout(self._mine_host, 10, 10)
        vly.addWidget(self._mine_host)
        self._mine_hint = _muted(t("studio.voices.mine_hint"), 12)
        vly.addWidget(self._mine_hint)
        self._no_clone = _muted(t("studio.voices.no_clone"), 12)
        vly.addWidget(self._no_clone)
        root.addWidget(vcard)

        # ── text ──────────────────────────────────────────────────────
        tcard = theme.make_card()
        tly = QVBoxLayout(tcard)
        tly.setContentsMargins(24, 20, 24, 20)
        tly.setSpacing(12)
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(t("studio.text.placeholder"))
        self._text.setMinimumHeight(150)
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.BG_CARD}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_DEFAULT}; border-radius: {theme.RADIUS_CONTROL}px;"
            f" padding: 10px 12px; font-size: 14px; }}"
            f"QPlainTextEdit:focus {{ border: 1px solid {theme.INK}; }}")
        self._text.textChanged.connect(self._on_text)
        tly.addWidget(self._text)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self._count = _muted(size=12)
        opts.addWidget(self._count, 1)
        self._lang_lbl = _muted(t("studio.lang.label"))
        opts.addWidget(self._lang_lbl)
        self._lang = QComboBox()
        theme.style_combo(self._lang)
        self._lang.setMinimumWidth(150)
        self._lang.currentIndexChanged.connect(self._on_lang_changed)
        opts.addWidget(self._lang)
        self._speed_lbl = _muted(t("studio.speed.label"))
        opts.addWidget(self._speed_lbl)
        self._speed = QComboBox()
        theme.style_combo(self._speed)
        for label, val in SPEEDS:
            self._speed.addItem(f"{label}×", val)
        saved_speed = float(self._pref("studio_speed", 1.0) or 1.0)
        self._speed.setCurrentIndex(next((i for i, (_l, v) in enumerate(SPEEDS) if abs(v - saved_speed) < 1e-6), 2))
        self._speed.currentIndexChanged.connect(self._on_speed_changed)
        opts.addWidget(self._speed)
        tly.addLayout(opts)

        act = QHBoxLayout()
        act.setSpacing(10)
        self._go = theme.accent_button(t("studio.speak.go"), 40)
        self._go.clicked.connect(self._generate)
        act.addWidget(self._go)
        self._cancel = theme.make_button(t("common.cancel"), "secondary", 40)
        self._cancel.clicked.connect(self._on_cancel)
        self._cancel.setVisible(False)
        act.addWidget(self._cancel)
        self._status = _muted()
        act.addWidget(self._status, 1)
        tly.addLayout(act)
        self._bar = ThinProgress(4)
        self._bar.setVisible(False)
        tly.addWidget(self._bar)
        root.addWidget(tcard)

        # ── result ────────────────────────────────────────────────────
        self._result_card = theme.make_card()
        rly = QVBoxLayout(self._result_card)
        rly.setContentsMargins(24, 20, 24, 18)
        rly.setSpacing(10)
        self._player = PlayerBar()
        self._player.save_requested.connect(self._save)
        self._player.started.connect(self._stop_preview)
        rly.addWidget(self._player)
        self._result_info = _muted(size=12)
        rly.addWidget(self._result_info)
        self._warn = QLabel()
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px; background: transparent;")
        self._warn.setVisible(False)
        rly.addWidget(self._warn)
        self._result_card.setVisible(False)
        root.addWidget(self._result_card)
        root.addStretch()

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_UNLOAD_MS)
        self._idle.timeout.connect(self._unload_idle)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._on_preview_tick)

        self._rebuild_languages(self._pref("studio_language", "auto") or "auto")
        self._rebuild_voices()
        self._on_text()
        self._refresh()

    # ── public ────────────────────────────────────────────────────────
    def set_engine(self, asr_engine) -> None:
        self._asr = asr_engine

    def refresh(self) -> None:
        self._rebuild_voices()
        self._refresh()

    def busy(self) -> bool:
        return self._synth is not None or self._dl is not None

    def stop_playback(self) -> None:
        self._player.shutdown()
        self._stop_preview()

    def shutdown(self) -> None:
        """App is quitting: stop audio and let any running job wind down."""
        self._player.shutdown()
        self._stop_preview()
        for w in (self._synth, self._dl):
            if w is not None:
                w.cancel()
                w.wait(8000)

    def retranslate(self) -> None:
        self._cap_builtin.setText(t("studio.voices.builtin"))
        self._cap_mine.setText(t("studio.voices.mine"))
        self._manage_btn.setText(t("studio.voices.manage"))
        self._mine_hint.setText(t("studio.voices.mine_hint"))
        self._cap_engine.setText(t("studio.engine.pick"))
        self._no_clone.setText(t("studio.voices.no_clone"))
        self._text.setPlaceholderText(t("studio.text.placeholder"))
        self._lang_lbl.setText(t("studio.lang.label"))
        self._speed_lbl.setText(t("studio.speed.label"))
        self._go.setText(t("studio.speak.go"))
        self._cancel.setText(t("common.cancel"))
        self._dl_cancel.setText(t("common.cancel"))
        self._player.retranslate()
        self._rebuild_languages()
        self._rebuild_voices()
        self._on_text()
        self._refresh()

    # ── voices ────────────────────────────────────────────────────────
    def _rebuild_languages(self, initial: Optional[str] = None) -> None:
        cur = initial or self._lang.currentData() or "auto"
        self._lang.blockSignals(True)
        self._lang.clear()
        for code in tts.LANGUAGES:
            self._lang.addItem(t(f"studio.lang.{code}"), code)
        self._lang.setCurrentIndex(max(0, self._lang.findData(cur)))
        self._lang.blockSignals(False)

    @staticmethod
    def _clear_flow(host: QWidget, flow: FlowLayout) -> None:
        while flow.count():
            it = flow.takeAt(0)
            w = it.widget()
            if w:
                w.hide()                       # gone now, not when the deferred delete runs
                w.setParent(None)
                w.deleteLater()

    def _rebuild_voices(self) -> None:
        self._stop_preview()
        self._clear_flow(self._builtin_host, self._builtin_flow)
        self._clear_flow(self._mine_host, self._mine_flow)
        self._chips.clear()
        zh = i18n.LANG == "zh"
        b = speech.backend(self._engine_id)
        self._engine_tag.setText(t(f"studio.engine.tag.{b.info.id}"))
        for v in b.voices():
            chip = VoiceChip(v.name, _voice_tag(v, zh), previewable=preview_path(v.id).is_file())
            chip.clicked.connect(lambda _=False, vid=v.id: self._select(vid))
            chip.preview.connect(lambda vid=v.id: self._toggle_preview(vid))
            self._builtin_flow.addWidget(chip)
            self._chips[v.id] = chip
        clone = b.info.supports_clone
        mine = self._lib.list() if clone else []
        for v in mine:
            chip = VoiceChip(v.name, t("studio.voices.mine_sub").format(sec=f"{v.duration:.0f}"), previewable=True)
            chip.clicked.connect(lambda _=False, vid=MY_PREFIX + v.id: self._select(vid))
            chip.preview.connect(lambda vid=MY_PREFIX + v.id: self._toggle_preview(vid))
            self._mine_flow.addWidget(chip)
            self._chips[MY_PREFIX + v.id] = chip
        if clone:
            add = VoiceChip(t("studio.voices.clone"), dashed=True, icon="plus")
            add.clicked.connect(self._clone_new)
            self._mine_flow.addWidget(add)
        for w in (self._cap_mine, self._mine_host, self._mine_rule):
            w.setVisible(clone)
        self._mine_hint.setVisible(clone and not mine)
        self._manage_btn.setVisible(bool(mine))
        self._no_clone.setVisible(not clone)
        if self._voice_id not in self._chips:
            self._voice_id = self._default_for(b)
        self._mark_selected()

    # ── voice previews ────────────────────────────────────────────────
    def _preview_audio(self, vid: str):
        """A built-in voice's shipped clip, or a saved voice's own recording."""
        if vid.startswith(MY_PREFIX):
            v = next((v for v in self._lib.list() if v.id == vid[len(MY_PREFIX):]), None)
            try:
                return audio_io.read_wav(str(v.wav_path)) if v else None
            except Exception:
                return None
        return load_preview(vid)

    def _toggle_preview(self, vid: str) -> None:
        playing = self._preview_vid == vid and self._preview.is_playing
        self._stop_preview()
        if playing:
            return
        clip = self._preview_audio(vid)
        if clip is None:
            return
        self._player.pause()
        self._preview.load(*clip)
        if not self._preview.play():
            self.toast.emit(t("studio.play_failed"), "warn")
            return
        self._preview_vid = vid
        if vid in self._chips:
            self._chips[vid].set_playing(True)
        self._preview_timer.start()

    def _stop_preview(self) -> None:
        self._preview_timer.stop()
        self._preview.stop()
        if self._preview_vid in self._chips:
            self._chips[self._preview_vid].set_playing(False)
        self._preview_vid = None

    def _on_preview_tick(self) -> None:
        if not self._preview.poll():
            self._stop_preview()

    def _mark_selected(self) -> None:
        for vid, chip in self._chips.items():
            chip.setChecked(vid == self._voice_id)

    def _select(self, vid: str) -> None:
        self._voice_id = vid
        self._save_pref("studio_voice", vid)
        self._mark_selected()
        self._refresh()

    # ── remembered choices ────────────────────────────────────────────
    def _pref(self, key: str, default):
        get = getattr(self._settings, "get", None)
        try:
            return get(key) if callable(get) else default
        except Exception:
            return default

    def _save_pref(self, key: str, value) -> None:
        setter = getattr(self._settings, "set", None)
        if callable(setter):
            try:
                setter(key, value)
            except OSError:
                pass

    def _on_lang_changed(self, _i: int) -> None:
        self._save_pref("studio_language", self._lang.currentData() or "auto")
        self._on_text()

    def _on_speed_changed(self, _i: int) -> None:
        self._save_pref("studio_speed", float(self._speed.currentData() or 1.0))
        self._on_text()

    def _saved_voice(self) -> Optional[SavedVoice]:
        if self._voice_id.startswith(MY_PREFIX):
            return self._lib.get(self._voice_id[len(MY_PREFIX):])
        return None

    def _clone_new(self) -> None:
        mic = self._settings.microphone if self._settings is not None else "auto"
        dlg = CloneDialog(self.window(), self._asr, self._lib, mic)
        dlg.exec()
        if dlg.saved is not None:
            self._voice_id = MY_PREFIX + dlg.saved.id
            self._save_pref("studio_voice", self._voice_id)
            self._rebuild_voices()
            self._refresh()
            self.toast.emit(t("studio.voices.saved").format(name=dlg.saved.name), "success")

    def _manage_menu(self) -> None:
        v = self._saved_voice()
        if v is None:
            self.toast.emit(t("studio.voices.pick_mine"), "info")
            return
        menu = QMenu(self)
        menu.addAction(t("studio.voices.rename"), self._rename_voice)
        menu.addAction(t("studio.voices.edit_text"), self._edit_text)
        menu.addAction(t("studio.voices.delete"), self._delete_voice)
        menu.exec(self._manage_btn.mapToGlobal(self._manage_btn.rect().bottomLeft()))

    def _rename_voice(self) -> None:
        v = self._saved_voice()
        if v is None:
            return
        name, ok = QInputDialog.getText(self, t("studio.voices.rename"), t("studio.voices.name"), text=v.name)
        if ok and name.strip():
            self._lib.rename(v.id, name)
            self._rebuild_voices()

    def _edit_text(self) -> None:
        v = self._saved_voice()
        if v is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, t("studio.voices.edit_text"), t("studio.clone.said"),
                                                 v.ref_text)
        if ok and text.strip():
            self._lib.update_text(v.id, text)

    def _delete_voice(self) -> None:
        v = self._saved_voice()
        if v is None:
            return
        from thundertalk.ui.styled_dialog import StyledDialog
        if StyledDialog.confirm(self.window(), title=t("studio.voices.delete_title").format(name=v.name),
                                body=t("studio.voices.delete_body"), accept_label=t("studio.voices.delete"),
                                cancel_label=t("common.cancel"), destructive=True):
            self._lib.delete(v.id)
            self._voice_id = default_voice()
            self._save_pref("studio_voice", "")
            self._rebuild_voices()
            self._refresh()

    # ── engine readiness ──────────────────────────────────────────────
    def _needed_backend(self):
        """The speech engine that will speak with the selected voice."""
        if self._voice_id.startswith(MY_PREFIX):
            return speech.backend(self._clone_backend())
        return speech.backend(speech.backend_id_for(self._voice_id))

    def _clone_backend(self) -> str:
        """My voices are spoken by the selected engine when it can clone."""
        b = speech.backend(self._engine_id)
        return b.info.id if b.info.supports_clone else speech.DEFAULT_CLONE_BACKEND

    def _initial_engine(self) -> str:
        saved = self._pref("studio_engine", "")
        if saved in speech.BACKEND_ORDER:
            self._engine_id = saved
        elif self._voice_id.startswith(MY_PREFIX):
            self._engine_id = speech.DEFAULT_CLONE_BACKEND
        else:
            try:
                self._engine_id = speech.backend_id_for(self._voice_id)
            except ValueError:
                self._engine_id = speech.BACKEND_ORDER[0]
        return self._engine_id

    @staticmethod
    def _default_for(b) -> str:
        zh = i18n.LANG == "zh"
        vs = b.voices()
        want = "chinese" if zh else "english"
        return next((v.id for v in vs if v.language == want), vs[0].id if vs else default_voice())

    def _on_engine(self, bid: str) -> None:
        self._engine_id = bid
        self._save_pref("studio_engine", bid)
        keep_mine = self._voice_id.startswith(MY_PREFIX) and speech.backend(bid).info.supports_clone
        if not keep_mine:
            self._voice_id = self._default_for(speech.backend(bid))
            self._save_pref("studio_voice", self._voice_id)
        self._rebuild_voices()
        self._refresh()

    def _engine_ready(self) -> bool:
        return self._needed_backend().is_ready()

    def _refresh(self) -> None:
        b = self._needed_backend()
        ready = b.is_ready()
        downloading = self._dl is not None
        zh = i18n.LANG == "zh"
        size = b.info.size_mb
        size_txt = f"{size / 1000:.1f} GB" if size >= 1000 else f"{size} MB"
        self._engine_card.setVisible(not ready or downloading)
        self._engine_title.setText(t("studio.engine.title_named").format(name=b.info.name))
        self._engine_body.setText((b.info.blurb_zh if zh else b.info.blurb_en) + "  "
                                  + t("studio.engine.size").format(size=size_txt))
        self._dl_btn.setText(t("studio.engine.download_named").format(name=b.info.name, size=size_txt))
        self._dl_btn.setVisible(not downloading)
        self._dl_cancel.setVisible(downloading)
        self._dl_bar.setVisible(downloading)
        busy = self.busy()
        self._go.setEnabled(ready and not busy and bool(self._text.toPlainText().strip()))
        for chip in self._chips.values():
            chip.setEnabled(not busy)
        self._text.setReadOnly(self._synth is not None)

    def _download(self) -> None:
        if self._dl is not None:
            return
        w = BackendDownloadWorker(self._needed_backend().info)
        self._dl = w
        w.progress.connect(self._on_dl_progress)
        w.done.connect(lambda _r: self.toast.emit(t("studio.engine.ready"), "success"))
        w.error.connect(lambda c: self.toast.emit(friendly_error(c), "error"))
        w.finished.connect(self._on_dl_finished)
        self._dl_bar.set_indeterminate(True)
        self._dl_status.setText(t("studio.engine.connecting"))
        self._refresh()
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

    # ── text ──────────────────────────────────────────────────────────
    def _language(self) -> str:
        code = self._lang.currentData() or "auto"
        return tts.detect_language(self._text.toPlainText()) if code == "auto" else code

    def _on_text(self) -> None:
        text = self._text.toPlainText()
        n = len(text.strip())
        if n:
            secs = tts.expected_seconds(text, self._language()) / float(self._speed.currentData() or 1.0)
            self._count.setText(t("studio.text.count").format(n=n, est=fmt_seconds(secs)))
        else:
            self._count.setText("")
        self._go.setEnabled(self._engine_ready() and not self.busy() and n > 0)

    # ── generate ──────────────────────────────────────────────────────
    def _voice_ref(self):
        if self._voice_id.startswith(MY_PREFIX):
            v = self._saved_voice()
            if v is None:
                return None
            if not v.ref_text.strip():
                self.toast.emit(t("studio.voices.need_text"), "warn")
                return None
            return self._lib.prompt(v.id)
        return self._voice_id

    def _generate(self) -> None:
        if self.busy():
            return
        self._stop_preview()
        text = self._text.toPlainText().strip()
        if not text:
            return
        ref = self._voice_ref()
        if ref is None:
            return
        self._player.shutdown()
        self._synth = SynthWorker(text, ref, self._lang.currentData() or "auto",
                                  float(self._speed.currentData() or 1.0), self._asr,
                                  clone_backend=self._clone_backend())
        w = self._synth
        w.step.connect(self._on_step)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.cancelled.connect(lambda: self._status.setText(t("studio.cancelled")))
        w.finished.connect(self._on_finished)
        self._t0 = time.monotonic()
        self._phase = t("studio.speak.loading")
        self._go.setVisible(False)
        self._cancel.setVisible(True)
        self._cancel.setEnabled(True)
        self._cancel.setText(t("common.cancel"))
        self._bar.setVisible(True)
        self._bar.set_indeterminate(True)
        self._result_card.setVisible(False)
        self._idle.stop()
        self._tick.start()
        self._on_tick()
        self._refresh()
        w.start()

    def _on_step(self, i: int, n: int) -> None:
        self._bar.set_value(100.0 * i / max(1, n))
        self._phase = t("studio.speak.piece").format(i=min(i + 1, n), n=n)

    def _on_tick(self) -> None:
        el = time.monotonic() - self._t0
        self._status.setText(f"{self._phase}   {int(el // 60)}:{int(el % 60):02d}")

    def _on_cancel(self) -> None:
        if self._synth is not None:
            self._synth.cancel()
            self._cancel.setEnabled(False)
            self._cancel.setText(t("studio.cancelling"))

    def _on_done(self, res: tts.SynthResult) -> None:
        self._result = res
        self._player.set_audio(res.audio, res.sample_rate)
        v = self._saved_voice()
        name = v.name if v else next((p.name for p in speech.all_voices() if p.id == self._voice_id), "")
        self._result_info.setText(t("studio.speak.done").format(
            dur=fmt_seconds(res.duration), took=fmt_seconds(res.seconds_taken), voice=name))
        warns = res.warnings
        self._warn.setText(t("studio.speak.warn").format(snippet=warns[0]) if warns else "")
        self._warn.setVisible(bool(warns))
        self._result_card.setVisible(True)
        self._status.setText("")

    def _on_error(self, code: str) -> None:
        self._status.setText("")
        self.toast.emit(friendly_error(code), "error")

    def _on_finished(self) -> None:
        self._tick.stop()
        self._synth = None
        self._bar.setVisible(False)
        self._bar.set_indeterminate(False)
        self._cancel.setVisible(False)
        self._go.setVisible(True)
        self._idle.start()
        self._refresh()

    def _unload_idle(self) -> None:
        """Free the ~3–4 GB the voice model holds once it's been idle for a while."""
        if self._synth is None:
            speech.get_engine().unload()

    # ── save ──────────────────────────────────────────────────────────
    def _save(self, fmt: str) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, t("studio.save"), str(Path.home() / f"speech.{fmt}"), f"*.{fmt}")
        if not path:
            return
        if not path.lower().endswith(f".{fmt}"):
            path += f".{fmt}"
        try:
            audio_io.export_audio(path, self._result.audio, self._result.sample_rate)
            self.toast.emit(t("studio.saved").format(name=Path(path).name), "success")
        except Exception as exc:
            self.toast.emit(t("studio.err.other").format(msg=exc), "error")
