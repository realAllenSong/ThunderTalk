"""Paper & Ink controls for existing AI providers; detection never blocks Qt."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QVBoxLayout, QWidget

from thundertalk.core.ai_cleanup import STYLES
from thundertalk.core.i18n import bus, t
from thundertalk.core.llm_providers import detect
from thundertalk.ui import theme
from thundertalk.ui.widgets import FormCard


class DetectionWorker(QThread):
    done = Signal(list)

    def __init__(self, options):
        super().__init__()
        self.options = options

    def run(self):
        self.done.emit(detect(**self.options))


class CleanupSettings(QWidget):
    providers_changed = Signal(list)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.providers = []
        self._worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.card = FormCard("cleanup.title")
        layout.addWidget(self.card)
        self.toggle = theme.ToggleSwitch(bool(settings.get("llm_rewrite_enabled")))
        self.toggle.toggled_signal.connect(lambda value: settings.set("llm_rewrite_enabled", value))
        self.card.add_row("cleanup.enable", "cleanup.description", self.toggle)
        self.provider_combo = self._combo()
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.card.add_row("cleanup.provider", "cleanup.provider_hint", self.provider_combo, sep=True)
        self.model_combo = self._combo(editable=True)
        self.model_combo.currentTextChanged.connect(self._model_changed)
        self.card.add_row("cleanup.model", "cleanup.model_hint", self.model_combo, sep=True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.status)
        self.refresh_button = theme.make_button("", "secondary", 34)
        self.refresh_button.clicked.connect(self.refresh)
        self.card.add_row("cleanup.scan", "cleanup.scan_hint", self.refresh_button, sep=True)
        self.url = self._edit(settings.get("cleanup_base_url"))
        self.url.setPlaceholderText("http://127.0.0.1:1234/v1")
        self.url.editingFinished.connect(lambda: settings.set("cleanup_base_url", self.url.text().strip()))
        self.card.add_row("cleanup.url", "cleanup.url_hint", self.url, sep=True)
        self.key = self._edit(settings.get("cleanup_api_key"))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.editingFinished.connect(lambda: settings.set("cleanup_api_key", self.key.text()))
        self.card.add_row("cleanup.key", "cleanup.key_hint", self.key, sep=True)
        self.cherry_key = self._edit(settings.get("cleanup_cherry_key"))
        self.cherry_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cherry_key.editingFinished.connect(lambda: settings.set("cleanup_cherry_key", self.cherry_key.text()))
        self.card.add_row("cleanup.cherry_key", "cleanup.cherry_hint", self.cherry_key, sep=True)
        self.app_combo = self._combo(editable=True)
        self.app_combo.addItems(sorted((settings.get("cleanup_app_overrides") or {}).keys()))
        self.app_combo.currentTextChanged.connect(self._app_changed)
        self.card.add_row("cleanup.app", "cleanup.app_hint", self.app_combo, sep=True)
        self.style_combo = self._combo()
        for style in STYLES:
            self.style_combo.addItem(t("cleanup.style." + style), style)
        self.card.add_row("cleanup.style", "cleanup.style_hint", self.style_combo, sep=True)
        self.save_button = theme.make_button("", "secondary", 34)
        self.save_button.clicked.connect(self._save_override)
        self.card.add_row("cleanup.override", "cleanup.override_hint", self.save_button, sep=True)
        self.commands = theme.ToggleSwitch(bool(settings.get("voice_commands_enabled")))
        self.commands.toggled_signal.connect(lambda value: settings.set("voice_commands_enabled", value))
        self.card.add_row("cleanup.commands", "cleanup.commands_hint", self.commands, sep=True)
        self.privacy = QLabel()
        self.privacy.setWordWrap(True)
        self.privacy.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.privacy)
        bus.language_changed.connect(self.retranslate)
        self._app_changed(self.app_combo.currentText())
        self.retranslate()

    @staticmethod
    def _combo(editable=False):
        combo = QComboBox()
        combo.setEditable(editable)
        combo.setFixedWidth(270)
        combo.setFixedHeight(38)
        combo.setStyleSheet(theme.COMBO_QSS)
        return combo

    @staticmethod
    def _edit(value):
        edit = QLineEdit(str(value or ""))
        edit.setFixedWidth(270)
        edit.setFixedHeight(38)
        edit.setStyleSheet(theme.INPUT_QSS)
        return edit

    def refresh(self):
        if self._worker is not None:
            return
        self.status.setText(t("cleanup.detecting"))
        self.refresh_button.setEnabled(False)
        options = dict(custom_base_url=self.settings.get("cleanup_base_url") or "",
                       api_key=self.settings.get("cleanup_api_key") or "",
                       cherry_api_key=self.settings.get("cleanup_cherry_key") or "",
                       model_overrides=self.settings.get("cleanup_model_overrides") or {})
        worker = DetectionWorker(options)
        self._worker = worker
        worker.done.connect(self._detected)
        worker.finished.connect(self._finished)
        worker.start()

    def _finished(self):
        self._worker.deleteLater()
        self._worker = None
        self.refresh_button.setEnabled(True)

    def shutdown(self):
        # Every probe is bounded; prevent destroying a running QThread on exit.
        if self._worker is not None:
            self._worker.wait()

    def _detected(self, providers):
        self.providers = providers
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItem(t("cleanup.auto"), "")
        for provider in providers:
            self.provider_combo.addItem(provider.display_name + " · " + t("cleanup.status." + provider.status), provider.id)
        selected = self.settings.get("cleanup_provider") or ""
        idx = self.provider_combo.findData(selected)
        if idx < 0 and selected:
            self.provider_combo.addItem(selected + " · " + t("cleanup.status.unavailable"), selected)
            idx = self.provider_combo.count() - 1
        self.provider_combo.setCurrentIndex(max(0, idx))
        self.provider_combo.blockSignals(False)
        self._sync_models()
        self.providers_changed.emit(providers)

    def chosen_provider(self):
        selected = self.settings.get("cleanup_provider")
        if selected:
            return next((p for p in self.providers if p.id == selected and p.is_ready()), None)
        return next((p for p in self.providers if p.is_ready()), None)

    def chosen_model(self, provider):
        chosen = (self.settings.get("cleanup_models") or {}).get(provider.id)
        if chosen:
            return chosen
        if provider.id == "cursor" and "gpt-5.4-mini-none" in provider.models:
            return "gpt-5.4-mini-none"
        return provider.models[0]

    def _provider_changed(self, _index):
        self.settings.set("cleanup_provider", self.provider_combo.currentData() or "")
        self._sync_models()

    def _sync_models(self):
        provider = self.chosen_provider()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if provider:
            self.model_combo.addItems(provider.models)
            self.model_combo.setCurrentText(self.chosen_model(provider))
        self.model_combo.blockSignals(False)
        self.toggle.setEnabled(provider is not None)
        self.model_combo.setEnabled(provider is not None)
        if provider:
            self.status.setText(provider.display_name + " · " + t("cleanup.status." + provider.status) +
                                " · " + t("cleanup.models." + provider.models_source))
        else:
            self.status.setText(t("cleanup.none"))

    def _model_changed(self, model):
        provider = self.chosen_provider()
        if provider and model.strip():
            models = dict(self.settings.get("cleanup_models") or {})
            models[provider.id] = model.strip()
            self.settings.set("cleanup_models", models)

    def _app_changed(self, app):
        if not hasattr(self, "style_combo"):
            return
        styles = self.settings.get("cleanup_app_overrides") or {}
        self.style_combo.setCurrentIndex(max(0, self.style_combo.findData(styles.get(app, "auto"))))

    def _save_override(self):
        app = self.app_combo.currentText().strip()
        if not app:
            return
        overrides = {key: value for key, value in (self.settings.get("cleanup_app_overrides") or {}).items()
                     if key.casefold() != app.casefold()}
        if self.style_combo.currentData() == "auto":
            overrides.pop(app, None)
        else:
            overrides[app] = self.style_combo.currentData()
        self.settings.set("cleanup_app_overrides", overrides)
        if self.app_combo.findText(app) < 0:
            self.app_combo.addItem(app)

    def retranslate(self):
        self.card.retranslate()
        self.refresh_button.setText(t("cleanup.refresh"))
        self.save_button.setText(t("cleanup.save"))
        self.privacy.setText(t("cleanup.privacy"))
        for i, style in enumerate(STYLES):
            self.style_combo.setItemText(i, t("cleanup.style." + style))
        if self.providers:
            self._detected(self.providers)
        else:
            self.status.setText(t("cleanup.none"))
