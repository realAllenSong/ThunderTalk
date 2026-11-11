"""Models page ▸ Cloud APIs: bring-your-own-key speech engines (core/cloud.py).

One row per provider: key (masked; saved to the Keychain), base URL for an
OpenAI-compatible server, Test, the speech-to-text model with "Use for
dictation", and the voice model + default voice for Studio ▸ Speak. Nothing
here reaches the network except Test (and ElevenLabs' voice list)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from thundertalk.core import cloud
from thundertalk.core.i18n import t
from thundertalk.ui import theme

_MUTED = f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent; border: none;"


class _Job(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except cloud.CloudError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:              # noqa: BLE001
            self.failed.emit(type(exc).__name__)


def _chip(text: str, kind: str, upper: bool = False):
    c = theme.badge(text, kind, upper=upper)
    c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return c


def _edit(width: int = 0, password: bool = False) -> QLineEdit:
    e = QLineEdit()
    e.setFixedHeight(34)
    if width:
        e.setFixedWidth(width)
    e.setStyleSheet(theme.INPUT_QSS)
    if password:
        e.setEchoMode(QLineEdit.EchoMode.Password)
    return e


def _combo(items: list[str], current: str, width: int = 220) -> QComboBox:
    c = QComboBox()
    c.setEditable(True)                       # model names are free text
    c.addItems(items)
    if current and current not in items:
        c.insertItem(0, current)
    c.setCurrentText(current or (items[0] if items else ""))
    c.setFixedHeight(34)
    c.setFixedWidth(width)
    theme.style_combo(c)
    return c


def _caption(text: str, width: int = 120) -> QLabel:
    lbl = QLabel(text)
    lbl.setFixedWidth(width)
    lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; background: transparent;")
    return lbl


class _Status(QLabel):
    """A muted message line that takes no space while empty."""

    def __init__(self) -> None:
        super().__init__("")
        self.setWordWrap(True)
        self.setStyleSheet(_MUTED)
        self.hide()

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setVisible(bool(text))


class ProviderRow(QWidget):
    use_for_dictation = Signal(str)            # model id "<provider>:<model>"
    changed = Signal()

    def __init__(self, pid: str, settings) -> None:
        super().__init__()
        self.pid = pid
        self._settings = settings
        self._job: Optional[_Job] = None
        self._active_id = ""
        p = cloud.PROVIDERS[pid]
        cfg = cloud.config(pid, settings)
        ly = QVBoxLayout(self)
        ly.setContentsMargins(0, 6, 0, 6)
        ly.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        name = QLabel(p.name)
        name.setFont(theme.font(14, bold=True))
        name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        head.addWidget(name)
        self._chip_box = QHBoxLayout()
        self._chip_box.setSpacing(6)
        head.addLayout(self._chip_box)
        head.addStretch()
        self._test = theme.make_button(t("cloud.test"), "ghost", height=30, font_px=12)
        self._test.clicked.connect(self._on_test)
        head.addWidget(self._test)
        ly.addLayout(head)

        if pid == "custom":
            url_row = QHBoxLayout()
            url_row.addWidget(_caption(t("cloud.base_url")))
            self._url = _edit()
            self._url.setPlaceholderText("http://127.0.0.1:8000/v1")
            self._url.setText(cfg["base_url"])
            self._url.editingFinished.connect(self._on_url)
            url_row.addWidget(self._url, stretch=1)
            ly.addLayout(url_row)
        else:
            self._url = None

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(_caption(t("cloud.key")))
        self._key = _edit(password=True)
        self._key.returnPressed.connect(self._on_save_key)
        key_row.addWidget(self._key, stretch=1)
        self._save = theme.make_button(t("cloud.save"), "secondary", height=34, font_px=12)
        self._save.clicked.connect(self._on_save_key)
        key_row.addWidget(self._save)
        self._remove = theme.make_button(t("cloud.remove"), "ghost", height=34, font_px=12)
        self._remove.clicked.connect(self._on_remove_key)
        key_row.addWidget(self._remove)
        ly.addLayout(key_row)

        self._asr = None
        if p.asr_models:
            asr_row = QHBoxLayout()
            asr_row.setSpacing(8)
            asr_row.addWidget(_caption(t("cloud.asr")))
            self._asr = _combo(list(p.asr_models) if pid != "custom" else [], cfg["asr_model"])
            self._asr.currentTextChanged.connect(self._on_asr_model)
            asr_row.addWidget(self._asr)
            self._use = theme.make_button(t("cloud.use_dictation"), "secondary", height=34, font_px=12)
            self._use.clicked.connect(self._on_use)
            asr_row.addWidget(self._use)
            asr_row.addStretch()
            ly.addLayout(asr_row)

        self._tts = self._voice = None
        if p.tts_models:
            tts_row = QHBoxLayout()
            tts_row.setSpacing(8)
            tts_row.addWidget(_caption(t("cloud.tts")))
            self._tts = _combo(list(p.tts_models) if pid != "custom" else [], cfg["tts_model"])
            self._tts.currentTextChanged.connect(lambda v: self._save_cfg(tts_model=v))
            tts_row.addWidget(self._tts)
            names = list(p.voices)
            self._voice = _combo(names, cfg["voice"] or (names[0] if names else ""), width=170)
            self._voice.lineEdit().setPlaceholderText(t("cloud.voice"))
            self._voice.currentTextChanged.connect(lambda v: self._save_cfg(voice=v))
            tts_row.addWidget(self._voice)
            tts_row.addStretch()
            ly.addLayout(tts_row)

        self._status = _Status()
        ly.addWidget(self._status)
        self.refresh()

    # ── state ──
    def refresh(self, active_id: Optional[str] = None) -> None:
        if active_id is not None:
            self._active_id = active_id
        while self._chip_box.count():
            w = self._chip_box.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        src = cloud.key_source(self.pid)
        if src == "keychain":
            self._chip_box.addWidget(_chip(t("cloud.key_saved"), "green"))
        elif src == "env":
            self._chip_box.addWidget(_chip(t("cloud.key_env"), "blue"))
        elif not cloud.PROVIDERS[self.pid].key_required:
            self._chip_box.addWidget(_chip(t("cloud.key_optional"), "muted"))
        else:
            self._chip_box.addWidget(_chip(t("cloud.no_key"), "muted"))
        in_use = bool(self._active_id) and cloud.split_id(self._active_id)[0] == self.pid
        if in_use:
            self._chip_box.addWidget(_chip(t("cloud.in_use"), "orange"))
        self._key.setPlaceholderText(t("cloud.key_saved_hint") if src == "keychain" else t("cloud.key_hint"))
        self._remove.setVisible(src == "keychain")
        ready = cloud.is_configured(self.pid, self._settings)
        self._test.setEnabled(ready)
        if self._asr is not None:
            current = f"{self.pid}:{self._asr.currentText().strip()}"
            self._use.setEnabled(ready and bool(self._asr.currentText().strip()) and current != self._active_id)
            self._use.setText(t("cloud.in_use") if current == self._active_id else t("cloud.use_dictation"))

    def _save_cfg(self, **values) -> None:
        try:
            cloud.save_config(self.pid, self._settings, **values)
        except cloud.CloudError as exc:
            self._status.setText(str(exc))
            return
        self.changed.emit()
        self.refresh()

    def _on_url(self) -> None:
        if self._url is not None:
            self._save_cfg(base_url=self._url.text())

    def _on_asr_model(self, value: str) -> None:
        self._save_cfg(asr_model=value)

    def _on_save_key(self) -> None:
        key = self._key.text().strip()
        if not key:
            return
        try:
            cloud.set_key(self.pid, key)
        except cloud.CloudError as exc:
            self._status.setText(str(exc))
            return
        finally:
            self._key.clear()
        self._status.setText(t("cloud.saved"))
        self.changed.emit()
        self.refresh()

    def _on_remove_key(self) -> None:
        cloud.remove_key(self.pid)
        self._status.setText(t("cloud.removed"))
        self.changed.emit()
        self.refresh()

    def _on_test(self) -> None:
        if self._job is not None:
            return
        self._status.setText(t("cloud.testing"))
        self._test.setEnabled(False)
        job = _Job(lambda: cloud.test(self.pid, self._settings))
        job.done.connect(lambda r: self._status.setText(t("cloud.test_ok").format(s=r.get("seconds", 0))))
        job.failed.connect(lambda msg: self._status.setText(t("cloud.test_failed").format(err=msg[:200])))
        job.finished.connect(self._job_finished)
        self._job = job
        job.start()

    def _job_finished(self) -> None:
        self._job = None
        self.refresh()

    def _on_use(self) -> None:
        model = self._asr.currentText().strip() if self._asr is not None else ""
        if model:
            self.use_for_dictation.emit(f"{self.pid}:{model}")

    def wait(self, ms: int = 5000) -> None:
        if self._job is not None:
            self._job.wait(ms)


class CloudCard(theme.Card):
    """The Cloud APIs section. ``use_for_dictation`` carries a cloud model id
    after the one-time "audio leaves this Mac" confirmation."""

    use_for_dictation = Signal(str)
    changed = Signal()

    def __init__(self, settings) -> None:
        super().__init__(radius=theme.RADIUS_CARD)
        self._settings = settings
        ly = QVBoxLayout(self)
        ly.setContentsMargins(24, 22, 24, 16)
        ly.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self._title = QLabel(t("cloud.title"))
        self._title.setFont(theme.font_serif(20))
        self._title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        top.addWidget(self._title)
        top.addWidget(_chip(t("cloud.optional"), "muted", upper=True))
        top.addStretch()
        ly.addLayout(top)
        self._note = QLabel(t("cloud.note"))
        self._note.setWordWrap(True)
        self._note.setStyleSheet(_MUTED)
        ly.addWidget(self._note)
        self.rows: dict[str, ProviderRow] = {}
        for pid in cloud.PROVIDERS:
            ly.addWidget(theme.separator())
            row = ProviderRow(pid, settings)
            row.use_for_dictation.connect(self._confirm_dictation)
            row.changed.connect(self.changed)
            ly.addWidget(row)
            self.rows[pid] = row

    def refresh(self, active_id: Optional[str]) -> None:
        for row in self.rows.values():
            row.refresh(active_id or "")

    def _confirm_dictation(self, model_id: str) -> None:
        pid, _model = cloud.split_id(model_id)
        if not self._settings.get("cloud_dictation_ok"):
            from thundertalk.ui.styled_dialog import StyledDialog
            ok = StyledDialog.confirm(
                self.window(), title=t("cloud.confirm_title").format(name=cloud.PROVIDERS[pid].name),
                body=t("cloud.confirm_body").format(name=cloud.PROVIDERS[pid].name),
                accept_label=t("cloud.confirm_go"), cancel_label=t("models.cancel"))
            if not ok:
                return
            self._settings.set("cloud_dictation_ok", True)
        self.use_for_dictation.emit(model_id)

    def wait(self) -> None:
        for row in self.rows.values():
            row.wait()
