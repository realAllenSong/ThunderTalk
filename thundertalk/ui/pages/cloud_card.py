"""Models page ▸ Cloud APIs: bring-your-own-key speech engines (core/cloud.py).

One row per provider: key (masked; saved to the Keychain), base URL for an
OpenAI-compatible server, Test, the speech-to-text model with "Use for
dictation", the text-to-speech model + default voice for Studio ▸ Speak, and
this month's usage. Models and voices are picked from the provider's list (an
OpenAI-compatible server's list comes from its /models on Test), never typed.
Nothing here reaches the network except Test, ElevenLabs' voice list and the
spend/quota lookup (cloud_usage.billing, every 10 minutes while visible)."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from thundertalk.core import cloud, cloud_usage
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


def _model_combo(pid: str, kind: str, settings, current: str, width: int = 280) -> QComboBox:
    """A pick-only list of the provider's models; the item data is the model id."""
    c = QComboBox()
    c.setFixedHeight(34)
    c.setFixedWidth(width)
    theme.style_combo(c)
    _fill_models(c, pid, kind, settings, current)
    return c


def _fill_models(c: QComboBox, pid: str, kind: str, settings, current: str) -> None:
    choices = cloud.model_choices(pid, kind, settings)
    c.blockSignals(True)
    c.clear()
    for i, m in enumerate(choices):
        label = m
        date = cloud.RETIRING.get(f"{pid}:{m}")
        if date:
            label += "  ·  " + t("cloud.retiring").format(date=date[:7])
        elif i == 0 and len(choices) > 1 and pid != "custom":
            label += "  ·  " + t("cloud.recommended")
        c.addItem(label, m)
    # An OpenAI-compatible server that has not listed its models yet keeps a
    # free-text field (its model names are unknown until Test).
    free = pid == "custom" and not ((settings.get("cloud") or {}).get(pid) or {}).get("models")
    c.setEditable(free)
    if free:
        c.lineEdit().setPlaceholderText(t("cloud.no_models"))
    i = c.findData(current)
    if i >= 0:
        c.setCurrentIndex(i)
    elif free and current:
        c.setEditText(current)
    c.blockSignals(False)


def _combo_value(c: QComboBox) -> str:
    if c.isEditable():
        return c.currentText().strip()
    return str(c.currentData() or "").strip()


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

        if pid == "openai":
            admin_row = QHBoxLayout()
            admin_row.setSpacing(8)
            admin_row.addWidget(_caption(t("cloud.admin_key")))
            self._admin = _edit(password=True)
            self._admin.returnPressed.connect(self._on_save_admin)
            admin_row.addWidget(self._admin, stretch=1)
            self._admin_save = theme.make_button(t("cloud.save"), "ghost", height=34, font_px=12)
            self._admin_save.clicked.connect(self._on_save_admin)
            admin_row.addWidget(self._admin_save)
            self._admin_remove = theme.make_button(t("cloud.remove"), "ghost", height=34, font_px=12)
            self._admin_remove.clicked.connect(self._on_remove_admin)
            admin_row.addWidget(self._admin_remove)
            self._admin_row = admin_row
        else:
            self._admin = None
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
        if self._admin is not None:
            ly.addLayout(self._admin_row)

        self._asr = None
        if p.asr_models:
            asr_row = QHBoxLayout()
            asr_row.setSpacing(8)
            asr_row.addWidget(_caption(t("cloud.asr")))
            self._asr = _model_combo(pid, "asr", settings, cfg["asr_model"])
            self._asr.currentIndexChanged.connect(lambda _i: self._on_asr_model(_combo_value(self._asr)))
            if self._asr.isEditable():
                self._asr.lineEdit().editingFinished.connect(lambda: self._on_asr_model(_combo_value(self._asr)))
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
            self._tts = _model_combo(pid, "tts", settings, cfg["tts_model"])
            self._tts.currentIndexChanged.connect(lambda _i: self._save_cfg(tts_model=_combo_value(self._tts)))
            if self._tts.isEditable():
                self._tts.lineEdit().editingFinished.connect(
                    lambda: self._save_cfg(tts_model=_combo_value(self._tts)))
            tts_row.addWidget(self._tts)
            if pid == "custom":
                # A self-hosted server's voice names are its own: typed once.
                self._voice = _combo([], cfg["voice"] or "alloy", width=170)
                self._voice.lineEdit().setPlaceholderText(t("cloud.voice"))
                self._voice.lineEdit().editingFinished.connect(
                    lambda: self._save_cfg(voice=self._voice.currentText()))
            else:
                self._voice = QComboBox()
                self._voice.setFixedHeight(34)
                self._voice.setFixedWidth(200)
                theme.style_combo(self._voice)
                self._fill_voices()
                self._voice.currentIndexChanged.connect(
                    lambda _i: self._save_cfg(voice=str(self._voice.currentData() or "")))
            tts_row.addWidget(self._voice)
            tts_row.addStretch()
            ly.addLayout(tts_row)

        self._usage = QLabel("")
        self._usage.setWordWrap(True)
        self._usage.setStyleSheet(_MUTED)
        ly.addWidget(self._usage)
        self._billing: Optional[dict] = None
        self._billing_err = ""
        self._billing_job: Optional[_Job] = None
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
            value = _combo_value(self._asr)
            current = f"{self.pid}:{value}"
            self._use.setEnabled(ready and bool(value) and current != self._active_id)
            self._use.setText(t("cloud.in_use") if current == self._active_id else t("cloud.use_dictation"))
        if self._admin is not None:
            admin = cloud.key_source("openai-admin")
            self._admin.setPlaceholderText(t("cloud.admin_saved_hint") if admin == "keychain" else t("cloud.admin_hint"))
            self._admin_remove.setVisible(admin == "keychain")
        self._show_usage()

    # ── usage ──
    def _show_usage(self) -> None:
        u = cloud_usage.provider_summary(self.pid)
        parts = []
        if u["requests"]:
            parts.append(t("cloud.usage").format(min=u["minutes"]))
            if u["chars"]:
                parts.append(t("cloud.usage_chars").format(n=u["chars"]))
            if u["usd"] is not None and u["usd"] > 0:
                parts.append(t("cloud.usage_usd").format(usd=f"{u['usd']:.2f}" if u["usd"] >= 0.01 else f"{u['usd']:.4f}"))
            if u["unpriced"]:
                parts.append(t("cloud.usage_unpriced"))
        elif cloud.is_configured(self.pid, self._settings):
            parts.append(t("cloud.usage_none"))
        b = self._billing
        if b and b.get("kind") == "spend":
            parts.append(t("cloud.spend").format(usd=f"{b['month_usd']:.2f}"))
        elif b and b.get("kind") == "quota":
            parts.append(t("cloud.quota").format(tier=b.get("tier") or "", used=f"{b['used']:,}",
                                                 limit=f"{b['limit']:,}"))
        if self._billing_err:
            parts.append(t("cloud.billing_failed").format(err=self._billing_err[:120]))
        self._usage.setText(" · ".join(parts))
        self._usage.setVisible(bool(parts))

    def refresh_billing(self, force: bool = False) -> None:
        """Ask the provider for spend/quota on a worker (only OpenAI with an
        admin key and ElevenLabs have an API for it)."""
        if self._billing_job is not None:
            return
        if not ((self.pid == "openai" and cloud.get_key("openai-admin"))
                or (self.pid == "elevenlabs" and cloud.is_configured("elevenlabs", self._settings))):
            if self._billing is not None or self._billing_err:
                self._billing, self._billing_err = None, ""
                self._show_usage()
            return
        job = _Job(lambda: cloud_usage.billing(self.pid, refresh=force, settings=self._settings))

        def done(r) -> None:
            self._billing, self._billing_err = r, ""

        def failed(msg: str) -> None:
            self._billing_err = msg

        job.done.connect(done)
        job.failed.connect(failed)
        job.finished.connect(self._billing_finished)
        self._billing_job = job
        job.start()

    def _billing_finished(self) -> None:
        self._billing_job = None
        self._show_usage()

    def _fill_voices(self) -> None:
        if self._voice is None or self._voice.isEditable():
            return
        chosen = cloud.config(self.pid, self._settings)["voice"]
        rows = cloud.voices(self.pid, settings=self._settings) if cloud.is_configured(self.pid, self._settings) \
            or cloud.PROVIDERS[self.pid].voices else []
        self._voice.blockSignals(True)
        self._voice.clear()
        for r in rows:
            self._voice.addItem(r["name"], r["id"].split(":", 1)[1])
        if chosen and self._voice.findData(chosen) < 0:
            self._voice.insertItem(0, chosen, chosen)
        i = self._voice.findData(chosen)
        self._voice.setCurrentIndex(i if i >= 0 else 0)
        self._voice.blockSignals(False)

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

    def _on_save_admin(self) -> None:
        key = self._admin.text().strip() if self._admin is not None else ""
        if not key:
            return
        try:
            cloud.set_key("openai-admin", key)
        except cloud.CloudError as exc:
            self._status.setText(str(exc))
            return
        finally:
            self._admin.clear()
        self._status.setText(t("cloud.saved"))
        self.refresh()
        self.refresh_billing(force=True)

    def _on_remove_admin(self) -> None:
        cloud.remove_key("openai-admin")
        self._status.setText(t("cloud.removed"))
        self.refresh()
        self.refresh_billing()

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
        job.done.connect(self._test_done)
        job.failed.connect(lambda msg: self._status.setText(t("cloud.test_failed").format(err=msg[:200])))
        job.finished.connect(self._job_finished)
        self._job = job
        job.start()

    def _test_done(self, r: dict) -> None:
        if "models" in r:
            self._status.setText(t("cloud.test_models").format(s=r.get("seconds", 0), n=r["models"]))
            cfg = cloud.config(self.pid, self._settings)
            if self._asr is not None:
                _fill_models(self._asr, self.pid, "asr", self._settings, cfg["asr_model"])
            if self._tts is not None:
                _fill_models(self._tts, self.pid, "tts", self._settings, cfg["tts_model"])
        else:
            self._status.setText(t("cloud.test_ok").format(s=r.get("seconds", 0)))
        self._fill_voices()

    def _job_finished(self) -> None:
        self._job = None
        self.refresh()

    def _on_use(self) -> None:
        model = _combo_value(self._asr) if self._asr is not None else ""
        if model:
            self.use_for_dictation.emit(f"{self.pid}:{model}")

    def wait(self, ms: int = 5000) -> None:
        for job in (self._job, self._billing_job):
            if job is not None:
                job.wait(ms)


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
        self._timer = QTimer(self)
        self._timer.setInterval(int(cloud_usage.BILLING_TTL * 1000))
        self._timer.timeout.connect(self._tick)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._tick()
        self._timer.start()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()

    def _tick(self) -> None:
        for row in self.rows.values():
            row.refresh()
            row.refresh_billing()

    def refresh(self, active_id: Optional[str] = None) -> None:
        for row in self.rows.values():
            row.refresh(active_id if active_id is None else (active_id or ""))

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
