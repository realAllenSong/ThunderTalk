"""AI proofreading — one switch, providers found automatically, one chosen model.

Detection and test requests run on worker threads. While the page is visible,
CLIs are re-probed every few seconds and local servers more often, so logging
in or starting an app shows up without a refresh button.
"""
from __future__ import annotations

import re
import subprocess
import threading

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core import llm_providers as lp
from thundertalk.core.i18n import bus, t
from thundertalk.ui import theme
from thundertalk.ui.widgets import FormCard, PageHeader, column_scroll

_OTHER = "__other__"
_BADGE = {"ready": "green", "login": "amber", "unverified": "blue", "checking": "blue",
          "needs_key": "amber", "no_models": "amber", "not_running": "muted",
          "not_installed": "muted", "unsupported": "red", "unavailable": "red"}
_MUTED = f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;"


def error_reason(error) -> str:
    if isinstance(error, lp.ProviderError) and error.reason == "exit":
        status = re.search(r"status (-?\d+)", str(error))
        if status:
            return t("cleanup.error.exit_status").format(status=status[1])
    if isinstance(error, lp.ProviderError) and error.reason == "version":
        return t("cleanup.error.version").format(version=str(error).split(": ")[-1].split(" ")[0])
    if isinstance(error, lp.ProviderError):
        return t("cleanup.error." + error.reason) if error.reason in (
            "auth", "rate_limit", "quota", "model", "flags", "network", "timeout", "empty", "exit", "request") else str(error)
    return t("cleanup.error.request")


class _Worker(QThread):
    """Runs ``fn(cancel_event)`` once; emits its result or the exception."""

    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.fn(self.cancel)
        except Exception as exc:  # reported to the page, never raised in Qt
            result = exc
        self.done.emit(result)


class _RadioDot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(18, 18)
        self.checked = False
        self.active = True

    def set_state(self, checked: bool, active: bool) -> None:
        self.checked, self.active = checked, active
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QColor(theme.INK) if self.checked else (
            theme._BORDER_STRONG_C if self.active else theme._BORDER_DEFAULT_C)
        p.setPen(QPen(ring, 1.5))
        p.setBrush(QColor(theme.BG_CARD))
        p.drawEllipse(QRectF(1.5, 1.5, 15, 15))
        if self.checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.INK))
            p.drawEllipse(QRectF(5.5, 5.5, 7, 7))
        p.end()


class ProviderRow(theme.Card):
    """One provider: choose dot, name, status, what to do next."""

    chosen = Signal(str)
    action = Signal(str, str)  # provider id, action

    def __init__(self, provider: lp.Provider) -> None:
        super().__init__(radius=8, hoverable=True)
        self.provider = provider
        self.kind = ""
        self.selectable = False
        ly = QVBoxLayout(self)
        ly.setContentsMargins(16, 12, 16, 12)
        ly.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.dot = _RadioDot()
        top.addWidget(self.dot, alignment=Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.name = QLabel(provider.display_name)
        self.name.setFont(theme.font(13, bold=True))
        self.name.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        head.addWidget(self.name)
        self.status = theme.badge("", "muted")
        head.addWidget(self.status)
        head.addStretch()
        text.addLayout(head)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(_MUTED)
        text.addWidget(self.hint)
        top.addLayout(text, 1)
        self.button = theme.make_button("", "secondary", 32, font_px=12)
        self.button.clicked.connect(lambda: self.action.emit(self.provider.id, self.kind))
        top.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignVCenter)
        ly.addLayout(top)

        self.panel = QWidget()
        self.panel.setStyleSheet("background: transparent;")
        pl = QHBoxLayout(self.panel)
        pl.setContentsMargins(30, 0, 0, 0)
        pl.setSpacing(10)
        self.panel_label = QLabel("")
        self.panel_label.setStyleSheet(_MUTED)
        pl.addWidget(self.panel_label)
        self.command = QLabel("")
        self.command.setFont(theme.font_mono(12))
        self.command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.command.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: {theme.BG_ELEVATED};"
            f" border-radius: {theme.RADIUS_CONTROL}px; padding: 5px 9px;")
        pl.addWidget(self.command, 1)
        self.copy = theme.make_button("", "ghost", 28, font_px=12)
        self.copy.clicked.connect(self._copy)
        pl.addWidget(self.copy)
        ly.addWidget(self.panel)
        self.panel.hide()

    def show_state(self, provider: lp.Provider, status: str, hint: str, kind: str,
                   command: str, selected: bool, panel_open: bool) -> None:
        self.provider = provider
        self.kind = kind
        self.selectable = provider.is_ready()
        self.name.setText(provider.display_name)
        self.status.setText(t("cleanup.status." + status))
        self.status.set_colors(*theme.PASTELS[_BADGE.get(status, "muted")])
        self.hint.setText(hint)
        self.dot.set_state(selected, self.selectable)
        labels = {"verify": t("cleanup.action.verify"), "login": t("cleanup.action.login"),
                  "install": t("cleanup.action.install"), "key": t("cleanup.action.key"),
                  "open": t("cleanup.action.open").format(app=provider.app_name)}
        self.button.setText(labels.get(kind, ""))
        self.button.setVisible(kind in labels)
        self.button.setEnabled(status != "checking")
        self.command.setText(command)
        self.panel_label.setText(t("cleanup.command_hint"))
        if self.copy.text() != t("cleanup.action.copied"):
            self.copy.setText(t("cleanup.action.copy"))
        self.panel.setVisible(bool(command) and panel_open)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.selectable
                       else Qt.CursorShape.ArrowCursor)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.command.text())
        self.copy.setText(t("cleanup.action.copied"))
        QTimer.singleShot(1500, self.copy, lambda: self.copy.setText(t("cleanup.action.copy")))

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.selectable:
            self.chosen.emit(self.provider.id)
        super().mousePressEvent(ev)


class ProofreadPage(QWidget):
    providers_changed = Signal(list)
    selection_changed = Signal()

    CLI_INTERVAL_MS = 15000
    SERVER_INTERVAL_MS = 3000

    def __init__(self, settings, detect_clis=None, detect_servers=None, check_model=None) -> None:
        super().__init__()
        self.settings = settings
        self._detect_clis_fn = detect_clis or lp.detect_clis
        self._detect_servers_fn = detect_servers or lp.detect_servers
        self._check_fn = check_model or lp.check_model
        self._clis: list[lp.Provider] = []
        self._servers: list[lp.Provider] = []
        self._detected_once = False
        self._cli_worker: _Worker | None = None
        self._server_worker: _Worker | None = None
        self._check_worker: _Worker | None = None
        self._checking: tuple[str, str, str] | None = None  # provider, model, purpose
        self._verify_failed: set[str] = set()
        self._model_failed: set[tuple[str, str]] = set()
        self._errors: dict[tuple[str, str], Exception] = {}
        self._open_panels: set[str] = set()
        self._rows: dict[str, ProviderRow] = {}
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, col = column_scroll(spacing=16)
        root.addWidget(scroll)
        self._header = PageHeader(t("cleanup.title"), t("cleanup.subtitle"))
        col.addWidget(self._header)

        self._switch_card = FormCard()
        self.toggle = theme.ToggleSwitch(bool(settings.get("llm_rewrite_enabled")))
        self.toggle.toggled_signal.connect(lambda value: settings.set("llm_rewrite_enabled", value))
        self._switch_card.add_row("cleanup.enable", "cleanup.description", self.toggle)
        self.privacy = QLabel("")
        self.privacy.setWordWrap(True)
        self.privacy.setStyleSheet(_MUTED)
        self._switch_card.add_widget(theme.separator())
        self._switch_card.add_widget(self.privacy)
        col.addWidget(self._switch_card)

        self._provider_card = FormCard("cleanup.providers")
        self._providers_hint = QLabel("")
        self._providers_hint.setWordWrap(True)
        self._providers_hint.setStyleSheet(_MUTED)
        self._provider_card.add_widget(self._providers_hint)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px; background: transparent;")
        self._provider_card.add_widget(self.summary)
        self._rows_host = QWidget()
        self._rows_host.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        self._provider_card.add_widget(self._rows_host)
        col.addWidget(self._provider_card)

        self._model_card = FormCard()
        self.model_combo = QComboBox()
        self.model_combo.setFixedWidth(300)
        self.model_combo.setFixedHeight(38)
        theme.style_combo(self.model_combo)
        self.model_combo.currentIndexChanged.connect(self._model_chosen)
        self._model_card.add_row("cleanup.model", "cleanup.model_hint", self.model_combo)
        self.model_search = self._edit("")
        self.model_search.textChanged.connect(self._sync_models)
        self._model_card.add_row("cleanup.model_search", "cleanup.model_search_hint", self.model_search)
        self.effort_combo = QComboBox()
        self.effort_combo.setFixedWidth(300)
        self.effort_combo.setFixedHeight(38)
        theme.style_combo(self.effort_combo)
        self.effort_combo.currentIndexChanged.connect(self._effort_chosen)
        self._model_card.add_row("cleanup.effort", "cleanup.effort_hint", self.effort_combo)
        self._other_row = QWidget()
        self._other_row.setStyleSheet("background: transparent;")
        ol = QHBoxLayout(self._other_row)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(10)
        ol.addStretch()
        self.other_edit = QLineEdit()
        self.other_edit.setFixedWidth(300)
        self.other_edit.setFixedHeight(38)
        self.other_edit.setStyleSheet(theme.INPUT_QSS)
        self.other_edit.returnPressed.connect(self._check_other)
        ol.addWidget(self.other_edit)
        self.other_button = theme.make_button("", "secondary", 38)
        self.other_button.clicked.connect(self._check_other)
        ol.addWidget(self.other_button)
        self._other_row.hide()
        self._model_card.add_widget(self._other_row)
        self.model_id = QLabel("")
        self.model_id.setWordWrap(True)
        self.model_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.model_id.setStyleSheet(_MUTED)
        self._model_card.add_widget(self.model_id)
        self.model_status = QLabel("")
        self.model_status.setWordWrap(True)
        self.model_status.setStyleSheet(_MUTED)
        self._model_card.add_widget(self.model_status)
        col.addWidget(self._model_card)

        self._custom_card = FormCard("cleanup.custom")
        self.url = self._edit(settings.get("cleanup_base_url"))
        self.url.setPlaceholderText("http://127.0.0.1:8000/v1")
        self.url.editingFinished.connect(self._custom_changed)
        self._custom_card.add_row("cleanup.url", "cleanup.url_hint", self.url)
        self.key = self._edit(settings.get("cleanup_api_key"))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.editingFinished.connect(self._custom_changed)
        self._custom_card.add_row("cleanup.key", "cleanup.key_hint", self.key, sep=True)
        col.addWidget(self._custom_card)
        col.addStretch()

        self._cli_timer = QTimer(self)
        self._cli_timer.setInterval(self.CLI_INTERVAL_MS)
        self._cli_timer.timeout.connect(self.detect_clis)
        self._server_timer = QTimer(self)
        self._server_timer.setInterval(self.SERVER_INTERVAL_MS)
        self._server_timer.timeout.connect(self.detect_servers)

        bus.language_changed.connect(self.retranslate)
        self.retranslate()

    @staticmethod
    def _edit(value) -> QLineEdit:
        edit = QLineEdit(str(value or ""))
        edit.setFixedWidth(300)
        edit.setFixedHeight(38)
        edit.setStyleSheet(theme.INPUT_QSS)
        return edit

    # ── detection ───────────────────────────────────────────────────

    @property
    def providers(self) -> list[lp.Provider]:
        return self._clis + self._servers

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._cli_timer.start()
        self._server_timer.start()
        self.refresh()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._cli_timer.stop()
        self._server_timer.stop()

    def refresh_for_startup(self) -> None:
        """First dictation needs no provider CLIs when proofreading is off."""
        if self.settings.get("llm_rewrite_enabled"):
            self.refresh()

    def refresh(self) -> None:
        self.detect_clis()
        self.detect_servers()

    def _verified(self) -> set[str]:
        return {pid for pid, models in (self.settings.get("cleanup_checks") or {}).items() if models}

    def detect_clis(self) -> None:
        if self._cli_worker is not None:
            return
        verified = self._verified()
        self._cli_worker = self._start(lambda _c: self._detect_clis_fn(verified),
                                       self._clis_detected, "_cli_worker")

    def detect_servers(self) -> None:
        if self._server_worker is not None:
            return
        options = dict(custom_base_url=self.settings.get("cleanup_base_url") or "",
                       api_key=self.settings.get("cleanup_api_key") or "",
                       cherry_api_key=self.settings.get("cleanup_cherry_key") or "")
        self._server_worker = self._start(lambda _c: self._detect_servers_fn(**options),
                                          self._servers_detected, "_server_worker")

    def _start(self, fn, on_done, slot: str) -> _Worker:
        worker = _Worker(fn)
        worker.done.connect(on_done)

        def finished():
            if getattr(self, slot) is worker:
                setattr(self, slot, None)
            worker.deleteLater()
            if slot == "_check_worker":
                self._auto_check()
        worker.finished.connect(finished)
        worker.start()
        return worker

    def _clis_detected(self, result) -> None:
        if isinstance(result, list):
            self._clis = result
            self._detected_once = True
            self._apply()

    def _servers_detected(self, result) -> None:
        if isinstance(result, list):
            self._servers = result
            self._apply()

    def shutdown(self) -> None:
        # Every probe and test request is bounded; never destroy a running QThread.
        self._cli_timer.stop()
        self._server_timer.stop()
        for worker in (self._cli_worker, self._server_worker, self._check_worker):
            if worker is not None:
                worker.cancel.set()
                worker.wait()

    # ── selection ───────────────────────────────────────────────────

    def chosen_provider(self) -> lp.Provider | None:
        selected = self.settings.get("cleanup_provider") or ""
        return next((p for p in self.providers if p.id == selected and p.is_ready()), None)

    def _models(self, provider: lp.Provider) -> list[str]:
        extra = (self.settings.get("cleanup_extra_models") or {}).get(provider.id) or []
        return provider.models + [m for m in extra if m not in provider.models]

    def chosen_model(self, provider: lp.Provider) -> str:
        saved = (self.settings.get("cleanup_models") or {}).get(provider.id)
        if provider.id == "claude" and saved in ("haiku", "sonnet", "opus"):
            saved = next((m for m in provider.models if m.startswith("claude-" + saved + "-")), saved)
        if saved and (saved in self._models(provider) or saved in provider.model_groups()):
            return saved
        model = lp.preferred_model(provider)
        return lp.split_cursor_model(model)[0] if provider.id == "cursor" else model

    def chosen_effort(self, provider: lp.Provider, model: str | None = None) -> str:
        model = model or self.chosen_model(provider)
        saved = (self.settings.get("cleanup_efforts") or {}).get(provider.id)
        return saved if saved in provider.effort_levels(model) else provider.default_effort(model)

    def _sync_efforts(self) -> None:
        provider = self.chosen_provider()
        model = self.model_combo.currentData()
        levels = provider.effort_levels(model) if provider and model and model != _OTHER else []
        self.effort_combo.blockSignals(True)
        self.effort_combo.clear()
        for effort in levels:
            self.effort_combo.addItem(t("cleanup.effort." + effort), effort)
        if levels:
            self.effort_combo.setCurrentIndex(max(0, self.effort_combo.findData(self.chosen_effort(provider, model))))
        else:
            self.effort_combo.addItem(t("cleanup.effort.unsupported"), "")
        self.effort_combo.setEnabled(bool(levels))
        self.effort_combo.blockSignals(False)

    def _effort_chosen(self, _index: int) -> None:
        provider = self.chosen_provider()
        if provider is None:
            return
        efforts = dict(self.settings.get("cleanup_efforts") or {})
        efforts[provider.id] = self.effort_combo.currentData()
        self.settings.set("cleanup_efforts", efforts)
        self._update_model_status()
        self._auto_check()
        self.selection_changed.emit()

    def _choose(self, pid: str) -> None:
        if pid == self.settings.get("cleanup_provider"):
            return
        self.settings.set("cleanup_provider", pid)
        self._syncing = True
        self.model_combo.setCurrentIndex(-1)
        self._syncing = False
        self._other_row.hide()
        self.model_search.clear()
        self._apply()
        self.selection_changed.emit()

    def _apply(self) -> None:
        """Re-render rows and the model list from the latest detection."""
        providers = self.providers
        if not self.settings.get("cleanup_provider"):
            first = next((p for p in providers if p.is_ready()), None)
            if first is not None:
                self.settings.set("cleanup_provider", first.id)
        selected = self.settings.get("cleanup_provider")
        for row in self._rows.values():
            self._rows_layout.removeWidget(row)
        seen = {}
        for p in providers:
            row = self._rows.get(p.id)
            if row is None:
                row = ProviderRow(p)
                row.chosen.connect(self._choose)
                row.action.connect(self._action)
                self._rows[p.id] = row
            status, hint, kind, command = self._describe(p)
            row.show_state(p, status, hint, kind, command, p.id == selected,
                           p.id in self._open_panels or p.id in self._verify_failed)
            self._rows_layout.addWidget(row)
            row.show()
            seen[p.id] = row
        for pid in [pid for pid in self._rows if pid not in seen]:
            self._rows.pop(pid).deleteLater()
        self._rows = seen
        if not self._detected_once:
            self.summary.setText(t("cleanup.detecting"))
        elif not any(p.is_ready() for p in providers):
            self.summary.setText(t("cleanup.none"))
        else:
            self.summary.setText("")
        self.summary.setVisible(bool(self.summary.text()))
        self._sync_models()
        self.providers_changed.emit(providers)

    def _describe(self, p: lp.Provider) -> tuple[str, str, str, str]:
        """(status, hint, action, command) shown for one provider."""
        n = len(p.models)
        if self._checking and self._checking[0] == p.id and self._checking[2] == "verify":
            return "checking", t("cleanup.hint.unverified"), "verify", ""
        if p.status == "ready":
            if p.kind == "cli":
                return "ready", t("cleanup.hint.ready_cli").format(n=n), "", ""
            if p.id == "custom":
                return "ready", t("cleanup.hint.ready_custom").format(url=p.base_url, n=n), "", ""
            return "ready", t("cleanup.hint.ready_server").format(port=p.port, n=n), "", ""
        if p.status == "unverified":
            if p.id in self._verify_failed:
                error = self._errors.get((p.id, self._check_key(p, self.chosen_model(p))))
                status = "login" if getattr(error, "reason", "") == "auth" else "unverified"
                hint = t("cleanup.error").format(reason=error_reason(error))
                return status, hint, "verify", p.login_command if status == "login" else ""
            return "unverified", t("cleanup.hint.unverified"), "verify", ""
        if p.status == "login":
            return "login", t("cleanup.hint.login"), "login", p.login_command
        if p.status == "not_installed":
            if p.kind == "cli":
                return ("not_installed", t("cleanup.hint.install_cli"),
                        "install" if p.install_command else "", p.install_command)
            return ("not_installed", t("cleanup.hint.not_installed_app").format(
                app=p.app_name, port=p.port), "", "")
        if p.status == "not_running":
            if p.id == "custom":
                return "not_running", t("cleanup.hint.not_running.custom").format(url=p.base_url), "", ""
            key = "cleanup.hint.not_running.ollama" if p.id == "ollama" else "cleanup.hint.not_running"
            has_app = lp._app_installed((p.app_name,), "")
            return ("not_running", t(key).format(app=p.app_name, port=p.port),
                    "open" if has_app else "", "")
        if p.status == "needs_key":
            return "needs_key", t("cleanup.hint.needs_key"), "key" if p.id == "cherry" else "", ""
        if p.status == "no_models":
            return "no_models", t("cleanup.hint.no_models").format(app=p.app_name or p.display_name), "", ""
        if p.status == "unsupported":
            return "unsupported", t("cleanup.hint.unsupported"), "", ""
        return "unavailable", t("cleanup.hint.unavailable"), "", ""

    def _action(self, pid: str, kind: str) -> None:
        provider = next((p for p in self.providers if p.id == pid), None)
        if provider is None:
            return
        if kind == "verify":
            self._start_check(provider, self.chosen_model(provider), "verify")
        elif kind in ("login", "install"):
            self._open_panels ^= {pid}
            self._apply()
        elif kind == "open":
            try:
                subprocess.Popen(["open", "-a", provider.app_name])
            except OSError:
                pass
        elif kind == "key":
            key, ok = QInputDialog.getText(self, provider.display_name,
                                           t("cleanup.key_prompt").format(app=provider.display_name),
                                           QLineEdit.EchoMode.Password)
            if ok:
                self.settings.set("cleanup_cherry_key", key.strip())
                self.detect_servers()

    def _custom_changed(self) -> None:
        url, key = self.url.text().strip(), self.key.text().strip()
        if (url, key) == (self.settings.get("cleanup_base_url"), self.settings.get("cleanup_api_key")):
            return
        self.settings.set("cleanup_base_url", url)
        self.settings.set("cleanup_api_key", key)
        self.detect_servers()

    # ── model ───────────────────────────────────────────────────────

    def _sync_models(self, *_args) -> None:
        provider = self.chosen_provider()
        models = self._models(provider) if provider else []
        if provider and provider.id == "cursor":
            models = list(dict.fromkeys(lp.split_cursor_model(m)[0] if m in provider.models else m for m in models))
        query = self.model_search.text().casefold().strip()
        models = [m for m in models if query in lp.model_label(m).casefold()]
        selected = self.chosen_model(provider) if provider else ""
        if provider and provider.id == "cursor":
            selected = lp.split_cursor_model(selected)[0] if selected in provider.models else selected
        items = [*models, _OTHER] if provider is not None else []
        current = [self.model_combo.itemData(i) for i in range(self.model_combo.count())]
        if items != current:  # periodic detection must not close an open popup
            self._syncing = True
            self.model_combo.clear()
            for model in items[:-1]:
                self.model_combo.addItem(lp.model_label(model), model)
                self.model_combo.setItemData(self.model_combo.count() - 1, model, Qt.ItemDataRole.ToolTipRole)
            if provider is not None:
                self.model_combo.addItem(t("cleanup.model_other"), _OTHER)
                self.model_combo.setCurrentIndex(
                    self.model_combo.findData(selected))
            else:
                self._other_row.hide()
            self._syncing = False
        elif provider is not None:
            self._syncing = True
            if self.model_combo.currentData() != _OTHER:
                self.model_combo.setCurrentIndex(self.model_combo.findData(selected))
            self._syncing = False
            self.model_combo.setItemText(len(items) - 1, t("cleanup.model_other"))
        self.model_combo.setEnabled(provider is not None)
        self._sync_efforts()
        self._update_model_status()
        if not query:
            self._auto_check()

    def _model_chosen(self, _index: int) -> None:
        if self._syncing:
            return
        provider = self.chosen_provider()
        model = self.model_combo.currentData()
        if provider is None or not model:
            return
        if model == _OTHER:
            self._other_row.show()
            self.other_edit.setFocus()
            return
        self._other_row.hide()
        models = dict(self.settings.get("cleanup_models") or {})
        models[provider.id] = model
        self.settings.set("cleanup_models", models)
        self._sync_efforts()
        self._update_model_status()
        self._auto_check()
        self.selection_changed.emit()

    def _check_key(self, provider: lp.Provider, model: str, effort=None) -> str:
        effort = self.chosen_effort(provider, model) if effort is None else effort
        return f"{model}@{effort}" if effort else model

    def _checked(self, pid: str, model: str):
        provider = next(p for p in self.providers if p.id == pid)
        return ((self.settings.get("cleanup_checks") or {}).get(pid) or {}).get(self._check_key(provider, model))

    def _auto_check(self) -> None:
        """Curated IDs are only suggestions: prove the selected one works."""
        provider = self.chosen_provider()
        if provider is None or provider.models_source != "curated" or not self.isVisible():
            return
        model = self.chosen_model(provider)
        if (self._checked(provider.id, model) is None
                and (provider.id, self._check_key(provider, model)) not in self._model_failed):
            self._start_check(provider, model, "model")

    def _check_other(self) -> None:
        provider = self.chosen_provider()
        model = self.other_edit.text().strip()
        if provider is not None and model:
            self._start_check(provider, model, "other")

    def _start_check(self, provider: lp.Provider, model: str, purpose: str) -> None:
        if self._check_worker is not None or not model:
            return
        self._checking = (provider.id, model, purpose)
        effort = self.chosen_effort(provider, model)
        self._check_worker = self._start(
            lambda cancel: self._check_fn(provider, model, 60, cancel,
                                           effort=effort or None),
            lambda result: self._check_done(provider, model, purpose, result, effort), "_check_worker")
        if purpose == "verify":
            self._apply()
        else:
            self._update_model_status()

    def _check_done(self, provider: lp.Provider, model: str, purpose: str, result, effort="") -> None:
        key = self._check_key(provider, model, effort)
        self._checking = None
        if isinstance(result, (int, float)) and not isinstance(result, bool):
            checks = dict(self.settings.get("cleanup_checks") or {})
            checks[provider.id] = {**(checks.get(provider.id) or {}), key: round(float(result), 2)}
            self.settings.set("cleanup_checks", checks)
            self._verify_failed.discard(provider.id)
            self._model_failed.discard((provider.id, key))
            self._errors.pop((provider.id, key), None)
            if provider.status == "unverified":
                provider.status, provider.ready = "ready", True
            if purpose == "other":
                extra = dict(self.settings.get("cleanup_extra_models") or {})
                extra[provider.id] = [*(extra.get(provider.id) or []), model]
                self.settings.set("cleanup_extra_models", extra)
                models = dict(self.settings.get("cleanup_models") or {})
                models[provider.id] = model
                self.settings.set("cleanup_models", models)
                self.other_edit.clear()
                self._other_row.hide()
                self.selection_changed.emit()
        elif not isinstance(result, lp.CompletionCancelled):
            checks = dict(self.settings.get("cleanup_checks") or {})
            checked = dict(checks.get(provider.id) or {})
            if key in checked:
                checked.pop(key)
                checks[provider.id] = checked
                self.settings.set("cleanup_checks", checks)
            if purpose == "verify":
                self._verify_failed.add(provider.id)
            self._model_failed.add((provider.id, key))
            self._errors[(provider.id, key)] = result
        self._apply()

    def _update_model_status(self) -> None:
        provider = self.chosen_provider()
        if provider is None:
            self.model_id.clear()
            self.model_status.setText(t("cleanup.model_none"))
            return
        model = self.model_combo.currentData()
        if model == _OTHER:
            model = self.other_edit.text().strip()
        model = model or self.chosen_model(provider)
        key = self._check_key(provider, model)
        resolved = provider.resolve_model(model, self.chosen_effort(provider, model) or None)
        self.model_id.setText(t("cleanup.model_id").format(model=resolved))
        if self._checking and self._checking[0] == provider.id and self._checking[2] != "verify":
            self.model_status.setText(t("cleanup.check.running").format(model=self._checking[1]))
            return
        seconds = self._checked(provider.id, model)
        if seconds is not None:
            self.model_status.setText(t("cleanup.check.ok").format(model=model, s=seconds))
        elif (provider.id, key) in self._model_failed:
            self.model_status.setText(t("cleanup.check.failed").format(model=model) + " " + t("cleanup.error").format(reason=error_reason(self._errors.get((provider.id, key)))))
        else:
            self.model_status.setText(t("cleanup.models." + provider.models_source).format(
                name=provider.display_name))

    # ── i18n ────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._header.set_title(t("cleanup.title"))
        self._header.set_subtitle(t("cleanup.subtitle"))
        for card in (self._switch_card, self._provider_card, self._model_card, self._custom_card):
            card.retranslate()
        self.privacy.setText(t("cleanup.privacy"))
        self._providers_hint.setText(t("cleanup.providers_hint"))
        self.model_search.setPlaceholderText(t("cleanup.model_search"))
        self.other_edit.setPlaceholderText(t("cleanup.model_other_placeholder"))
        self.other_button.setText(t("cleanup.model_check"))
        self._apply()
